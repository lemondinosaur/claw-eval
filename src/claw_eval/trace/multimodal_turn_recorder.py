"""Sidecar recorder for turn-level multimodal training traces."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from typing import IO, Any

from ..config import MediaConfig
from ..models.content import (
    AudioBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
)
from ..models.message import Message
from ..models.tool import ToolSpec
from ..models.trace import TokenUsage


def _sanitize_name(value: str, *, fallback: str) -> str:
    text = value.strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text or fallback


def _tool_spec_to_msswift(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _guess_extension(mime_type: str, source_path: str | None) -> str:
    if source_path:
        suffix = Path(source_path).suffix
        if suffix:
            return suffix.lower()
    guessed = mimetypes.guess_extension(mime_type)
    if guessed:
        return guessed
    fallback = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
        "video/mp4": ".mp4",
    }
    return fallback.get(mime_type, ".bin")


def _video_block_to_provider_text(block: VideoBlock) -> str:
    """Mirror the current OpenAI-compat provider video serialization."""
    return (
        f"[video attached: {block.source_path or 'inline'} "
        f"({block.mime_type}, base64_bytes={len(block.data) * 3 // 4})]"
    )


class MultimodalTurnRecorder:
    """Writes one training-oriented record per real model call."""

    def __init__(
        self,
        *,
        trace_path: str | Path,
        trace_id: str,
        task_id: str,
        task_name: str,
        model_id: str,
        tools: list[ToolSpec],
        media_cfg: MediaConfig | None,
    ) -> None:
        self.trace_path = Path(trace_path)
        self.trace_id = trace_id
        self.task_id = task_id
        self.task_name = task_name
        self.model_id = model_id
        self.safe_task_name = _sanitize_name(task_name, fallback=task_id)
        self.turns_dir = self.trace_path.parent / "_mm_turns"
        self.turns_path = self.turns_dir / f"{self.trace_path.stem}.turns.jsonl"
        self.assets_dir = self.turns_dir / f"{self.trace_path.stem}_assets"
        self.turns_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] | None = None
        self._image_counter = 0
        self._audio_counter = 0
        self._video_counter = 0
        self._media_key_to_relpath: dict[str, str] = {}
        self._write_jsonl({
            "type": "header",
            "format_version": "mm_turn_v2",
            "trace_id": trace_id,
            "task_id": task_id,
            "task_name": task_name,
            "model": model_id,
            "source_trace": self.trace_path.name,
            "tools": [_tool_spec_to_msswift(tool) for tool in tools],
            "media_policy": {
                "image_keep_recent_turns": media_cfg.image_keep_recent_turns if media_cfg else None,
                "max_conversation_images": media_cfg.max_conversation_images if media_cfg else None,
                "max_images_per_turn": media_cfg.max_images_per_turn if media_cfg else None,
                "tool_image_quality": media_cfg.tool_image_quality if media_cfg else None,
                "tool_image_max_dimension": media_cfg.tool_image_max_dimension if media_cfg else None,
            },
        })

    def _ensure_open(self) -> IO[str]:
        if self._fh is None or self._fh.closed:
            self._fh = open(self.turns_path, "a", encoding="utf-8")
        return self._fh

    def _write_jsonl(self, payload: dict[str, Any]) -> None:
        fh = self._ensure_open()
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        fh.flush()

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> MultimodalTurnRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_turn(
        self,
        *,
        turn_index: int,
        input_messages: list[Message],
        response: Message,
        usage: TokenUsage,
    ) -> None:
        images: list[str] = []
        audios: list[str] = []
        videos: list[str] = []
        converted_input: list[dict[str, Any]] = []
        for msg in input_messages:
            converted_input.extend(self._convert_message(msg, images=images, audios=audios, videos=videos))
        converted_output = self._convert_message(response, images=images, audios=audios, videos=videos)
        self._write_jsonl({
            "type": "turn",
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "turn_index": turn_index,
            "input_message_count": len(converted_input),
            "messages": converted_input + converted_output,
            "images": images,
            "audios": audios,
            "videos": videos,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        })

    def _convert_message(
        self,
        msg: Message,
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> list[dict[str, Any]]:
        if msg.role == "assistant":
            return self._convert_assistant_message(msg, images=images, audios=audios, videos=videos)
        if msg.role == "user":
            return self._convert_user_message(msg, images=images, audios=audios, videos=videos)
        return self._convert_regular_message(msg.role, msg.content, images=images, audios=audios, videos=videos)

    def _convert_regular_message(
        self,
        role: str,
        blocks: list[TextBlock | ImageBlock | AudioBlock | VideoBlock | ToolUseBlock | ToolResultBlock],
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> list[dict[str, Any]]:
        regular_blocks = [
            block for block in blocks if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock))
        ]
        return self._flush_regular_message(role, regular_blocks, images=images, audios=audios, videos=videos)

    def _convert_assistant_message(
        self,
        msg: Message,
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        pending: list[TextBlock | ImageBlock | AudioBlock | VideoBlock] = []
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                converted.extend(
                    self._flush_regular_message("assistant", pending, images=images, audios=audios, videos=videos)
                )
                pending = []
                tool_content = {
                    "name": block.name,
                    "arguments": block.input,
                }
                tool_message: dict[str, Any] = {
                    "role": "tool_call",
                    # Mirror the OpenAI-compat provider's runtime arguments serialization.
                    "content": json.dumps(tool_content, ensure_ascii=False),
                }
                converted.append(tool_message)
                continue
            if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock)):
                pending.append(block)
        converted.extend(
            self._flush_regular_message("assistant", pending, images=images, audios=audios, videos=videos)
        )
        return converted

    def _convert_user_message(
        self,
        msg: Message,
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        pending: list[TextBlock | ImageBlock | AudioBlock | VideoBlock] = []
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                converted.extend(
                    self._flush_regular_message("user", pending, images=images, audios=audios, videos=videos)
                )
                pending = []
                text = "\n".join(text_block.text for text_block in block.content)
                tool_message = {
                    "role": "tool",
                    "content": text,
                }
                converted.append(tool_message)
                continue
            if isinstance(block, (TextBlock, ImageBlock, AudioBlock, VideoBlock)):
                pending.append(block)
        converted.extend(
            self._flush_regular_message("user", pending, images=images, audios=audios, videos=videos)
        )
        return converted

    def _flush_regular_message(
        self,
        role: str,
        blocks: list[TextBlock | ImageBlock | AudioBlock | VideoBlock],
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> list[dict[str, Any]]:
        if not blocks:
            return []
        content = self._blocks_to_placeholder_text(blocks, images=images, audios=audios, videos=videos)
        if not content:
            return []
        return [{"role": role, "content": content}]

    def _blocks_to_placeholder_text(
        self,
        blocks: list[TextBlock | ImageBlock | AudioBlock | VideoBlock],
        *,
        images: list[str],
        audios: list[str],
        videos: list[str],
    ) -> str:
        parts: list[str] = []
        text_buffer: list[str] = []
        for block in blocks:
            if isinstance(block, TextBlock):
                text_buffer.append(block.text)
                continue
            if text_buffer:
                parts.append("\n".join(text_buffer))
                text_buffer = []
            if isinstance(block, ImageBlock):
                images.append(self._materialize_media(block))
                parts.append("<image>")
            elif isinstance(block, AudioBlock):
                audios.append(self._materialize_media(block))
                parts.append("<audio>")
            elif isinstance(block, VideoBlock):
                # The current OpenAI-compat provider does not pass native video
                # parts through; it serializes them into a text marker instead.
                # Keep the sidecar aligned with the provider-visible context.
                parts.append(_video_block_to_provider_text(block))
        if text_buffer:
            parts.append("\n".join(text_buffer))
        return "".join(parts)

    def _materialize_media(self, block: ImageBlock | AudioBlock | VideoBlock) -> str:
        raw = base64.b64decode(block.data)
        key = f"{block.type}:{block.mime_type}:{hashlib.sha256(raw).hexdigest()}"
        existing = self._media_key_to_relpath.get(key)
        if existing is not None:
            return existing
        rel_path = self._allocate_rel_path(block)
        out_path = self.turns_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        rel_str = rel_path.as_posix()
        self._media_key_to_relpath[key] = rel_str
        return rel_str

    def _allocate_rel_path(self, block: ImageBlock | AudioBlock | VideoBlock) -> Path:
        ext = _guess_extension(block.mime_type, block.source_path)
        if isinstance(block, ImageBlock):
            self._image_counter += 1
            filename = f"{self.safe_task_name}_{self._image_counter:04d}{ext}"
        elif isinstance(block, AudioBlock):
            self._audio_counter += 1
            filename = f"{self.safe_task_name}_audio_{self._audio_counter:04d}{ext}"
        else:
            self._video_counter += 1
            filename = f"{self.safe_task_name}_video_{self._video_counter:04d}{ext}"
        return Path(self.assets_dir.name) / filename
