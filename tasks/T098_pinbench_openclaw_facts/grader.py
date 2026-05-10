from __future__ import annotations

import re
from typing import Any

from claw_eval.graders.base import AbstractGrader
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import DimensionScores, MediaLoad, ToolDispatch, TraceMessage


class PinbenchOpenClawFactsGrader(AbstractGrader):
    def grade(
        self,
        messages: list[TraceMessage],
        dispatches: list[ToolDispatch],
        task: TaskDefinition,
        audit_data: dict[str, dict] | None = None,
        judge: Any | None = None,
        media_events: list[MediaLoad] | None = None,
        env_snapshot: dict | None = None,
    ) -> DimensionScores:
        scores = DimensionScores(safety=1.0)
        text = self._get_final_assistant_text(messages).strip()
        lines = [line.replace(",", "") for line in text.splitlines() if line.strip()]
        tool_used = any(d.tool_name == "documents_extract_text" for d in dispatches if d.response_status < 400)

        def _find(keyword, extra=None):
            for line in lines:
                low = line.lower()
                if keyword in low:
                    if extra is None or extra in low:
                        return True
            return False

        checks = [
            tool_used,
            _find("5705"),
            _find("2999"),
            _find("287", "ai"),
            _find("253", "search"),
            _find("skill.md"),
            any("websocket" in l.lower() and "typed" in l.lower() for l in lines),
            any(bool(re.search(r"feb.*7.*2026|2026.*02.*07", l.lower())) for l in lines),
            any(l.strip() == "6" or l.strip().endswith(": 6") or l.strip().endswith("is 6") for l in lines),
        ]
        scores.completion = round(sum(checks) / len(checks), 2)
        scores.robustness = 1.0
        scores.efficiency_turns = len([m for m in messages if m.message.role == "assistant"])
        return scores
