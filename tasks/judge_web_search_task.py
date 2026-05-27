#!/usr/bin/env python3
"""Classify tasks by whether they require web-search tooling.

This classifier is intentionally structural rather than semantic. A task is
treated as "web-search-required" when its current task definition explicitly
declares any of the repository's web-search tools or backing web services.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"

WEB_TOOL_NAMES = frozenset({"web_search", "web_fetch", "web_open"})
WEB_SERVICE_NAMES = frozenset({"web", "web_real", "web_real_injection"})


@dataclass
class TaskWebAnalysis:
    task_dir: str
    task_id: str
    requires_web_search: bool
    web_tools: list[str] = field(default_factory=list)
    web_services: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def analyze_task(task_dir: Path) -> TaskWebAnalysis:
    task = _load_yaml(task_dir / "task.yaml")
    task_id = str(task.get("task_id") or task_dir.name)

    tool_names = sorted(
        str(tool.get("name"))
        for tool in (task.get("tools") or [])
        if isinstance(tool, dict) and tool.get("name") in WEB_TOOL_NAMES
    )
    endpoint_names = sorted(
        str(endpoint.get("tool_name"))
        for endpoint in (task.get("tool_endpoints") or [])
        if isinstance(endpoint, dict) and endpoint.get("tool_name") in WEB_TOOL_NAMES
    )
    service_names = sorted(
        str(service.get("name"))
        for service in (task.get("services") or [])
        if isinstance(service, dict) and service.get("name") in WEB_SERVICE_NAMES
    )

    merged_tools = sorted(set(tool_names) | set(endpoint_names))
    requires_web_search = bool(merged_tools or service_names)

    analysis = TaskWebAnalysis(
        task_dir=task_dir.name,
        task_id=task_id,
        requires_web_search=requires_web_search,
        web_tools=merged_tools,
        web_services=service_names,
    )

    if merged_tools:
        analysis.reasons.append(
            f"task declares web tool(s): {', '.join(merged_tools)}"
        )
    if service_names:
        analysis.reasons.append(
            f"task starts web service(s): {', '.join(service_names)}"
        )
    if not analysis.reasons:
        analysis.reasons.append(
            "task.yaml declares no repository web-search tools or web search services"
        )

    return analysis


def summarize(analyses: list[TaskWebAnalysis]) -> dict[str, Any]:
    requires = [item.task_dir for item in analyses if item.requires_web_search]
    no_requires = [item.task_dir for item in analyses if not item.requires_web_search]
    return {
        "total_tasks": len(analyses),
        "web_search_required_count": len(requires),
        "no_web_search_count": len(no_requires),
        "web_search_required_tasks": requires,
        "no_web_search_tasks": no_requires,
    }


def render_text(analyses: list[TaskWebAnalysis]) -> str:
    summary = summarize(analyses)
    lines: list[str] = []
    lines.append("Task Web-Search Classification")
    lines.append(f"repo: {REPO_ROOT}")
    lines.append(f"total_tasks: {summary['total_tasks']}")
    lines.append(f"web_search_required: {summary['web_search_required_count']}")
    lines.append(f"no_web_search: {summary['no_web_search_count']}")
    lines.append("")
    lines.append("Tasks:")
    for item in analyses:
        lines.append(
            f"- {item.task_dir}: requires_web_search={str(item.requires_web_search).lower()}"
        )
        for reason in item.reasons:
            lines.append(f"    reason: {reason}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify tasks by whether they require web-search tooling."
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Analyze one task directory name, e.g. T045zh_cve_research",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "web", "no_web"],
        default="all",
        help="Filter output by web-search requirement.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format.",
    )
    args = parser.parse_args()

    if args.task:
        task_dirs = [TASKS_DIR / args.task]
    else:
        task_dirs = sorted(
            p for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task.yaml").exists()
        )

    analyses = [analyze_task(task_dir) for task_dir in task_dirs]
    if args.mode == "web":
        analyses = [item for item in analyses if item.requires_web_search]
    elif args.mode == "no_web":
        analyses = [item for item in analyses if not item.requires_web_search]

    if args.format == "json":
        payload = {
            "repo_root": str(REPO_ROOT),
            "summary": summarize(analyses),
            "tasks": [asdict(item) for item in analyses],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(render_text(analyses))


if __name__ == "__main__":
    main()
