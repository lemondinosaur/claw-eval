#!/usr/bin/env python3
"""Convert Claw traces or exported JSONL files into ms-swift JSONL data.

The output format is the canonical ms-swift dataset schema:
    {"messages": [...], "tools": [...]}

Notes:
- Trace conversion only processes files ending in ``.jsonl``.
- ``tools_snapshot`` is used when present. Older traces fall back to
  recovering tools from ``tasks/<task_id>/task.yaml`` plus used agent/sandbox
  tools when possible.
- Tool call / tool response payloads are serialized to JSON strings to match
  ms-swift's agent dataset expectations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw_eval.models.content import (  # noqa: E402
    AudioBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
)
from claw_eval.models.message import Message  # noqa: E402
from claw_eval.models.task import TaskDefinition  # noqa: E402
from claw_eval.models.trace import (  # noqa: E402
    ModelInputSnapshot,
    SystemPromptSnapshot,
    ToolsSnapshot,
    TraceMessage,
    TraceStart,
)
from claw_eval.runner.agent_tools import build_agent_tools  # noqa: E402
from claw_eval.runner.sandbox_tools import SANDBOX_TOOLS  # noqa: E402
from claw_eval.trace import read_events  # noqa: E402


LOG = logging.getLogger("trace_dir_to_msswift")
_SANDBOX_TOOL_MAP = {tool.name: tool for tool in SANDBOX_TOOLS}


class TraceConversionError(RuntimeError):
    """Raised when a trace cannot be converted into a training row."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert trace JSONL files or sanitize exported JSONL into ms-swift JSONL data.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--trace-dir",
        help="Directory containing completed trace files. Only *.jsonl files are processed.",
    )
    source_group.add_argument(
        "--input-jsonl",
        help="Existing exported JSONL to sanitize into ms-swift-compatible agent rows.",
    )
    parser.add_argument(
        "--output-jsonl",
        required=True,
        help="Path to the output ms-swift JSONL file.",
    )
    parser.add_argument(
        "--tasks-dir",
        default=str(REPO_ROOT / "tasks"),
        help="Task directory used to recover tools for older traces without tools_snapshot.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Abort on the first conversion error instead of skipping bad traces.",
    )
    parser.add_argument(
        "--sample-mode",
        choices=("auto", "full", "turn"),
        default="auto",
        help=(
            "Dataset row granularity. "
            "'full' keeps one row per trace, "
            "'turn' emits one row per recorded model-input snapshot, "
            "'auto' uses turn rows when snapshots are available."
        ),
    )
    return parser.parse_args()


def _dedupe_tools(tools: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        deduped.append(tool)
    return deduped


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _to_serialized_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _sanitize_row_for_msswift(row: dict[str, Any]) -> dict[str, Any]:
    for message in row.get("messages", []):
        if message.get("role") in {"tool_call", "tool", "tool_response"}:
            message["content"] = _to_serialized_content(message.get("content"))
    return row


def _tool_spec_to_msswift(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _content_block_to_msswift_item(
    block: TextBlock | ImageBlock | AudioBlock | VideoBlock,
) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageBlock):
        return {"type": "image", "image": f"data:{block.mime_type};base64,{block.data}"}
    if isinstance(block, AudioBlock):
        return {"type": "audio", "audio": f"data:{block.mime_type};base64,{block.data}"}
    return {"type": "video", "video": f"data:{block.mime_type};base64,{block.data}"}


def _flush_regular_message(
    output_messages: list[dict[str, Any]],
    role: str,
    blocks: list[TextBlock | ImageBlock | AudioBlock | VideoBlock],
) -> None:
    if not blocks:
        return

    if all(isinstance(block, TextBlock) for block in blocks):
        text = "\n".join(block.text for block in blocks)
        if text:
            output_messages.append({"role": role, "content": text})
        return

    content = [_content_block_to_msswift_item(block) for block in blocks]
    output_messages.append({"role": role, "content": content})


def _convert_assistant_message(message: Message) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    pending: list[TextBlock | ImageBlock | AudioBlock | VideoBlock] = []

    for block in message.content:
        if isinstance(block, ToolUseBlock):
            _flush_regular_message(converted, "assistant", pending)
            pending = []
            tool_content: dict[str, Any] = {
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            }
            if block.extra_content is not None:
                tool_content["extra_content"] = block.extra_content
            converted.append({"role": "tool_call", "content": _to_serialized_content(tool_content)})
            continue

        if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock)):
            pending.append(block)

    _flush_regular_message(converted, "assistant", pending)
    return converted


