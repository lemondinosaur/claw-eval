#!/usr/bin/env python3
"""Tag task requirements from prompt text plus task-program structure.

This script assigns three boolean tags to every task:

1. ``needs_real_reference_file``
   True when solving the task depends on one or more concrete input files whose
   exact content matters, such as videos, images, PDFs, attachments, HTML/SQL/DB
   fixtures, or downloadable papers. It is false for tasks that can be solved
   from prompt text plus tool/API responses alone, even if those tools are backed
   by local fixture files.

2. ``needs_network``
   True only when the task requires live/external network access beyond the
   repository's local mock services. ``web_real`` and public URLs count; the
   local mock ``web`` service does not.

3. ``needs_docker``
   True when the current repository execution path requires Docker sandbox mode
   rather than plain host mode or host ``--sandbox-tools`` mode. This mainly
   follows env snapshot usage and sandbox-only media/file access.

The classifier is hybrid:
- hard structural rules from ``task.yaml`` + ``grader.py``
- prompt-text/file-reference heuristics
- optional LLM arbitration for ambiguous cases
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI


api_key = ""
base_url = ""
model = ""


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "task_tags.jsonl"

LIVE_WEB_SERVICE_NAMES = frozenset({"web_real", "web_real_injection"})
MOCK_WEB_SERVICE_NAMES = frozenset({"web"})
WEB_TOOL_NAMES = frozenset({"web_search", "web_fetch", "web_open"})

SANDBOX_TOOL_NAMES = frozenset(
    {
        "Bash",
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "BrowserScreenshot",
        "ReadMedia",
        "Download",
    }
)
REMOTE_ONLY_LOCAL_SANDBOX_TOOLS = frozenset({"ReadMedia", "Download"})
FILE_PROXY_TOOLS = frozenset(
    {
        "ocr_extract_text",
        "caption_describe_image",
        "documents_extract_text",
    }
)

WORKSPACE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])(/workspace(?:/[^\s\"'`),:;]+)?)")
FIXTURE_REF_RE = re.compile(r"fixtures/[^\s\"'`),:;]+")
URL_RE = re.compile(r"https?://[^\s)>\"]+")
FILELIKE_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_\-\u4e00-\u9fff./]+"
    r"\.(?:pdf|mp4|webm|mov|avi|mkv|jpg|jpeg|png|gif|bmp|webp|svg|"
    r"txt|md|csv|tsv|xlsx|xls|doc|docx|ppt|pptx|html|htm|sql|db|sqlite|bin|"
    r"json|yaml|yml|py))(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)

STRONG_REFERENCE_EXTS = frozenset(
    {
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".html",
        ".htm",
        ".sql",
        ".db",
        ".sqlite",
        ".bin",
        ".py",
    }
)
MEDIA_EXTS = frozenset(
    {
        ".mp4",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".pdf",
    }
)

PROMPT_FILE_CUE_PATTERNS = (
    "the container has the following file",
    "container files:",
    "attached",
    "attachment",
    "download and read this pdf",
    "use the pdf parsing tool",
    "extract the text using ocr",
    "read the config file",
    "read the file",
    "scan all the pdf files",
)
PROMPT_FILE_CUE_PATTERNS_ZH = (
    "附件",
    "附图",
    "附上的",
    "文件",
    "图片",
    "图像",
    "视频",
    "字幕",
    "论文",
    "配置文件",
    "读取文件",
    "查看图片",
    "观看视频",
    "跟着学",
)

DOCKER_MEDIA_CUES = (
    "watch the video",
    "watch video",
    "extract frames",
    "video file",
    "video.webm",
    "video.mp4",
    "subtitle",
)
DOCKER_MEDIA_CUES_ZH = (
    "视频",
    "字幕",
    "观看视频",
    "看视频",
    "逐帧",
)


@dataclass
class TaskContext:
    task_path: str
    task_id: str
    task_name: str
    category: str
    prompt_text: str
    reference_solution: str
    tools: list[str]
    services: list[str]
    attachments: list[str]
    sandbox_files: list[str]
    sandbox_grader_files: list[str]
    local_grader_files: list[str]
    env_snapshot_files: list[str]
    env_snapshot_commands: list[str]
    tool_endpoints: list[str]
    raw_task: dict[str, Any]
    grader_text: str
    workspace_paths: list[str] = field(default_factory=list)
    fixture_refs: list[str] = field(default_factory=list)
    file_mentions: list[str] = field(default_factory=list)
    external_urls: list[str] = field(default_factory=list)


@dataclass
class TagDecision:
    value: bool
    reasons: list[str]
    confidence: float


@dataclass
class TaskTags:
    task_path: str
    needs_real_reference_file: bool
    needs_network: bool
    needs_docker: bool
    reasons: dict[str, list[str]] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_prompt_text(task: dict[str, Any]) -> str:
    prompt = task.get("prompt") or {}
    return str(prompt.get("text") or "")


def _extract_attachments(task: dict[str, Any]) -> list[str]:
    prompt = task.get("prompt") or {}
    attachments = prompt.get("attachments") or []
    return [str(item) for item in attachments]


def _extract_tools(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in task.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name"):
            names.append(str(tool["name"]))
    return names


def _extract_services(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for service in task.get("services") or []:
        if isinstance(service, dict) and service.get("name"):
            names.append(str(service["name"]))
    return names


def _extract_tool_endpoints(task: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for endpoint in task.get("tool_endpoints") or []:
        if isinstance(endpoint, dict) and endpoint.get("tool_name"):
            names.append(str(endpoint["tool_name"]))
    return names


def _extract_workspace_paths(text: str) -> list[str]:
    return sorted({match.group(1) for match in WORKSPACE_PATH_RE.finditer(text or "")})


def _extract_fixture_refs(text: str) -> list[str]:
    return sorted({match.group(0) for match in FIXTURE_REF_RE.finditer(text or "")})


def _extract_file_mentions(text: str) -> list[str]:
    return sorted({match.group(1) for match in FILELIKE_MENTION_RE.finditer(text or "")})


def _extract_external_urls(text: str) -> list[str]:
    return sorted({match.group(0) for match in URL_RE.finditer(text or "")})


def _grader_uses_env_snapshot(grader_text: str) -> bool:
    if "env_snapshot" not in grader_text:
        return False
    signals = (
        "file:",
        "cmd:",
        "check_file_exists(",
        "collect_screenshots_from_snapshot",
        "get_snapshot_stdout(",
        "get_snapshot_exit_code(",
        "get_ffprobe_metadata(",
    )
    return any(signal in grader_text for signal in signals)


def _suffix(path_str: str) -> str:
    return Path(path_str).suffix.lower()


def _paths_with_ext(paths: list[str], exts: set[str] | frozenset[str]) -> list[str]:
    return [path for path in paths if _suffix(path) in exts]


def _lowered(text: str) -> str:
    return (text or "").lower()


def build_task_context(task_dir: Path) -> TaskContext:
    task = _load_yaml(task_dir / "task.yaml")
    grader_text = _read_text(task_dir / "grader.py")
    prompt_text = _extract_prompt_text(task)
    return TaskContext(
        task_path=str(task_dir.relative_to(REPO_ROOT)),
        task_id=str(task.get("task_id") or task_dir.name),
        task_name=str(task.get("task_name") or task_dir.name),
        category=str(task.get("category") or ""),
        prompt_text=prompt_text,
        reference_solution=str(task.get("reference_solution") or ""),
        tools=_extract_tools(task),
        services=_extract_services(task),
        attachments=_extract_attachments(task),
        sandbox_files=[str(item) for item in (task.get("sandbox_files") or [])],
        sandbox_grader_files=[str(item) for item in (task.get("sandbox_grader_files") or [])],
        local_grader_files=[str(item) for item in (task.get("local_grader_files") or [])],
        env_snapshot_files=[str(item) for item in (task.get("env_snapshot_files") or [])],
        env_snapshot_commands=[str(item) for item in (task.get("env_snapshot_commands") or [])],
        tool_endpoints=_extract_tool_endpoints(task),
        raw_task=task,
        grader_text=grader_text,
        workspace_paths=_extract_workspace_paths(prompt_text),
        fixture_refs=_extract_fixture_refs(prompt_text),
        file_mentions=_extract_file_mentions(prompt_text),
        external_urls=_extract_external_urls(prompt_text),
    )


def infer_needs_network(ctx: TaskContext) -> TagDecision:
    reasons: list[str] = []
    live_services = sorted(set(ctx.services) & LIVE_WEB_SERVICE_NAMES)
    mock_services = sorted(set(ctx.services) & MOCK_WEB_SERVICE_NAMES)

    if live_services:
        reasons.append(
            f"task declares live web service(s): {', '.join(live_services)}"
        )
        return TagDecision(True, reasons, 1.0)

    if ctx.external_urls:
        reasons.append("prompt contains public URL(s) that must be fetched/downloaded")
        return TagDecision(True, reasons, 0.98)

    if mock_services:
        reasons.append(
            f"task only declares local mock web service(s): {', '.join(mock_services)}"
        )
        return TagDecision(False, reasons, 0.98)

    declared_web_tools = sorted(
        {name for name in ctx.tools + ctx.tool_endpoints if name in WEB_TOOL_NAMES}
    )
    if declared_web_tools:
        reasons.append(
            "task declares web tools but no mock service; treat as external web access"
        )
        return TagDecision(True, reasons, 0.75)

    reasons.append("no live web service or public URL dependency detected")
    return TagDecision(False, reasons, 0.95)


def _prompt_has_file_cues(ctx: TaskContext) -> bool:
    prompt_lower = _lowered(ctx.prompt_text)
    if any(token in prompt_lower for token in PROMPT_FILE_CUE_PATTERNS):
        return True
    if any(token in ctx.prompt_text for token in PROMPT_FILE_CUE_PATTERNS_ZH):
        return True
    if ctx.workspace_paths or ctx.fixture_refs or ctx.file_mentions:
        return True
    return False


def _prompt_has_media_cues(ctx: TaskContext) -> bool:
    prompt_lower = _lowered(ctx.prompt_text)
    if any(token in prompt_lower for token in DOCKER_MEDIA_CUES):
        return True
    if any(token in ctx.prompt_text for token in DOCKER_MEDIA_CUES_ZH):
        return True
    return False


def _has_file_proxy_tool(ctx: TaskContext) -> bool:
    return bool(set(ctx.tools) & FILE_PROXY_TOOLS)


def infer_needs_real_reference_file(ctx: TaskContext) -> TagDecision:
    reasons: list[str] = []
    score = 0
    strong_sandbox_files = _paths_with_ext(ctx.sandbox_files, STRONG_REFERENCE_EXTS)
    media_sandbox_files = _paths_with_ext(ctx.sandbox_files, MEDIA_EXTS)

    if ctx.attachments:
        reasons.append("prompt.attachments explicitly provide input files")
        score += 100

    if ctx.external_urls:
        reasons.append("prompt asks the agent to fetch/download a concrete external file")
        score += 80

    if ctx.workspace_paths or ctx.fixture_refs:
        reasons.append("prompt directly references workspace/fixture files")
        score += 60

    if ctx.file_mentions and ctx.sandbox_files:
        reasons.append("prompt names specific input files that exist in task fixtures")
        score += 45

    if _prompt_has_file_cues(ctx) and ctx.sandbox_files:
        reasons.append("prompt language indicates direct dependence on provided files")
        score += 35

    if strong_sandbox_files and not ctx.services:
        reasons.append("task exposes concrete sandbox files without a service abstraction")
        score += 50

    if media_sandbox_files and _prompt_has_media_cues(ctx):
        reasons.append("task depends on media/document understanding of a provided artifact")
        score += 45

    if strong_sandbox_files and _has_file_proxy_tool(ctx):
        reasons.append("task provides a parser/vision tool over a specific local artifact")
        score += 45

    if (
        ctx.services
        and not ctx.attachments
        and not ctx.external_urls
        and not ctx.workspace_paths
        and not ctx.fixture_refs
        and not ctx.file_mentions
    ):
        reasons.append("task input is delivered through tools/services rather than explicit files")
        score -= 60

    if not reasons:
        reasons.append("no concrete input-file dependency detected")

    value = score >= 50
    confidence = 0.95 if abs(score) >= 60 else 0.7
    return TagDecision(value, reasons, confidence)


def _guess_prompt_needs_sandbox_tools(ctx: TaskContext) -> tuple[bool, list[str]]:
    prompt_text = ctx.prompt_text
    attachments = ctx.attachments
    fixture_refs = ctx.fixture_refs
    workspace_paths = ctx.workspace_paths
    task_tools = ctx.tools
    reasons: list[str] = []

    if attachments:
        return False, reasons

    if any(name in FILE_PROXY_TOOLS for name in task_tools):
        return False, reasons

    lower_prompt = _lowered(prompt_text)
    lower_grader = _lowered(ctx.grader_text)

    needs_remote_only = (
        "readmedia" in lower_prompt
        or "readmedia" in lower_grader
        or any(token in lower_prompt for token in DOCKER_MEDIA_CUES)
        or any(token in prompt_text for token in DOCKER_MEDIA_CUES_ZH)
    )
    if needs_remote_only and (workspace_paths or fixture_refs):
        reasons.append(
            "task likely needs sandbox ReadMedia/Bash access to local video or subtitle files"
        )
        return True, reasons

    if any(path.startswith("/workspace/fixtures/") for path in workspace_paths):
        reasons.append(
            "prompt references /workspace/fixtures files, but plain mode does not expose sandbox file tools"
        )
        return True, reasons

    if fixture_refs:
        reasons.append(
            "prompt references local fixtures without attachments; agent would need file-reading sandbox tools"
        )
        return True, reasons

    return False, reasons


def _inputs_live_only_in_sandbox(ctx: TaskContext) -> tuple[bool, list[str]]:
    if not ctx.sandbox_files:
        return False, []
    if ctx.attachments or ctx.services or ctx.tool_endpoints:
        return False, []
    if any(name in FILE_PROXY_TOOLS for name in ctx.tools):
        return False, []

    prompt_text = ctx.prompt_text
    lower = _lowered(prompt_text)
    input_like_refs = bool(ctx.workspace_paths or ctx.fixture_refs)
    media_like = any(
        token in lower
        for token in (
            "video",
            "subtitle",
            "image",
            "pdf",
            "config file",
            "read the config file",
            "read the file",
            "watch the video",
            "watch video",
            "extract all",
            "locate figure",
        )
    ) or any(
        token in prompt_text
        for token in (
            "视频",
            "字幕",
            "图片",
            "图像",
            "论文",
            "图表",
            "配置文件",
            "读取文件",
            "读这个文件",
            "看这个视频",
            "观看视频",
            "查看图片",
        )
    )

    if input_like_refs or media_like:
        reasons = [
            "task input appears to exist only in sandbox_files, with no attachments or host-side service exposing it"
        ]
        if ctx.workspace_paths:
            reasons.append(
                "prompt references /workspace paths that only make sense inside the sandbox container"
            )
        elif ctx.fixture_refs:
            reasons.append(
                "prompt references local fixture paths, but plain mode provides no file-reading tools"
            )
        return True, reasons

    return False, []


def infer_needs_docker(ctx: TaskContext) -> TagDecision:
    reasons: list[str] = []
    grader_uses_env = _grader_uses_env_snapshot(ctx.grader_text)
    declared_env_snapshot = bool(ctx.env_snapshot_files or ctx.env_snapshot_commands)
    needs_env_snapshot = declared_env_snapshot or bool(ctx.sandbox_grader_files) or grader_uses_env

    if needs_env_snapshot:
        if ctx.env_snapshot_files:
            reasons.append("task.yaml declares env_snapshot_files")
        if ctx.env_snapshot_commands:
            reasons.append("task.yaml declares env_snapshot_commands")
        if ctx.sandbox_grader_files:
            reasons.append("task.yaml declares sandbox_grader_files")
        if grader_uses_env:
            reasons.append("grader reads env_snapshot artifacts")
        return TagDecision(True, reasons, 1.0)

    declared_sandbox_tool_names = [
        name for name in ctx.tools if name in SANDBOX_TOOL_NAMES
    ]
    if any(name in REMOTE_ONLY_LOCAL_SANDBOX_TOOLS for name in declared_sandbox_tool_names):
        reasons.append(
            "declared sandbox tools include ReadMedia/Download, which require remote sandbox container"
        )
        return TagDecision(True, reasons, 1.0)

    needs_sandbox_tools, sandbox_tool_reasons = _guess_prompt_needs_sandbox_tools(ctx)
    if needs_sandbox_tools:
        remote_only_needed = any(
            token in _lowered(ctx.prompt_text)
            for token in ("video.mp4", "video.webm", "watch the video", "watch video", "extract frames")
        ) or "readmedia" in _lowered(ctx.grader_text)
        if remote_only_needed:
            reasons.extend(sandbox_tool_reasons)
            reasons.append("current codebase only provides ReadMedia in Docker sandbox mode")
            return TagDecision(True, reasons, 0.98)
        reasons.extend(sandbox_tool_reasons)
        reasons.append("task needs sandbox file tools, but host sandbox-tools mode is sufficient")
        return TagDecision(False, reasons, 0.9)

    sandbox_only_inputs, sandbox_only_reasons = _inputs_live_only_in_sandbox(ctx)
    if sandbox_only_inputs:
        reasons.extend(sandbox_only_reasons)
        reasons.append("without Docker, sandbox_files are not injected into a host-accessible workspace")
        return TagDecision(True, reasons, 0.95)

    reasons.append("no Docker-only execution dependency detected")
    return TagDecision(False, reasons, 0.95)


class OptionalLLMJudge:
    SYSTEM_PROMPT = """You classify benchmark tasks into three boolean tags.

