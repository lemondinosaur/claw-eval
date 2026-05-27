"""Shared helpers for the create_query pipeline."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from claw_eval.models.task import TaskDefinition

BASE_URL = "https://app.ppapi.ai/v1"
API_KEY = ""
MODEL = "deepseek-v4-flash"

ALLOWED_FIXTURE_SUFFIXES = {".json", ".txt"}
DEFAULT_MAX_FILE_CHARS = 300000
DEFAULT_MAX_SHOT_FILE_CHARS = 50000
EXCLUDED_QUERY_TOOLS = {"web_search", "web_fetch"}
SERVICE_FIXTURE_ENV_KEYS = {
    "calendar": "CALENDAR_FIXTURES",
    "config": "CONFIG_FIXTURES",
    "contacts": "CONTACTS_FIXTURES",
    "crm": "CRM_FIXTURES",
    "finance": "FINANCE_FIXTURES",
    "gmail": "GMAIL_FIXTURES",
    "helpdesk": "HELPDESK_FIXTURES",
    "inventory": "INVENTORY_FIXTURES",
    "kb": "KB_FIXTURES",
    "notes": "NOTES_FIXTURES",
    "rss": "RSS_FIXTURES",
    "scheduler": "SCHEDULER_FIXTURES",
    "todo": "TODO_FIXTURES",
}
CREATE_QUERY_TZ = timezone(timedelta(hours=8))
CREATE_QUERY_TZ_LABEL = "UTC+08:00"
BUNDLE_SCHEMA_VERSION = "create_query.task_bundle.v1"


class QueryPipelineError(RuntimeError):
    """Raised when the create_query pipeline cannot proceed."""


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root_default() -> Path:
    return script_dir().parent


def resolve_output_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if path.is_absolute():
        return path
    return script_dir() / path


def now_str() -> str:
    return datetime.now(CREATE_QUERY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def batch_timestamp() -> str:
    return datetime.now(CREATE_QUERY_TZ).strftime("%Y%m%d_%H%M%S")


def resolve_run_output_dir(output_dir: str | Path | None = None) -> Path:
    if output_dir:
        return resolve_output_dir(output_dir)
    env_dir = os.environ.get("CREATE_QUERY_RUN_DIR", "").strip()
    if env_dir:
        return resolve_output_dir(env_dir)
    return script_dir() / f"output_{batch_timestamp()}"


def infer_run_dir_from_path(path: str | Path) -> Path | None:
    p = Path(path).resolve()
    for candidate in [p.parent, *p.parents]:
        if candidate.name == "tasks":
            return candidate.parent
    return None


def find_latest_run_dir_for_task(source_task_id: str) -> Path | None:
    base = script_dir()
    runs = sorted((p for p in base.glob("output_*") if p.is_dir()), reverse=True)
    for run_dir in runs:
        task_dir = run_dir / "tasks" / source_task_id
        if (task_dir / "bundle.json").exists() or (task_dir / "step1" / "output.json").exists():
            return run_dir
    return None


def read_text(path: Path, max_chars: int = DEFAULT_MAX_FILE_CHARS) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[TRUNCATED BY create_query]...\n"
    return text


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_bundle_path(out_base: Path, source_task_id: str) -> Path:
    return out_base / "tasks" / source_task_id / "bundle.json"


def init_task_bundle(
    source_task_id: str,
    *,
    source_task_dir: str | None = None,
    out_base: Path | None = None,
) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": now_str(),
        "timezone": CREATE_QUERY_TZ_LABEL,
        "source_task_id": source_task_id,
        "step1": {
            "status": "not_started",
            "generated_at": "",
            "prompt": "",
            "raw": "",
            "output": None,
            "error": "",
        },
        "step2": {
            "status": "not_started",
            "generated_at": "",
            "items": [],
            "error": "",
        },
    }
    if source_task_dir:
        bundle["source_task_dir"] = source_task_dir
    if out_base is not None:
        bundle["run_dir"] = str(out_base)
        bundle["run_name"] = out_base.name
    return bundle


def load_task_bundle(
    bundle_path: Path,
    *,
    source_task_id: str,
    source_task_dir: str | None = None,
    out_base: Path | None = None,
) -> dict[str, Any]:
    if bundle_path.exists():
        obj = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise QueryPipelineError(f"Task bundle must be a JSON object: {bundle_path}")
        bundle = obj
    else:
        bundle = init_task_bundle(
            source_task_id,
            source_task_dir=source_task_dir,
            out_base=out_base,
        )

    bundle.setdefault("schema_version", BUNDLE_SCHEMA_VERSION)
    bundle.setdefault("generated_at", now_str())
    bundle.setdefault("timezone", CREATE_QUERY_TZ_LABEL)
    bundle["source_task_id"] = source_task_id
    if source_task_dir:
        bundle["source_task_dir"] = source_task_dir
    if out_base is not None:
        bundle["run_dir"] = str(out_base)
        bundle["run_name"] = out_base.name

    step1 = bundle.get("step1")
    if not isinstance(step1, dict):
        step1 = {}
    step1.setdefault("status", "not_started")
    step1.setdefault("generated_at", "")
    step1.setdefault("prompt", "")
    step1.setdefault("raw", "")
    step1.setdefault("output", None)
    step1.setdefault("error", "")
    bundle["step1"] = step1

    step2 = bundle.get("step2")
    if not isinstance(step2, dict):
        step2 = {}
    step2.setdefault("status", "not_started")
    step2.setdefault("generated_at", "")
    step2.setdefault("items", [])
    step2.setdefault("error", "")
    step2.setdefault("summary", {})
    bundle["step2"] = step2
    return bundle


def step2_expected_items(bundle: dict[str, Any]) -> int | None:
    step1 = bundle.get("step1")
    if not isinstance(step1, dict):
        return None
    output = step1.get("output")
    if not isinstance(output, dict):
        return None
    descs = output.get("new_task_descriptions")
    if not isinstance(descs, list):
        return None
    return len(descs)


def step2_materialized_item_count(items: list[Any]) -> int:
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        task_dir = str(item.get("task_dir") or "").strip()
        if task_dir and Path(task_dir).exists():
            count += 1
    return count


def step2_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    step2 = bundle.get("step2")
    items = step2.get("items") if isinstance(step2, dict) else []
    if not isinstance(items, list):
        items = []

    counts: Counter[str] = Counter()
    failed_task_ids: list[str] = []
    tracked_task_ids: set[str] = set()
    in_progress_items = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            tracked_task_ids.add(task_id)
        status = str(item.get("status") or "not_started").strip() or "not_started"
        counts[status] += 1
        if status == "failed" and task_id:
            failed_task_ids.append(task_id)
        elif status not in {"completed", "failed", "not_started"}:
            in_progress_items += 1

    expected_items = step2_expected_items(bundle)
    completed_items = counts["completed"]
    failed_items = counts["failed"]
    explicit_not_started = counts["not_started"]
    tracked_items = len(tracked_task_ids) if tracked_task_ids else sum(counts.values())

    if expected_items is None:
        not_started_items = explicit_not_started
    else:
        not_started_items = max(
            expected_items - completed_items - failed_items - in_progress_items,
            explicit_not_started,
        )

    materialized_items = step2_materialized_item_count(items)

    if failed_items:
        if completed_items == 0 and in_progress_items == 0 and not_started_items == 0:
            overall_status = "failed"
        else:
            overall_status = "partial_failed"
    elif expected_items is not None and expected_items > 0 and completed_items >= expected_items and in_progress_items == 0:
        overall_status = "completed"
    elif expected_items is None and tracked_items > 0 and completed_items == tracked_items and in_progress_items == 0:
        overall_status = "completed"
    elif in_progress_items > 0 or completed_items > 0 or materialized_items > 0:
        overall_status = "running"
    else:
        overall_status = "not_started"

    status_counts = {key: counts[key] for key in sorted(counts)}
    return {
        "status": overall_status,
        "expected_items": expected_items,
        "tracked_items": tracked_items,
        "materialized_items": materialized_items,
        "completed_items": completed_items,
        "failed_items": failed_items,
        "in_progress_items": in_progress_items,
        "not_started_items": not_started_items,
        "failed_task_ids": failed_task_ids,
        "status_counts": status_counts,
    }


def refresh_step2_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    step2 = bundle.get("step2")
    if not isinstance(step2, dict):
        step2 = {}
        bundle["step2"] = step2

    summary = step2_summary(bundle)
    step2["summary"] = summary
    step2["status"] = summary["status"]

    if summary["failed_items"]:
        failed_parts: list[str] = []
        items = step2.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict) or str(item.get("status") or "") != "failed":
                    continue
                task_id = str(item.get("task_id") or "").strip() or "<unknown>"
                error = str(item.get("error") or "").strip()
                failed_parts.append(f"{task_id}: {error}" if error else task_id)
        if len(failed_parts) > 3:
            more = len(failed_parts) - 3
            failed_parts = [*failed_parts[:3], f"... (+{more} more)"]
        step2["error"] = "; ".join(failed_parts)
    else:
        step2["error"] = ""

    return summary


def save_task_bundle(bundle_path: Path, bundle: dict[str, Any]) -> None:
    bundle["generated_at"] = now_str()
    refresh_step2_summary(bundle)
    dump_json(bundle_path, bundle)


def strip_code_fence(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def strip_file_fence(text: str) -> str:
    s = str(text).strip()
    s = re.sub(r"^```(?:yaml|yml|json|python|py|txt)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.rstrip() + "\n"


def extract_json_object(text: str) -> dict[str, Any]:
    s = strip_code_fence(text)
    try:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            raise QueryPipelineError("Model output JSON must be an object.")
        return obj
    except json.JSONDecodeError:
        pass

    start = s.find("{")
    if start < 0:
        raise QueryPipelineError("No JSON object found in model output.")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(s[start : i + 1])
                if not isinstance(obj, dict):
                    raise QueryPipelineError("Extracted JSON must be an object.")
                return obj
    raise QueryPipelineError("Could not extract complete JSON object.")


def call_chat(
    prompt: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    system_prompt: str,
) -> str:
    if not base_url:
        raise QueryPipelineError("Missing base URL. Set CREATE_QUERY_BASE_URL or pass --base-url.")
    if not api_key:
        raise QueryPipelineError("Missing API key. Set CREATE_QUERY_API_KEY or pass --api-key.")
    if not model:
        raise QueryPipelineError("Missing model. Set CREATE_QUERY_MODEL or pass --model.")

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise QueryPipelineError(f"API HTTPError {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise QueryPipelineError(f"API URLError: {exc}") from exc

    data = json.loads(raw)
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise QueryPipelineError(f"Unexpected API response shape: {raw[:1000]}") from exc


def fixture_files(task_dir: Path) -> list[Path]:
    fixtures_dir = task_dir / "fixtures"
    if not fixtures_dir.exists() or not fixtures_dir.is_dir():
        return []
    return sorted(p for p in fixtures_dir.rglob("*") if p.is_file())


def task_definition(task_dir: Path) -> TaskDefinition:
    yaml_path = task_dir / "task.yaml"
    if not yaml_path.exists():
        raise QueryPipelineError(f"Missing task.yaml under {task_dir}")
    return TaskDefinition.from_yaml(yaml_path)


def supported_fixture_files(task_dir: Path) -> list[Path]:
    return [p for p in fixture_files(task_dir) if p.suffix.lower() in ALLOWED_FIXTURE_SUFFIXES]


def task_is_supported(task_dir: Path) -> tuple[bool, str]:
    if not task_dir.is_dir():
        return False, "not a directory"
    if not (task_dir / "task.yaml").exists():
        return False, "missing task.yaml"
    try:
        td = task_definition(task_dir)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"task.yaml parse error: {exc}"
    if td.user_agent.enabled:
        return False, "user_agent.enabled"
    files = fixture_files(task_dir)
    if not files:
        return False, "missing fixtures"
    bad = [str(p.relative_to(task_dir)) for p in files if p.suffix.lower() not in ALLOWED_FIXTURE_SUFFIXES]
    if bad:
        return False, "unsupported fixture suffix: " + ", ".join(bad[:5])
    return True, "ok"


def load_task_tags(tag_path: Path) -> dict[str, dict[str, bool]]:
    if not tag_path.exists():
        raise QueryPipelineError(f"Task tags JSONL not found: {tag_path}")

    out: dict[str, dict[str, bool]] = {}
    for lineno, line in enumerate(tag_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QueryPipelineError(f"Invalid JSON on line {lineno} of {tag_path}: {exc}") from exc
        task_path = str(obj.get("task_path", "")).strip()
        if not task_path:
            raise QueryPipelineError(f"Missing task_path on line {lineno} of {tag_path}")
        out[task_path] = {
            "needs_network": bool(obj.get("needs_network", False)),
            "needs_real_reference_file": bool(obj.get("needs_real_reference_file", False)),
            "needs_docker": bool(obj.get("needs_docker", False)),
        }
    return out


def zh_preferred_key(task_name: str) -> str:
    match = re.match(r"^[A-Z]\d+(?:zh)?_(.+)$", task_name)
    if match:
        key = match.group(1)
    else:
        key = task_name
    if key.endswith("_zh"):
        key = key[:-3]
    return key


def is_zh_variant(task_name: str) -> bool:
    return bool(re.match(r"^[A-Z]\d+zh_", task_name) or task_name.endswith("_zh"))


def infer_task_instruction_language(task_name: str, *, fallback: str | None = None) -> str:
    if is_zh_variant(task_name):
        return "zh"
    if fallback in {"zh", "en"}:
        return fallback
    return "en"


def task_in_generation_scope(
    task_dir: Path,
    *,
    task_tags: dict[str, dict[str, bool]] | None = None,
) -> tuple[bool, str]:
    ok, reason = task_is_supported(task_dir)
    if not ok:
        return ok, reason

    try:
        td = task_definition(task_dir)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"task.yaml parse error: {exc}"

    if "general" not in set(td.tags or []):
        return False, "missing general tag"
    if td.prompt.attachments:
        return False, "prompt.attachments"

    blocked_tools = sorted({tool.name for tool in td.tools} & EXCLUDED_QUERY_TOOLS)
    if blocked_tools:
        return False, "excluded tools: " + ", ".join(blocked_tools)

    if task_tags is not None:
        task_path = f"tasks/{task_dir.name}"
        meta = task_tags.get(task_path)
        if meta is None:
            return False, "missing task_tags entry"
        if meta.get("needs_network"):
            return False, "needs_network"
        if meta.get("needs_real_reference_file"):
            return False, "needs_real_reference_file"

    return True, "ok"


def task_summary(task: TaskDefinition, fixture_paths: list[str], *, has_grader: bool = False) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_name": task.task_name,
        "task_type": task.category or task.task_name,
        "category": task.category,
        "difficulty": task.difficulty,
        "tags": task.tags,
        "language": task.prompt.language,
        "services": [svc.name for svc in task.services],
        "tools": [tool.name for tool in task.tools],
        "fixture_paths": fixture_paths,
        "sandbox_files": task.sandbox_files,
        "has_user_agent": task.user_agent.enabled,
        "has_grader": has_grader,
        "core_capability": task.category or task.task_name,
    }


def file_sections(files: dict[str, str]) -> str:
    sections: list[str] = []
    for rel, content in files.items():
        sections.append(f"--- FILE: {rel} ---\n{content}\n--- END FILE: {rel} ---")
    return "\n".join(sections)


def normalise_files_payload(obj: dict[str, Any]) -> dict[str, str]:
    files = obj.get("files")
    if isinstance(files, dict):
        out: dict[str, str] = {}
        for key, value in files.items():
            if isinstance(value, str):
                out[str(key)] = strip_file_fence(value)
            else:
                out[str(key)] = strip_file_fence(json.dumps(value, ensure_ascii=False, indent=2))
        return out
    if isinstance(files, list):
        out = {}
        for item in files:
            if not isinstance(item, dict) or "path" not in item or "content" not in item:
                raise QueryPipelineError("files list entries must have path and content.")
            out[str(item["path"])] = strip_file_fence(item["content"])
        return out
    raise QueryPipelineError("Model JSON must include files as dict or list.")


def validate_rel_path(rel: str) -> None:
    p = Path(rel)
    if p.is_absolute():
        raise QueryPipelineError(f"Absolute path not allowed: {rel}")
    if any(part == ".." for part in p.parts):
        raise QueryPipelineError(f"Parent traversal path not allowed: {rel}")
    if not rel or rel.endswith("/"):
        raise QueryPipelineError(f"Invalid file path: {rel}")
