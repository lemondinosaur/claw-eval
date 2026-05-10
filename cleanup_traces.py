#!/usr/bin/env python3
"""Clean up trace directory: remove abnormal traces, keep at most N per task."""

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path


def _check_trace(path: Path) -> dict:
    """Read a trace JSONL and return status info."""
    info = {
        "path": path, "task_id": None, "has_trace_end": False, "has_grading": False,
        "error": None, "task_score": 0.0, "tool_calls": 0, "sandbox_errs": 0,
    }
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            t = ev.get("type")
            if t == "trace_start":
                info["task_id"] = ev.get("task_id", "")
            elif t == "trace_end":
                info["has_trace_end"] = True
                fm = ev.get("failure_modes", [])
                if fm:
                    info["error"] = fm[0][:80]
            elif t == "grading_result":
                info["has_grading"] = True
                info["task_score"] = ev.get("task_score", 0.0)
            elif t == "tool_dispatch":
                info["tool_calls"] += 1
                rb = ev.get("response_body", {})
                if isinstance(rb, dict) and "Connection refused" in str(rb.get("error", "")):
                    info["sandbox_errs"] += 1
    except Exception as e:
        info["error"] = str(e)[:80]

    # Infer task_id from filename if not found in content
    if not info["task_id"]:
        # filename format: {task_id}_{uuid}.jsonl
        m = re.match(r"(.+)_[0-9a-f]{8}\.jsonl$", path.name)
        if m:
            info["task_id"] = m.group(1)

    return info


def main():
    parser = argparse.ArgumentParser(description="Clean up trace directory")
    parser.add_argument("trace_dir", help="Path to trace directory")
    parser.add_argument("--keep", type=int, default=3, help="Max completed traces to keep per task (default: 3)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    parser.add_argument(
        "--drop-sandbox-failed", action="store_true",
        help="Also delete graded trials where sandbox-error rate > threshold AND task_score < score-threshold "
             "(catches trials that hit sandbox crashes but still produced a grading_result with low score)",
    )
    parser.add_argument("--sandbox-err-threshold", type=float, default=0.3,
                        help="Min ratio of tool_dispatch calls returning Connection refused (default: 0.3)")
    parser.add_argument("--score-threshold", type=float, default=0.2,
                        help="Max task_score considered as failed (default: 0.2)")
    parser.add_argument("--min-tool-calls", type=int, default=5,
                        help="Skip trials with fewer than N tool calls (default: 5)")
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir)
    if not trace_dir.exists():
        print(f"Directory not found: {trace_dir}")
        return

    jsonl_files = sorted(trace_dir.glob("*.jsonl"))
    print(f"Scanning {len(jsonl_files)} trace files in {trace_dir}\n")

    # Classify all traces
    abnormal = []  # no grading_result
    sandbox_failed = []  # graded but sandbox-crash + low score
    by_task: dict[str, list[Path]] = defaultdict(list)  # task_id -> [completed paths]

    for f in jsonl_files:
        info = _check_trace(f)
        tid = info["task_id"] or "unknown"

        if not info["has_grading"]:
            reason = "no grading_result"
            if not info["has_trace_end"]:
                reason = "no trace_end (incomplete)"
            elif info["error"]:
                reason = f"error: {info['error']}"
            abnormal.append((f, tid, reason))
            continue

        # Sandbox-crash detection (only if --drop-sandbox-failed)
        if args.drop_sandbox_failed and info["tool_calls"] >= args.min_tool_calls:
            err_rate = info["sandbox_errs"] / info["tool_calls"]
            if err_rate > args.sandbox_err_threshold and info["task_score"] < args.score_threshold:
                sandbox_failed.append((
                    f, tid,
                    f"sandbox_err={info['sandbox_errs']}/{info['tool_calls']} ({err_rate:.0%}), "
                    f"score={info['task_score']:.2f}",
                ))
                continue

        by_task[tid].append(f)

    # Report abnormal
    if abnormal:
        print(f"=== Abnormal traces to DELETE: {len(abnormal)} ===")
        for f, tid, reason in abnormal:
            print(f"  DEL {f.name}  ({tid}: {reason})")
    else:
        print("=== No abnormal traces found ===")

    # Report sandbox-failed
    if sandbox_failed:
        print(f"\n=== Sandbox-failed traces to DELETE: {len(sandbox_failed)} ===")
        for f, tid, reason in sandbox_failed:
            print(f"  DEL {f.name}  ({tid}: {reason})")
    elif args.drop_sandbox_failed:
        print("\n=== No sandbox-failed traces matched ===")

    # Report excess completed traces
    excess = []
    for tid in sorted(by_task):
        paths = by_task[tid]
        if len(paths) > args.keep:
            random.shuffle(paths)
            keep = paths[:args.keep]
            remove = paths[args.keep:]
            by_task[tid] = keep
            excess.extend((f, tid) for f in remove)

    print()
    if excess:
        print(f"=== Excess completed traces to DELETE (keeping {args.keep}/task): {len(excess)} ===")
        for f, tid in excess:
            print(f"  DEL {f.name}  ({tid}: excess)")
    else:
        print(f"=== No excess traces (all tasks have <= {args.keep} completed) ===")

    # Summary
    total_del = len(abnormal) + len(sandbox_failed) + len(excess)
    total_keep = sum(len(v) for v in by_task.values())
    print(f"\n--- Summary ---")
    print(f"  Total files:    {len(jsonl_files)}")
    print(f"  To delete:      {total_del} ({len(abnormal)} abnormal + {len(sandbox_failed)} sandbox-failed + {len(excess)} excess)")
    print(f"  To keep:        {total_keep}")
    print(f"  Tasks with completed traces: {len(by_task)}")
    for tid in sorted(by_task):
        print(f"    {tid}: {len(by_task[tid])} trace(s)")

    if total_del == 0:
        print("\nNothing to clean up.")
        return

    if args.dry_run:
        print(f"\n[dry-run] Would delete {total_del} files. Run without --dry-run to execute.")
        return

    # Delete
    deleted = 0
    for f, _, _ in abnormal:
        f.unlink()
        deleted += 1
    for f, _, _ in sandbox_failed:
        f.unlink()
        deleted += 1
    for f, _ in excess:
        f.unlink()
        deleted += 1

    print(f"\nDeleted {deleted} files.")

    # Update batch_results.json if it exists
    results_file = trace_dir / "batch_results.json"
    if results_file.exists():
        print(f"NOTE: {results_file.name} may be stale. Re-run with --continue to regenerate.")


if __name__ == "__main__":
    main()