Definitions:
1. needs_real_reference_file:
   True iff solving the task requires grounding in one or more concrete provided
   or downloadable files whose exact content matters. This includes videos,
   images, PDFs, screenshots, attachments, local HTML/SQL/DB/bin/text/csv files,
   and similar artifacts. False when the task can be solved from prompt text plus
   tool/API responses alone, even if those tools are backed by fixture files.

2. needs_network:
   True iff solving the task requires live/external network access beyond local
   mock services in the repository. Public URLs and web_real count. Local mock
   service named web does not count.

3. needs_docker:
   True iff this repository's current execution path requires Docker sandbox mode
   rather than plain host mode or host sandbox-tools mode. env snapshots,
   sandbox grader files, sandbox-only media access, and /workspace-only delivery
   are strong Docker signals.

Return strict JSON with this shape:
{
  "needs_real_reference_file": true,
  "needs_network": false,
  "needs_docker": true,
  "reasons": {
    "needs_real_reference_file": ["..."],
    "needs_network": ["..."],
    "needs_docker": ["..."]
  }
}
"""

    def __init__(self) -> None:
        self.enabled = bool(api_key and model)
        self.client = None
        if self.enabled:
            self.client = OpenAI(api_key=api_key, base_url=base_url or None)

    def classify(self, ctx: TaskContext, heuristics: dict[str, TagDecision]) -> dict[str, Any] | None:
        if not self.enabled or self.client is None:
            return None

        payload = {
            "task_path": ctx.task_path,
            "task_id": ctx.task_id,
            "task_name": ctx.task_name,
            "category": ctx.category,
            "prompt_text": ctx.prompt_text,
            "reference_solution": ctx.reference_solution[:1200],
            "tools": ctx.tools,
            "services": ctx.services,
            "tool_endpoints": ctx.tool_endpoints,
            "attachments": ctx.attachments,
            "sandbox_files": ctx.sandbox_files[:30],
            "env_snapshot_files": ctx.env_snapshot_files,
            "env_snapshot_commands": ctx.env_snapshot_commands,
            "sandbox_grader_files": ctx.sandbox_grader_files,
            "workspace_paths": ctx.workspace_paths,
            "fixture_refs": ctx.fixture_refs,
            "file_mentions": ctx.file_mentions,
            "external_urls": ctx.external_urls,
            "heuristics": {
                name: {
                    "value": decision.value,
                    "confidence": decision.confidence,
                    "reasons": decision.reasons,
                }
                for name, decision in heuristics.items()
            },
        }

        response = self.client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)


def _needs_llm_review(
    reference_decision: TagDecision,
    network_decision: TagDecision,
    docker_decision: TagDecision,
) -> bool:
    return any(
        decision.confidence < 0.8
        for decision in (reference_decision, network_decision, docker_decision)
    )


def merge_with_llm(
    ctx: TaskContext,
    heuristics: dict[str, TagDecision],
    llm_result: dict[str, Any] | None,
) -> TaskTags:
    final_values = {
        "needs_real_reference_file": heuristics["needs_real_reference_file"].value,
        "needs_network": heuristics["needs_network"].value,
        "needs_docker": heuristics["needs_docker"].value,
    }
    final_reasons = {
        key: list(decision.reasons) for key, decision in heuristics.items()
    }

    if llm_result:
        for key in final_values:
            if key == "needs_network":
                if heuristics[key].confidence < 0.95:
                    final_values[key] = bool(llm_result.get(key, final_values[key]))
            elif key == "needs_docker":
                if heuristics[key].confidence < 0.95:
                    final_values[key] = bool(llm_result.get(key, final_values[key]))
            else:
                if heuristics[key].confidence < 0.9:
                    final_values[key] = bool(llm_result.get(key, final_values[key]))

        llm_reasons = llm_result.get("reasons") or {}
        for key in final_reasons:
            extra = llm_reasons.get(key) or []
            if extra:
                final_reasons[key].extend(f"llm: {item}" for item in extra[:2])

    return TaskTags(
        task_path=ctx.task_path,
        needs_real_reference_file=final_values["needs_real_reference_file"],
        needs_network=final_values["needs_network"],
        needs_docker=final_values["needs_docker"],
        reasons=final_reasons,
    )


def analyze_task(
    task_dir: Path,
    *,
    llm_mode: str,
    llm_judge: OptionalLLMJudge,
) -> TaskTags:
    ctx = build_task_context(task_dir)
    reference_decision = infer_needs_real_reference_file(ctx)
    network_decision = infer_needs_network(ctx)
    docker_decision = infer_needs_docker(ctx)
    heuristics = {
        "needs_real_reference_file": reference_decision,
        "needs_network": network_decision,
        "needs_docker": docker_decision,
    }

    llm_result = None
    if llm_mode == "all":
        llm_result = llm_judge.classify(ctx, heuristics)
    elif llm_mode == "auto" and _needs_llm_review(
        reference_decision, network_decision, docker_decision
    ):
        llm_result = llm_judge.classify(ctx, heuristics)

    return merge_with_llm(ctx, heuristics, llm_result)


def dump_jsonl(results: list[TaskTags], output_path: Path, *, include_reasons: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            payload = {
                "task_path": result.task_path,
                "needs_real_reference_file": result.needs_real_reference_file,
                "needs_network": result.needs_network,
                "needs_docker": result.needs_docker,
            }
            if include_reasons and result.reasons is not None:
                payload["reasons"] = result.reasons
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def print_summary(results: list[TaskTags]) -> None:
    total = len(results)
    reference_true = sum(item.needs_real_reference_file for item in results)
    network_true = sum(item.needs_network for item in results)
    docker_true = sum(item.needs_docker for item in results)
    print(
        json.dumps(
            {
                "total_tasks": total,
                "needs_real_reference_file": reference_true,
                "needs_network": network_true,
                "needs_docker": docker_true,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tag tasks with reference-file/network/docker requirements."
    )
    parser.add_argument(
        "--tasks-dir",
        default=str(TASKS_DIR),
        help="Directory containing task subdirectories.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=["auto", "all", "never"],
        default="auto",
        help="Whether to use the optional LLM arbitration step.",
    )
    parser.add_argument(
        "--include-reasons",
        action="store_true",
        help="Include heuristic/LLM reasons in the JSONL output.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Optional single task directory name to analyze.",
    )
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir).resolve()
    if args.task:
        task_dirs = [tasks_dir / args.task]
    else:
        task_dirs = sorted(
            path for path in tasks_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists()
        )

    llm_judge = OptionalLLMJudge()
    if args.llm_mode != "never" and not llm_judge.enabled:
        print("LLM disabled: api_key/model not set; using heuristics only.")

    results = [
        analyze_task(task_dir, llm_mode=args.llm_mode, llm_judge=llm_judge)
        for task_dir in task_dirs
    ]
    dump_jsonl(results, Path(args.output).resolve(), include_reasons=args.include_reasons)
    print_summary(results)


if __name__ == "__main__":
    main()
