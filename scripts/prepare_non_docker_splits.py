#!/usr/bin/env python3
"""Generate non-Docker task split directories and a manifest.

This script materializes the non-Docker subsets used by ``claw-eval batch``
into external directories of symlinks:

- ``non_docker_general/plain``
- ``non_docker_general/host_sandbox_tools``
- ``non_docker_multimodal/plain``
- ``non_docker_multi_turn/plain``
- ``non_docker_multi_turn/host_sandbox_tools``

It can also produce the stricter ``non_docker + no_web_search`` subset by
excluding tasks that structurally declare the repository's web-search tools.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tasks_non_docker_splits"
DEFAULT_OUTPUT_DIR_NON_WEB = REPO_ROOT / "tasks_non_docker_non_web"
SPLIT_ORDER = (
    "non_docker_general",
    "non_docker_multimodal",
    "non_docker_multi_turn",
)
MODE_ORDER = ("plain", "host_sandbox_tools")


def _load_module(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _iter_task_dirs(tasks_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in tasks_dir.iterdir()
        if path.is_dir() and (path / "task.yaml").exists()
    )


def _classify_split(task_dir: Path) -> str:
    task = _load_yaml(task_dir / "task.yaml")
    tags = set(task.get("tags") or [])
    user_agent_enabled = bool((task.get("user_agent") or {}).get("enabled"))

    if user_agent_enabled:
        return "non_docker_multi_turn"
    if "multimodal" in tags:
        return "non_docker_multimodal"
    if "general" in tags:
        return "non_docker_general"

    raise ValueError(
        f"unable to infer split for {task_dir.name}: expected one of "
        f"user_agent.enabled, tags=[multimodal], or tags=[general]"
    )


def _reset_output_dir(output_dir: Path, *, force: bool) -> None:
    if not output_dir.exists():
        return
    if not force:
        raise FileExistsError(
            f"{output_dir} already exists; rerun with --force to replace it"
        )
    if output_dir.is_symlink() or output_dir.is_file():
        output_dir.unlink()
    else:
        shutil.rmtree(output_dir)


def _ensure_split_dirs(output_dir: Path) -> None:
    for split in SPLIT_ORDER:
        split_dir = output_dir / split
        split_dir.mkdir(parents=True, exist_ok=True)
        for mode in MODE_ORDER:
            (split_dir / mode).mkdir(parents=True, exist_ok=True)


def _link_task(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.symlink_to(src.resolve())


def _serialise_counts(counts: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    payload: dict[str, dict[str, int]] = {}
    for split in SPLIT_ORDER:
        split_counts = counts.get(split, Counter())
        payload[split] = {
            mode: split_counts[mode]
            for mode in MODE_ORDER
            if split_counts.get(mode, 0) > 0
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare non-Docker task split directories and manifest."
    )
    parser.add_argument(
        "--tasks-dir",
        default=str(TASKS_DIR),
        help="Path to source tasks directory (default: repo tasks/).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to create for the split task symlinks.",
    )
    parser.add_argument(
        "--exclude-web-search",
        action="store_true",
        help="Exclude tasks that structurally require repository web-search tools.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output directory.",
    )
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir).resolve()
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    elif args.exclude_web_search:
        output_dir = DEFAULT_OUTPUT_DIR_NON_WEB.resolve()
    else:
        output_dir = DEFAULT_OUTPUT_DIR.resolve()

    docker_module = _load_module(
        REPO_ROOT / "tasks" / "judge_docker_task.py",
        "judge_docker_task_module",
    )
    web_module = _load_module(
        REPO_ROOT / "tasks" / "judge_web_search_task.py",
        "judge_web_search_task_module",
    )

    task_dirs = _iter_task_dirs(tasks_dir)
    workspace_dir_exists = Path("/workspace").exists()

    docker_analyses = {
        task_dir.name: docker_module.analyze_task(
            task_dir,
            workspace_dir_exists=workspace_dir_exists,
        )
        for task_dir in task_dirs
    }
    web_analyses = {
        task_dir.name: web_module.analyze_task(task_dir)
        for task_dir in task_dirs
    }

    _reset_output_dir(output_dir, force=args.force)
    output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_split_dirs(output_dir)

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    manifest_tasks: list[dict[str, str]] = []
    excluded_web_tasks: list[str] = []

    for task_dir in task_dirs:
        name = task_dir.name
        docker_analysis = docker_analyses[name]
        if not docker_analysis.runnable_without_docker:
            continue

        if args.exclude_web_search and web_analyses[name].requires_web_search:
            excluded_web_tasks.append(name)
            continue

        split = _classify_split(task_dir)
        mode = docker_analysis.mode
        if mode not in MODE_ORDER:
            raise ValueError(f"unexpected non-Docker mode for {name}: {mode}")

        dst = output_dir / split / mode / name
        _link_task(task_dir, dst)
        counts[split][mode] += 1
        manifest_tasks.append(
            {
                "task_dir": name,
                "split": split,
                "mode": mode,
            }
        )

    manifest_tasks.sort(
        key=lambda item: (
            SPLIT_ORDER.index(item["split"]),
            MODE_ORDER.index(item["mode"]),
            item["task_dir"],
        )
    )

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "tasks_dir": str(tasks_dir),
        "output_dir": str(output_dir),
        "filters": {
            "non_docker_only": True,
            "exclude_web_search": args.exclude_web_search,
        },
        "counts": _serialise_counts(counts),
        "tasks": manifest_tasks,
    }
    if args.exclude_web_search:
        manifest["excluded_web_search_tasks"] = excluded_web_tasks

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"created: {output_dir}")
    for split in SPLIT_ORDER:
        split_path = output_dir / split
        split_counts = counts.get(split, Counter())
        mode_summary = ", ".join(
            f"{mode}={split_counts[mode]}"
            for mode in MODE_ORDER
            if split_counts.get(mode, 0) > 0
        )
        if not mode_summary:
            mode_summary = "empty"
        print(f"{split}: {split_path} ({mode_summary})")
    if args.exclude_web_search:
        print(f"excluded_web_search_tasks: {len(excluded_web_tasks)}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
