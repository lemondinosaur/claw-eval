#!/usr/bin/env python3
"""Classify tasks by whether they can run without Docker.

This script inspects the repository's current execution pipeline rather than
guessing from task names:

1. `claw-eval run --sandbox`:
   - starts a Docker container
   - injects `sandbox_files`
   - enables remote sandbox tools
   - collects `env_snapshot_files` / `env_snapshot_commands`

2. `claw-eval run --sandbox-tools`:
   - does NOT use Docker
   - enables local sandbox tools on the host
   - does NOT collect env snapshots
   - local `ReadMedia` / `Download` are unavailable in the current codebase

3. plain `claw-eval run`:
   - no Docker
   - no sandbox tools
   - only task-defined tools / services / prompt attachments are available

The goal is to find which tasks are actually executable without Docker under
the current repository implementation.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"

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

WORKSPACE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])(/workspace(?:/[^\s\"'`),:;]+)?)")
FIXTURE_REF_RE = re.compile(r"fixtures/[^\s\"'`),:;]+")


@dataclass
class TaskAnalysis:
    task_dir: str
    task_id: str
    category: str
    mode: str
    runnable_without_docker: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggested_command: str = ""
    has_services: bool = False
    has_tool_endpoints: bool = False
    has_attachments: bool = False
    has_workspace_paths_in_prompt: bool = False
    has_env_snapshot_needs: bool = False
    has_sandbox_grader_files: bool = False
    has_local_grader_files: bool = False
    sandbox_files_count: int = 0


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
    return [str(x) for x in attachments]


def _extract_tools(task: dict[str, Any]) -> list[str]:
    tools = task.get("tools") or []
    names: list[str] = []
    for tool in tools:
        name = tool.get("name")
        if name:
            names.append(str(name))
    return names


def _extract_workspace_paths(text: str) -> list[str]:
    return sorted({m.group(1) for m in WORKSPACE_PATH_RE.finditer(text or "")})


def _extract_fixture_refs(text: str) -> list[str]:
    return sorted({m.group(0) for m in FIXTURE_REF_RE.finditer(text or "")})


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
    return any(sig in grader_text for sig in signals)


def _guess_prompt_needs_sandbox_tools(
    task: dict[str, Any],
    grader_text: str,
) -> tuple[bool, list[str]]:
    prompt_text = _extract_prompt_text(task)
    attachments = _extract_attachments(task)
    fixture_refs = _extract_fixture_refs(prompt_text)
    workspace_paths = _extract_workspace_paths(prompt_text)
    task_tools = _extract_tools(task)
    reasons: list[str] = []

    if attachments:
        return False, reasons

    if task_tools:
        known_info_tools = {
            "ocr_extract_text",
            "caption_describe_image",
            "web_search",
            "web_fetch",
        }
        if any(name in known_info_tools for name in task_tools):
            return False, reasons

    lower_prompt = prompt_text.lower()
    lower_grader = grader_text.lower()

    needs_remote_only = (
        "readmedia" in lower_prompt
        or "readmedia" in lower_grader
        or "watch the video" in lower_prompt
        or "watch video" in lower_prompt
        or "extract frames" in lower_prompt
        or "video file" in lower_prompt
        or "video.webm" in lower_prompt
        or "video.mp4" in lower_prompt
        or "subtitle" in lower_prompt
        or "视频" in prompt_text
        or "字幕" in prompt_text
        or "观看视频" in prompt_text
        or "看视频" in prompt_text
        or "逐帧" in prompt_text
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


def _inputs_live_only_in_sandbox(task: dict[str, Any]) -> tuple[bool, list[str]]:
    prompt_text = _extract_prompt_text(task)
    attachments = _extract_attachments(task)
    services = task.get("services") or []
    tool_endpoints = task.get("tool_endpoints") or []
    sandbox_files = [str(x) for x in (task.get("sandbox_files") or [])]
    task_tools = _extract_tools(task)
    workspace_paths = _extract_workspace_paths(prompt_text)
    fixture_refs = _extract_fixture_refs(prompt_text)
    lower = prompt_text.lower()

    if not sandbox_files:
        return False, []
    if attachments or services or tool_endpoints:
        return False, []

    if any(
        name in {"ocr_extract_text", "caption_describe_image", "web_search", "web_fetch"}
        for name in task_tools
    ):
        return False, []

    input_like_refs = bool(workspace_paths or fixture_refs)
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
        if workspace_paths:
            reasons.append(
                "prompt references /workspace paths that only make sense inside the sandbox container"
            )
        elif fixture_refs:
            reasons.append(
                "prompt references local fixture paths, but plain mode provides no file-reading tools"
            )
        return True, reasons

    return False, []


def analyze_task(task_dir: Path, *, workspace_dir_exists: bool) -> TaskAnalysis:
    task_yaml = task_dir / "task.yaml"
    grader_py = task_dir / "grader.py"

    task = _load_yaml(task_yaml)
    grader_text = _read_text(grader_py)
    prompt_text = _extract_prompt_text(task)
    attachments = _extract_attachments(task)
    tool_names = _extract_tools(task)

    services = task.get("services") or []
    tool_endpoints = task.get("tool_endpoints") or []
    env_snapshot_files = [str(x) for x in (task.get("env_snapshot_files") or [])]
    env_snapshot_commands = [str(x) for x in (task.get("env_snapshot_commands") or [])]
    sandbox_grader_files = [str(x) for x in (task.get("sandbox_grader_files") or [])]
    local_grader_files = [str(x) for x in (task.get("local_grader_files") or [])]
    sandbox_files = [str(x) for x in (task.get("sandbox_files") or [])]
    environment_cfg = task.get("environment") or {}

    task_id = str(task.get("task_id") or task_dir.name)
    category = str(task.get("category") or "")
    workspace_paths = _extract_workspace_paths(prompt_text)
    grader_uses_env = _grader_uses_env_snapshot(grader_text)
    declared_env_snapshot = bool(env_snapshot_files or env_snapshot_commands)
    needs_env_snapshot = declared_env_snapshot or bool(sandbox_grader_files) or grader_uses_env
    has_workspace_paths = bool(workspace_paths)

    analysis = TaskAnalysis(
        task_dir=task_dir.name,
        task_id=task_id,
        category=category,
        mode="plain",
        runnable_without_docker=True,
        has_services=bool(services),
        has_tool_endpoints=bool(tool_endpoints),
        has_attachments=bool(attachments),
        has_workspace_paths_in_prompt=has_workspace_paths,
        has_env_snapshot_needs=needs_env_snapshot,
        has_sandbox_grader_files=bool(sandbox_grader_files),
        has_local_grader_files=bool(local_grader_files),
        sandbox_files_count=len(sandbox_files),
    )

    explicit_sandbox_tools = bool(environment_cfg.get("sandbox_tools"))
    declared_sandbox_tool_names = [name for name in tool_names if name in SANDBOX_TOOL_NAMES]

    if needs_env_snapshot:
        analysis.mode = "docker_required"
        analysis.runnable_without_docker = False
        if env_snapshot_files:
            analysis.reasons.append(
                "task.yaml declares env_snapshot_files; current CLI only collects them in --sandbox mode"
            )
        if env_snapshot_commands:
            analysis.reasons.append(
                "task.yaml declares env_snapshot_commands; current CLI only runs them in --sandbox mode"
            )
        if sandbox_grader_files:
            analysis.reasons.append(
                "task.yaml declares sandbox_grader_files; these are injected only into the Docker sandbox after the agent loop"
            )
        if grader_uses_env:
            analysis.reasons.append(
                "grader reads env_snapshot artifacts (file:/cmd: entries), which plain mode never provides"
            )
        analysis.suggested_command = (
            f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox"
        )
        return analysis

    if explicit_sandbox_tools or declared_sandbox_tool_names:
        remote_only_declared = any(
            name in REMOTE_ONLY_LOCAL_SANDBOX_TOOLS for name in declared_sandbox_tool_names
        )

        if remote_only_declared:
            analysis.mode = "docker_required"
            analysis.runnable_without_docker = False
            if explicit_sandbox_tools:
                analysis.reasons.append(
                    "task.yaml explicitly enables sandbox_tools and declares remote-only sandbox tools"
                )
            analysis.reasons.append(
                "declared sandbox tools include ReadMedia/Download, which local --sandbox-tools mode cannot provide"
            )
            analysis.suggested_command = (
                f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox"
            )
            return analysis

        analysis.mode = "host_sandbox_tools"
        analysis.runnable_without_docker = True
        if explicit_sandbox_tools:
            analysis.reasons.append(
                "task.yaml explicitly enables sandbox_tools in environment"
            )
        if declared_sandbox_tool_names:
            tool_list = ", ".join(sorted(declared_sandbox_tool_names))
            analysis.reasons.append(
                f"task declares host sandbox tool(s): {tool_list}"
            )
        analysis.reasons.append(
            "run in host mode with --sandbox-tools so local sandbox tool calls are available"
        )
        analysis.suggested_command = (
            f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox-tools"
        )
        return analysis

    needs_sandbox_tools, sandbox_tool_reasons = _guess_prompt_needs_sandbox_tools(task, grader_text)
    if needs_sandbox_tools:
        remote_only_needed = any(
            token in prompt_text.lower()
            for token in ("video.mp4", "video.webm", "watch the video", "watch video", "extract frames")
        ) or "readmedia" in grader_text.lower()

        if remote_only_needed:
            analysis.mode = "docker_required"
            analysis.runnable_without_docker = False
            analysis.reasons.extend(sandbox_tool_reasons)
            analysis.reasons.append(
                "local --sandbox-tools mode cannot provide ReadMedia; in current code ReadMedia works only via remote sandbox container"
            )
            analysis.suggested_command = (
                f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox"
            )
            return analysis

        analysis.mode = "host_sandbox_tools"
        analysis.runnable_without_docker = True
        analysis.reasons.extend(sandbox_tool_reasons)
        analysis.reasons.append(
            "task likely needs local sandbox file/shell tools, but not env snapshots"
        )
        if not workspace_dir_exists and any(p.startswith("/workspace/") for p in workspace_paths):
            analysis.warnings.append(
                "current host has no /workspace directory; local --sandbox-tools mode may still fail on hard-coded /workspace paths"
            )
        analysis.suggested_command = (
            f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox-tools"
        )
        return analysis

    sandbox_only_inputs, sandbox_only_reasons = _inputs_live_only_in_sandbox(task)
    if sandbox_only_inputs:
        analysis.mode = "docker_required"
        analysis.runnable_without_docker = False
        analysis.reasons.extend(sandbox_only_reasons)
        analysis.reasons.append(
            "without --sandbox, current runner does not inject sandbox_files into any host-accessible workspace"
        )
        analysis.suggested_command = (
            f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name} --sandbox"
        )
        return analysis

    analysis.mode = "plain"
    analysis.runnable_without_docker = True

    if services or tool_endpoints:
        analysis.reasons.append(
            "task relies on mock services / tool_endpoints, which ServiceManager can run directly on the host"
        )
    if attachments:
        analysis.reasons.append(
            "prompt.attachments are loaded by the host-side media loader; Docker is not required for input delivery"
        )
    if not services and not tool_endpoints and not attachments:
        analysis.reasons.append(
            "task has no env_snapshot dependency and no obvious sandbox-only inputs; it can run in plain host mode"
        )

    if sandbox_files and not attachments and not has_workspace_paths and services:
        analysis.warnings.append(
            "task declares sandbox_files, but they appear to be for service fixture parity rather than a hard Docker requirement"
        )

    analysis.suggested_command = (
        f"PYTHONPATH=src python -m claw_eval.cli run --task tasks/{task_dir.name}"
    )
    return analysis


def summarize(analyses: list[TaskAnalysis]) -> dict[str, Any]:
    by_mode: dict[str, int] = {}
    for item in analyses:
        by_mode[item.mode] = by_mode.get(item.mode, 0) + 1

    return {
        "total_tasks": len(analyses),
        "modes": by_mode,
        "non_docker_tasks": [a.task_dir for a in analyses if a.runnable_without_docker],
        "plain_tasks": [a.task_dir for a in analyses if a.mode == "plain"],
        "host_sandbox_tools_tasks": [a.task_dir for a in analyses if a.mode == "host_sandbox_tools"],
        "docker_required_tasks": [a.task_dir for a in analyses if a.mode == "docker_required"],
    }


def render_text(analyses: list[TaskAnalysis], *, show_all: bool) -> str:
    summary = summarize(analyses)
    lines: list[str] = []
    lines.append("Task Docker Classification")
    lines.append(f"repo: {REPO_ROOT}")
    lines.append(f"total_tasks: {summary['total_tasks']}")
    lines.append("")
    lines.append("Summary:")
    for mode in ("plain", "host_sandbox_tools", "docker_required"):
        lines.append(f"  {mode}: {summary['modes'].get(mode, 0)}")

    lines.append("")
    lines.append("Interpretation:")
    lines.append("  plain: run directly, no Docker, no sandbox tools")
    lines.append("  host_sandbox_tools: no Docker, but needs --sandbox-tools")
    lines.append("  docker_required: current code path requires --sandbox")

    selected = analyses if show_all else [a for a in analyses if a.runnable_without_docker]
    lines.append("")
    lines.append("Tasks:")
    for item in selected:
        lines.append(
            f"- {item.task_dir}: mode={item.mode} runnable_without_docker={str(item.runnable_without_docker).lower()}"
        )
        for reason in item.reasons:
            lines.append(f"    reason: {reason}")
        for warning in item.warnings:
            lines.append(f"    warning: {warning}")
        lines.append(f"    command: {item.suggested_command}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify tasks by whether they can run without Docker."
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Analyze one task directory name, e.g. T008_todo_management",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "non_docker", "plain", "host_sandbox_tools", "docker_required"],
        default="non_docker",
        help="Filter output by classification mode.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="For text mode, include all analyzed tasks regardless of filter.",
    )
    args = parser.parse_args()

    if args.task:
        task_dirs = [TASKS_DIR / args.task]
    else:
        task_dirs = sorted(
            p for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task.yaml").exists()
        )

    workspace_dir_exists = Path("/workspace").exists()

    analyses = [
        analyze_task(task_dir, workspace_dir_exists=workspace_dir_exists)
        for task_dir in task_dirs
    ]

    if args.mode != "all":
        if args.mode == "non_docker":
            analyses = [a for a in analyses if a.runnable_without_docker]
        else:
            analyses = [a for a in analyses if a.mode == args.mode]

    if args.format == "json":
        payload = {
            "repo_root": str(REPO_ROOT),
            "workspace_dir_exists": workspace_dir_exists,
            "summary": summarize(analyses),
            "tasks": [asdict(a) for a in analyses],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(render_text(analyses, show_all=args.show_all or args.mode == "all"))


if __name__ == "__main__":
    main()
