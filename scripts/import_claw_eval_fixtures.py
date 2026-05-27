#!/usr/bin/env python3
"""Import Claw-Eval dataset fixtures into this repository's task directories.

This script reads the split metadata from the source Claw-Eval parquet files and
copies only declared fixture files from `fixtures.tar.gz` into `tasks/<task_id>/`.
It never edits `grader.py` or `task.yaml`.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_ROOT = PROJECT_ROOT.parent / "Claw-Eval"
DEFAULT_TASKS_ROOT = PROJECT_ROOT / "tasks"
SPLITS = ("general", "multimodal", "multi_turn")
PROTECTED_FILENAMES = {"grader.py", "task.yaml"}


@dataclass(frozen=True)
class FixtureEntry:
    split: str
    task_id: str
    relative_path: str

    @property
    def archive_path(self) -> str:
        return f"{self.task_id}/{self.relative_path}"

    @property
    def target_relative_path(self) -> Path:
        return Path(self.task_id) / Path(self.relative_path)


def _load_split_rows(parquet_path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        return table.to_pylist()
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError(f"failed to read parquet with pyarrow: {parquet_path}") from exc

    try:
        import pandas as pd

        frame = pd.read_parquet(parquet_path)
        return frame.to_dict(orient="records")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "reading parquet requires `pyarrow` or `pandas`; neither is available"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"failed to read parquet with pandas: {parquet_path}") from exc


def load_fixture_entries(source_root: Path) -> list[FixtureEntry]:
    data_root = source_root / "data"
    entries: list[FixtureEntry] = []

    for split in SPLITS:
        parquet_path = data_root / f"{split}-00000-of-00001.parquet"
        if not parquet_path.exists():
            raise FileNotFoundError(f"missing parquet file: {parquet_path}")

        for row in _load_split_rows(parquet_path):
            task_id = row["task_id"]
            fixtures = row.get("fixture") or []
            for rel_path in fixtures:
                rel_path = str(rel_path)
                entry = FixtureEntry(split=split, task_id=task_id, relative_path=rel_path)
                validate_entry(entry)
                entries.append(entry)

    return entries


def validate_entry(entry: FixtureEntry) -> None:
    rel_path = Path(entry.relative_path)
    if rel_path.is_absolute():
        raise ValueError(f"{entry.task_id}: absolute fixture path is not allowed: {rel_path}")

    if ".." in rel_path.parts:
        raise ValueError(f"{entry.task_id}: parent traversal is not allowed: {rel_path}")

    if any(part in PROTECTED_FILENAMES for part in rel_path.parts):
        raise ValueError(
            f"{entry.task_id}: fixture path attempts to target protected file: {rel_path}"
        )

    if not rel_path.parts or rel_path.parts[0] != "fixtures":
        raise ValueError(
            f"{entry.task_id}: unexpected fixture root {rel_path}; expected under fixtures/"
        )


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_within(root: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"refusing to write outside target root: {target}") from exc


def summarize_dataset(entries: Iterable[FixtureEntry]) -> dict[str, int]:
    total_entries = 0
    tasks_with_fixtures: set[str] = set()
    by_split = {split: 0 for split in SPLITS}

    for entry in entries:
        total_entries += 1
        tasks_with_fixtures.add(entry.task_id)
        by_split[entry.split] += 1

    return {
        "total_fixture_files": total_entries,
        "tasks_with_fixtures": len(tasks_with_fixtures),
        **{f"{split}_fixture_files": count for split, count in by_split.items()},
    }


def sync_fixtures(
    entries: list[FixtureEntry],
    archive_path: Path,
    tasks_root: Path,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, int]:
    results = {
        "copied": 0,
        "skipped_existing": 0,
        "skipped_identical": 0,
        "missing_task_dir": 0,
        "archive_missing": 0,
        "protected_skipped": 0,
    }

    if not archive_path.exists():
        raise FileNotFoundError(f"missing archive: {archive_path}")

    with tarfile.open(archive_path, "r:gz") as archive:
        for entry in entries:
            target_task_dir = tasks_root / entry.task_id
            if not target_task_dir.exists():
                results["missing_task_dir"] += 1
                print(f"MISS_TASK {entry.task_id}")
                continue

            target_path = tasks_root / entry.target_relative_path
            ensure_within(tasks_root, target_path)

            if target_path.name in PROTECTED_FILENAMES:
                results["protected_skipped"] += 1
                print(f"SKIP_PROTECTED {target_path}")
                continue

            try:
                member = archive.getmember(entry.archive_path)
            except KeyError:
                results["archive_missing"] += 1
                print(f"MISS_ARCHIVE {entry.archive_path}")
                continue

            if not member.isfile():
                results["archive_missing"] += 1
                print(f"MISS_ARCHIVE_FILE {entry.archive_path}")
                continue

            extracted = archive.extractfile(member)
            if extracted is None:
                results["archive_missing"] += 1
                print(f"MISS_ARCHIVE_FILE {entry.archive_path}")
                continue

            data = extracted.read()
            archive_hash = sha256_bytes(data)

            if target_path.exists():
                local_hash = sha256_path(target_path)
                if local_hash == archive_hash:
                    results["skipped_identical"] += 1
                    continue
                if not overwrite:
                    results["skipped_existing"] += 1
                    print(f"SKIP_EXISTING {target_path}")
                    continue

            if dry_run:
                results["copied"] += 1
                print(f"PLAN_COPY {entry.archive_path} -> {target_path}")
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("wb") as handle:
                handle.write(data)
            shutil.copystat(archive_path, target_path, follow_symlinks=True)
            results["copied"] += 1
            print(f"COPIED {entry.archive_path} -> {target_path}")

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import fixture files from the source Claw-Eval dataset into tasks/."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=f"Path to the source Claw-Eval dataset root (default: {DEFAULT_SOURCE_ROOT})",
    )
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=DEFAULT_TASKS_ROOT,
        help=f"Path to the target tasks root (default: {DEFAULT_TASKS_ROOT})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing fixture files when contents differ.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which files would be copied without writing anything.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="Limit import to one or more task IDs. Can be passed multiple times.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    source_root = args.source_root.resolve()
    tasks_root = args.tasks_root.resolve()
    archive_path = source_root / "data" / "fixtures.tar.gz"

    if not source_root.exists():
        raise FileNotFoundError(f"source root does not exist: {source_root}")
    if not tasks_root.exists():
        raise FileNotFoundError(f"tasks root does not exist: {tasks_root}")

    entries = load_fixture_entries(source_root)
    dataset_summary = summarize_dataset(entries)

    if args.task_id:
        requested = set(args.task_id)
        entries = [entry for entry in entries if entry.task_id in requested]
        unknown = sorted(requested - {entry.task_id for entry in entries})
        for task_id in unknown:
            print(f"WARN unknown task id in dataset metadata: {task_id}", file=sys.stderr)

    print("Dataset summary:")
    for key, value in dataset_summary.items():
        print(f"  {key}: {value}")
    print(f"  selected_fixture_files: {len(entries)}")
    print(f"  mode: {'dry-run' if args.dry_run else 'write'}")
    print(f"  overwrite: {args.overwrite}")

    results = sync_fixtures(
        entries=entries,
        archive_path=archive_path,
        tasks_root=tasks_root,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    print("Sync summary:")
    for key, value in results.items():
        print(f"  {key}: {value}")

    if results["missing_task_dir"] or results["archive_missing"] or results["protected_skipped"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