def _convert_user_message(msg: TraceMessage) -> list[dict[str, Any]]:
    return _convert_user_message_content(msg.message)


def _convert_user_message_content(message: Message) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    pending: list[TextBlock | ImageBlock | AudioBlock | VideoBlock] = []

    for block in message.content:
        if isinstance(block, ToolResultBlock):
            _flush_regular_message(converted, "user", pending)
            pending = []
            text = "\n".join(text_block.text for text_block in block.content)
            tool_result_content = {
                "tool_use_id": block.tool_use_id,
                "is_error": block.is_error,
                "result": _try_parse_json(text),
            }
            converted.append({"role": "tool", "content": _to_serialized_content(tool_result_content)})
            continue

        if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock)):
            pending.append(block)

    _flush_regular_message(converted, "user", pending)
    return converted


def _message_to_msswift(message: Message) -> list[dict[str, Any]]:
    if message.role == "assistant":
        return _convert_assistant_message(message)
    if message.role == "user":
        return _convert_user_message_content(message)
    if message.role == "system":
        system_blocks = [
            block
            for block in message.content
            if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock))
        ]
        converted: list[dict[str, Any]] = []
        _flush_regular_message(converted, "system", system_blocks)
        return converted
    return []


def _trace_messages_to_msswift(messages: list[TraceMessage]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []

    for msg in messages:
        converted.extend(_message_to_msswift(msg.message))

    return converted


def _messages_to_msswift(messages: list[Message]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        converted.extend(_message_to_msswift(message))
    return converted


def _message_has_media(message: Message) -> bool:
    return any(
        isinstance(block, (ImageBlock, AudioBlock, VideoBlock))
        for block in message.content
    )


def _snapshots_have_media(input_snapshots: list[ModelInputSnapshot]) -> bool:
    return any(
        _message_has_media(message)
        for snapshot in input_snapshots
        for message in snapshot.messages
    )


def _collect_used_tool_names(messages: list[TraceMessage]) -> set[str]:
    names: set[str] = set()
    for msg in messages:
        if msg.message.role != "assistant":
            continue
        for block in msg.message.content:
            if isinstance(block, ToolUseBlock):
                names.add(block.name)
    return names


def _recover_tools_from_task(
    task_id: str,
    tasks_dir: Path,
    used_tool_names: set[str],
) -> list[Any]:
    task_path = tasks_dir / task_id / "task.yaml"
    if not task_path.exists():
        raise TraceConversionError(
            f"Missing task.yaml for task_id={task_id!r} at {task_path}",
        )

    task = TaskDefinition.from_yaml(task_path)
    tools = list(task.tools)
    tools.extend(
        build_agent_tools(
            enable_todo=task.environment.enable_todo,
            enable_compact=task.environment.enable_compact,
        )
    )

    existing = {tool.name for tool in tools}
    for name in sorted(used_tool_names):
        sandbox_tool = _SANDBOX_TOOL_MAP.get(name)
        if sandbox_tool is None or name in existing:
            continue
        tools.append(sandbox_tool)
        existing.add(name)

    return _dedupe_tools(tools)


def _extract_trace_parts(
    trace_path: Path,
) -> tuple[TraceStart, str | None, list[Any] | None, list[TraceMessage], list[ModelInputSnapshot]]:
    trace_start: TraceStart | None = None
    system_prompt: str | None = None
    tools_snapshot: list[Any] | None = None
    trace_messages: list[TraceMessage] = []
    input_snapshots: list[ModelInputSnapshot] = []

    for event in read_events(trace_path):
        if isinstance(event, TraceStart):
            trace_start = event
        elif isinstance(event, SystemPromptSnapshot):
            if system_prompt is None:
                system_prompt = event.prompt_text
        elif isinstance(event, ToolsSnapshot):
            if tools_snapshot is None:
                tools_snapshot = list(event.tools)
        elif isinstance(event, TraceMessage):
            trace_messages.append(event)
        elif isinstance(event, ModelInputSnapshot):
            input_snapshots.append(event)

    if trace_start is None:
        raise TraceConversionError(f"{trace_path} is missing trace_start")
    return trace_start, system_prompt, tools_snapshot, trace_messages, input_snapshots


def _resolve_tools_snapshot(
    trace_path: Path,
    trace_start: TraceStart,
    tools_snapshot: list[Any] | None,
    trace_messages: list[TraceMessage],
    tasks_dir: Path,
) -> list[Any]:
    if tools_snapshot is not None:
        return tools_snapshot

    used_tool_names = _collect_used_tool_names(trace_messages)
    resolved = _recover_tools_from_task(
        trace_start.task_id,
        tasks_dir,
        used_tool_names,
    )
    LOG.warning(
        "Trace %s has no tools_snapshot; recovered tools from task.yaml for task %s",
        trace_path.name,
        trace_start.task_id,
    )
    return resolved


def _load_trace_row(trace_path: Path, tasks_dir: Path) -> dict[str, Any]:
    trace_start, system_prompt, tools_snapshot, trace_messages, _ = _extract_trace_parts(trace_path)
    tools_snapshot = _resolve_tools_snapshot(
        trace_path,
        trace_start,
        tools_snapshot,
        trace_messages,
        tasks_dir,
    )

    messages = _trace_messages_to_msswift(trace_messages)
    if system_prompt is not None:
        messages.insert(0, {"role": "system", "content": system_prompt})

    if not messages:
        raise TraceConversionError(f"{trace_path} produced no training messages")

    row: dict[str, Any] = {"messages": messages}
    if tools_snapshot:
        row["tools"] = [
            _tool_spec_to_msswift(tool)
            for tool in _dedupe_tools(tools_snapshot)
        ]
    return _sanitize_row_for_msswift(row)


def _load_trace_turn_rows(trace_path: Path, tasks_dir: Path) -> list[dict[str, Any]]:
    trace_start, _, tools_snapshot, trace_messages, input_snapshots = _extract_trace_parts(trace_path)
    if not input_snapshots:
        return [_load_trace_row(trace_path, tasks_dir)]

    tools_snapshot = _resolve_tools_snapshot(
        trace_path,
        trace_start,
        tools_snapshot,
        trace_messages,
        tasks_dir,
    )
    assistant_by_turn = {
        msg.turn_index: msg
        for msg in trace_messages
        if msg.message.role == "assistant" and msg.turn_index is not None
    }

    rows: list[dict[str, Any]] = []
    for snapshot in sorted(input_snapshots, key=lambda item: item.turn_index):
        assistant_msg = assistant_by_turn.get(snapshot.turn_index)
        if assistant_msg is None:
            raise TraceConversionError(
                f"{trace_path.name} snapshot turn {snapshot.turn_index} is missing assistant output"
            )
        messages = _messages_to_msswift(snapshot.messages)
        messages.extend(_message_to_msswift(assistant_msg.message))
        if not messages:
            raise TraceConversionError(
                f"{trace_path.name} snapshot turn {snapshot.turn_index} produced no training messages"
            )

        row: dict[str, Any] = {"messages": messages}
        if tools_snapshot:
            row["tools"] = [
                _tool_spec_to_msswift(tool)
                for tool in _dedupe_tools(tools_snapshot)
            ]
        rows.append(_sanitize_row_for_msswift(row))

    return rows


def _find_trace_files(trace_dir: Path, output_path: Path) -> list[Path]:
    files = []
    for path in sorted(trace_dir.rglob("*.jsonl")):
        if not path.is_file():
            continue
        if path.resolve() == output_path.resolve():
            continue
        files.append(path)
    return files


def sanitize_exported_jsonl(
    input_path: Path,
    output_path: Path,
    *,
    strict: bool,
) -> None:
    if not input_path.exists() or not input_path.is_file():
        raise TraceConversionError(f"input jsonl does not exist or is not a file: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(f"{output_path.suffix}.tmp") if input_path.resolve() == output_path.resolve() else output_path

    written = 0
    skipped = 0
    with input_path.open("r", encoding="utf-8") as src, temp_path.open("w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                row = _sanitize_row_for_msswift(row)
            except Exception as exc:
                skipped += 1
                LOG.error("Failed to sanitize line %d in %s: %s", line_no, input_path.name, exc)
                if strict:
                    raise
                continue
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    if temp_path != output_path:
        temp_path.replace(output_path)

    LOG.info(
        "Finished sanitization: input=%s written_rows=%d skipped=%d output=%s",
        input_path,
        written,
        skipped,
        output_path,
    )


def convert_trace_directory(
    trace_dir: Path,
    output_path: Path,
    tasks_dir: Path,
    *,
    strict: bool,
    sample_mode: str,
) -> None:
    trace_files = _find_trace_files(trace_dir, output_path)
    if not trace_files:
        raise TraceConversionError(f"No .jsonl trace files found in {trace_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    with output_path.open("w", encoding="utf-8") as f:
        for trace_path in trace_files:
            try:
                if sample_mode == "full":
                    rows = [_load_trace_row(trace_path, tasks_dir)]
                elif sample_mode == "turn":
                    rows = _load_trace_turn_rows(trace_path, tasks_dir)
                else:
                    _, _, _, _, input_snapshots = _extract_trace_parts(trace_path)
                    rows = (
                        _load_trace_turn_rows(trace_path, tasks_dir)
                        if input_snapshots and _snapshots_have_media(input_snapshots)
                        else [_load_trace_row(trace_path, tasks_dir)]
                    )
            except Exception as exc:
                skipped += 1
                LOG.error("Failed to convert %s: %s", trace_path.name, exc)
                if strict:
                    raise
                continue

            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1

    LOG.info(
        "Finished conversion: input_files=%d written_rows=%d skipped=%d output=%s",
        len(trace_files),
        written,
        skipped,
        output_path,
    )


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    output_path = Path(args.output_jsonl).expanduser().resolve()
    trace_dir = Path(args.trace_dir).expanduser().resolve() if args.trace_dir else None
    input_jsonl = Path(args.input_jsonl).expanduser().resolve() if args.input_jsonl else None
    tasks_dir = Path(args.tasks_dir).expanduser().resolve()

    if trace_dir is not None:
        if not trace_dir.exists() or not trace_dir.is_dir():
            raise SystemExit(f"trace directory does not exist or is not a directory: {trace_dir}")
        if not tasks_dir.exists() or not tasks_dir.is_dir():
            raise SystemExit(f"tasks directory does not exist or is not a directory: {tasks_dir}")
    elif input_jsonl is None:
        raise SystemExit("either --trace-dir or --input-jsonl is required")

    try:
        if trace_dir is not None:
            convert_trace_directory(
                trace_dir,
                output_path,
                tasks_dir,
                strict=args.strict,
                sample_mode=args.sample_mode,
            )
        else:
            sanitize_exported_jsonl(input_jsonl, output_path, strict=args.strict)
    except Exception as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
