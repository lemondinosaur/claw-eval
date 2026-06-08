import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.claw_eval.models.content import TextBlock, ToolResultBlock, ToolUseBlock
from src.claw_eval.models.message import Message
from src.claw_eval.models.tool import ToolSpec
from src.claw_eval.models.trace import TokenUsage
from src.claw_eval.trace.multimodal_turn_recorder import MultimodalTurnRecorder
from train_dataset.trace_dir_to_msswift_multimodal import convert_trace_directory


def _write_text(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_real_export_renders_runtime_context_for_tool_media(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace_root"
    turns_dir = trace_root / "sample_mm_turns"
    assets_dir = turns_dir / "sample_assets"
    turns_path = turns_dir / "sample.turns.jsonl"

    input_image = assets_dir / "input.png"
    frame_0 = assets_dir / "frame_000.png"
    frame_1 = assets_dir / "frame_001.png"
    for path in (input_image, frame_0, frame_1):
        _write_text(path)

    header = {
        "type": "header",
        "trace_version": "multimodal_turns/v1",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "ReadMedia",
                    "description": "Read a video into frames.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "max_frames": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                },
            }
        ],
    }
    turn = {
        "type": "turn",
        "messages": [
            {"role": "system", "content": "SYSTEM PROMPT"},
            {"role": "user", "content": "请分析这个视频。<image>"},
            {
                "role": "tool_call",
                "content": json.dumps(
                    {"name": "ReadMedia", "arguments": {"path": "/workspace/a.mp4", "max_frames": 1}},
                    ensure_ascii=False,
                ),
                "tool_call_id": "call_a",
                "extra_content": {"provider_only": True},
            },
            {
                "role": "tool_call",
                "content": json.dumps(
                    {"name": "ReadMedia", "arguments": {"path": "/workspace/b.mp4", "max_frames": 1}},
                    ensure_ascii=False,
                ),
                "tool_call_id": "call_b",
            },
            {
                "role": "tool",
                "content": '{"media_type":"video","path":"/workspace/a.mp4"}',
                "tool_call_id": "call_a",
                "is_error": False,
            },
            {
                "role": "tool",
                "content": '{"media_type":"video","path":"/workspace/b.mp4"}',
                "tool_call_id": "call_b",
                "is_error": False,
            },
            {"role": "user", "content": "[Visual content from tool results: 2 image(s)]<image><image>"},
            {"role": "assistant", "content": "这是总结。"},
        ],
        "images": [
            "sample_assets/input.png",
            "sample_assets/frame_000.png",
            "sample_assets/frame_001.png",
        ],
    }

    turns_path.parent.mkdir(parents=True, exist_ok=True)
    turns_path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n" + json.dumps(turn, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out.jsonl"
    count = convert_trace_directory(trace_root, output_path)
    assert count == 1

    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert row["tools"] == header["tools"]
    assert all(set(message.keys()) == {"role", "content"} for message in row["messages"])
    assert [message["role"] for message in row["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    assert row["messages"][0]["content"] == "SYSTEM PROMPT"

    assert row["messages"][1]["content"] == "请分析这个视频。<image>"
    assert row["messages"][2]["content"] == (
        '<tool_call>\n{"name": "ReadMedia", "arguments": {"path": "/workspace/a.mp4", "max_frames": 1}}\n'
        '</tool_call>\n'
        '<tool_call>\n{"name": "ReadMedia", "arguments": {"path": "/workspace/b.mp4", "max_frames": 1}}\n'
        "</tool_call>"
    )
    assert row["messages"][3]["content"] == (
        '<tool_response>\n{"media_type":"video","path":"/workspace/a.mp4"}\n</tool_response>\n'
        '<tool_response>\n{"media_type":"video","path":"/workspace/b.mp4"}\n</tool_response>'
        "<|im_end|>\n<|im_start|>user\n"
        "[Visual content from tool results: 2 image(s)]<image><image>"
    )
    assert row["messages"][4]["content"] == "这是总结。"
    assert row["images"] == [str(input_image.resolve()), str(frame_0.resolve()), str(frame_1.resolve())]


def test_export_keeps_plain_system_for_toolless_tasks(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace_root"
    turns_dir = trace_root / "sample_mm_turns"
    turns_path = turns_dir / "sample.turns.jsonl"

    header = {
        "type": "header",
        "trace_version": "multimodal_turns/v1",
        "tools": [],
    }
    turn = {
        "type": "turn",
        "messages": [
            {"role": "system", "content": "SYSTEM PROMPT"},
            {"role": "user", "content": "请直接回答。<image>"},
            {"role": "assistant", "content": "这是答案。"},
        ],
        "images": [],
    }

    turns_path.parent.mkdir(parents=True, exist_ok=True)
    turns_path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n" + json.dumps(turn, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out.jsonl"
    count = convert_trace_directory(trace_root, output_path)
    assert count == 1

    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert "tools" not in row
    assert row["messages"] == [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "请直接回答。<image>"},
        {"role": "assistant", "content": "这是答案。"},
    ]


def test_export_matches_runtime_tool_call_rendering_details(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace_root"
    turns_dir = trace_root / "sample_mm_turns"
    turns_path = turns_dir / "sample.turns.jsonl"

    header = {
        "type": "header",
        "trace_version": "multimodal_turns/v1",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Read an image.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
    }
    turn = {
        "type": "turn",
        "messages": [
            {"role": "system", "content": "SYSTEM PROMPT"},
            {"role": "user", "content": "请继续。"},
            {"role": "assistant", "content": "我先读取一下。"},
            {
                "role": "tool_call",
                "content": json.dumps(
                    {"name": "Read", "arguments": {"path": "/workspace/地铁1.png"}},
                    ensure_ascii=False,
                ),
            },
            {"role": "tool", "content": '{"ok":true}'},
            {"role": "user", "content": "[Visual content from tool results: 1 image(s)]<image>"},
            {"role": "assistant", "content": "读取完成。"},
        ],
        "images": [],
    }

    turns_path.parent.mkdir(parents=True, exist_ok=True)
    turns_path.write_text(
        json.dumps(header, ensure_ascii=False) + "\n" + json.dumps(turn, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out.jsonl"
    count = convert_trace_directory(trace_root, output_path)
    assert count == 1

    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert row["messages"][2]["content"] == (
        "我先读取一下。\n"
        '<tool_call>\n{"name": "Read", "arguments": {"path": "/workspace/地铁1.png"}}\n</tool_call>'
    )
    assert row["messages"][3]["content"] == (
        '<tool_response>\n{"ok":true}\n</tool_response>'
        "<|im_end|>\n<|im_start|>user\n"
        "[Visual content from tool results: 1 image(s)]<image>"
    )


def test_recorder_strips_non_visible_tool_metadata(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    recorder = MultimodalTurnRecorder(
        trace_path=trace_path,
        trace_id="trace-1",
        task_id="M000",
        task_name="multimodal_test",
        model_id="qwen3-vl",
        tools=[
            ToolSpec(
                name="ReadMedia",
                description="Read a video into frames.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
        media_cfg=None,
    )

    with recorder:
        recorder.record_turn(
            turn_index=0,
            input_messages=[
                Message(role="system", content="SYSTEM PROMPT"),
                Message(role="user", content="请处理这个视频"),
                Message(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="call_1",
                            name="ReadMedia",
                            input={"path": "/workspace/地铁1.png"},
                            extra_content={"provider_only": True},
                        )
                    ],
                ),
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(
                            tool_use_id="call_1",
                            content=[TextBlock(text='{"error":"frame decode failed"}')],
                            is_error=True,
                        )
                    ],
                ),
            ],
            response=Message(role="assistant", content="收到结果"),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    rows = [
        json.loads(line)
        for line in (trace_path.parent / "_mm_turns" / "trace.turns.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["format_version"] == "mm_turn_v2"

    messages = rows[1]["messages"]
    tool_call_message = next(message for message in messages if message["role"] == "tool_call")
    tool_message = next(message for message in messages if message["role"] == "tool")

    assert tool_call_message == {
        "role": "tool_call",
        "content": '{"name": "ReadMedia", "arguments": {"path": "/workspace/地铁1.png"}}',
    }
    assert tool_message == {
        "role": "tool",
        "content": '{"error":"frame decode failed"}',
    }
    assert all("tool_call_id" not in message for message in messages)
    assert all("extra_content" not in message for message in messages)
    assert all("is_error" not in message for message in messages)
