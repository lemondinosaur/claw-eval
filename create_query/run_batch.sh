#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREATE_QUERY_DIR="$ROOT_DIR/create_query"
TAG_FILE="${TAG_FILE:-${tag_file:-$ROOT_DIR/judge_task/task_tags.jsonl}}"
STEP2_MODULE="${STEP2_MODULE:-${step2_module:-create_query.step2_new}}"

MODE="${1:-batch}"
WORKERS="${WORKERS:-${workers:-4}}"
WORKER="${WORKER:-${worker:-1}}"
TASK_NAME="${TASK:-${task:-}}"
ONLY_INDEX="${ONLY_INDEX:-${only_index:-}}"
OVERWRITE="${OVERWRITE:-${overwrite:-0}}"
RUN_DIR="${CREATE_QUERY_RUN_DIR:-${run_dir:-}}"
if [[ -z "$RUN_DIR" ]]; then
  RUN_TS="$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
  RUN_DIR="$CREATE_QUERY_DIR/output_${RUN_TS}"
fi
export CREATE_QUERY_RUN_DIR="$RUN_DIR"

LOG_DIR="$RUN_DIR/log"
STEP1_DIR="$RUN_DIR/tasks"
STEP2_DIR="$RUN_DIR/generated"
CANDIDATE_FILE="$RUN_DIR/candidates.general.no_network.no_real_reference.txt"
RUN_SUMMARY="$RUN_DIR/run_summary.json"

mkdir -p "$LOG_DIR" "$STEP1_DIR" "$STEP2_DIR"

write_candidates() {
  (
    cd "$ROOT_DIR"
    PYTHONPATH=src python - "$ROOT_DIR" "$TAG_FILE" <<'PY'
import sys
from pathlib import Path

from create_query.common import load_task_tags, task_in_generation_scope

repo_root = Path(sys.argv[1]).resolve()
tag_file = Path(sys.argv[2]).resolve()
task_tags = load_task_tags(tag_file)
tasks_dir = repo_root / "tasks"

selected: list[str] = []
for child in sorted(tasks_dir.iterdir()):
    if not child.is_dir() or not (child / "task.yaml").exists():
        continue
    ok, _reason = task_in_generation_scope(child, task_tags=task_tags)
    if not ok:
        continue
    selected.append(child.name)

for name in selected:
    print(name)
PY
  ) > "$CANDIDATE_FILE"
}

write_candidates

write_run_summary() {
  python - "$RUN_SUMMARY" "$RUN_DIR" "$STEP2_MODULE" "$WORKERS" "$ONLY_INDEX" "$CANDIDATE_FILE" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
run_dir = sys.argv[2]
step2_module = sys.argv[3]
workers = int(sys.argv[4])
only_index = sys.argv[5] or None
candidate_file = Path(sys.argv[6])
candidates = [line.strip() for line in candidate_file.read_text(encoding="utf-8").splitlines() if line.strip()]
obj = {
    "schema_version": "create_query.run_summary.v2",
    "run_dir": run_dir,
    "step2_module": step2_module,
    "workers": workers,
    "only_index": only_index,
    "candidate_count": len(candidates),
    "candidates": candidates,
}
summary_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

write_run_summary

count_candidates() {
  python - "$CANDIDATE_FILE" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
print(sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()))
PY
}

worker_log_prefix() {
  local worker_id="$1"
  printf "%s/worker_%02d" "$LOG_DIR" "$worker_id"
}

run_one_task() {
  local task_name="$1"
  local worker_id="$2"
  local prefix
  prefix="$(worker_log_prefix "$worker_id")"
  local log_file="${prefix}.log"
  local err_file="${prefix}.err.log"
  local fail_file="${prefix}.failed.txt"

  echo "[$(date '+%F %T')] START task=${task_name} step2_module=${STEP2_MODULE} only_index=${ONLY_INDEX:-all}" | tee -a "$log_file"

  if ! (
    cd "$ROOT_DIR" &&
    PYTHONPATH=src python -m create_query.step1 --task "$task_name"
  ) >>"$log_file" 2>>"$err_file"; then
    echo "${task_name} step1" | tee -a "$fail_file" "$log_file" >&2
    return 1
  fi

  local step2_cmd=(python -m "$STEP2_MODULE" --task "$task_name")
  if [[ -n "$ONLY_INDEX" ]]; then
    step2_cmd+=(--only-index "$ONLY_INDEX")
  fi
  if [[ "$OVERWRITE" == "1" ]]; then
    step2_cmd+=(--overwrite)
  fi

  if ! (
    cd "$ROOT_DIR" &&
    PYTHONPATH=src "${step2_cmd[@]}"
  ) >>"$log_file" 2>>"$err_file"; then
    echo "${task_name} step2" | tee -a "$fail_file" "$log_file" >&2
    return 1
  fi

  echo "[$(date '+%F %T')] DONE task=${task_name}" | tee -a "$log_file"
}

run_worker_shard() {
  local worker_id="$1"
  local prefix
  prefix="$(worker_log_prefix "$worker_id")"
  local log_file="${prefix}.log"
  local err_file="${prefix}.err.log"
  local fail_file="${prefix}.failed.txt"
  local task_file="${prefix}.tasks.txt"
  : > "$log_file"
  : > "$err_file"
  : > "$fail_file"
  : > "$task_file"

  local zero_based=$((worker_id - 1))
  local idx=0
  while IFS= read -r task_name; do
    [[ -n "$task_name" ]] || continue
    if (( idx % WORKERS == zero_based )); then
      echo "$task_name" >> "$task_file"
      if ! run_one_task "$task_name" "$worker_id"; then
        :
      fi
    fi
    idx=$((idx + 1))
  done < "$CANDIDATE_FILE"
}

print_paths() {
  echo "candidate_file: $CANDIDATE_FILE"
  echo "step1_output:   $STEP1_DIR"
  echo "step2_output:   $STEP2_DIR"
  echo "log_dir:        $LOG_DIR"
  echo "step2_module:   $STEP2_MODULE"
  echo "run_dir:        $RUN_DIR"
}

case "$MODE" in
  list)
    print_paths
    echo "candidate_count: $(count_candidates)"
    cat "$CANDIDATE_FILE"
    ;;
  one)
    if [[ -z "$TASK_NAME" ]]; then
      echo "TASK is required for mode=one" >&2
      exit 2
    fi
    print_paths
    if ! run_one_task "$TASK_NAME" "$WORKER"; then
      exit 1
    fi
    ;;
  shard)
    print_paths
    run_worker_shard "$WORKER"
    ;;
  batch)
    print_paths
    echo "candidate_count: $(count_candidates)"
    pids=()
    for worker_id in $(seq 1 "$WORKERS"); do
      (
        WORKER="$worker_id"
        run_worker_shard "$worker_id"
      ) &
      pids+=("$!")
    done

    rc=0
    for pid in "${pids[@]}"; do
      if ! wait "$pid"; then
        rc=1
      fi
    done

    python - "$RUN_SUMMARY" "$RUN_DIR" "$LOG_DIR" "$CANDIDATE_FILE" "$ONLY_INDEX" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
run_dir = Path(sys.argv[2])
log_dir = Path(sys.argv[3])
candidate_file = Path(sys.argv[4])
only_index = sys.argv[5] or None

obj = json.loads(summary_path.read_text(encoding="utf-8"))
candidates = [line.strip() for line in candidate_file.read_text(encoding="utf-8").splitlines() if line.strip()]
expected_per_source = 1 if only_index else 5
selected_index = int(only_index) if only_index else None


def selected_task_ids(source_task_id: str, bundle: dict) -> list[str]:
    output = ((bundle.get("step1") or {}).get("output") or {})
    descs = output.get("new_task_descriptions")
    if isinstance(descs, list):
        task_ids: list[str] = []
        for desc in descs:
            if not isinstance(desc, dict):
                continue
            task_id = str(desc.get("new_task_id") or "").strip()
            index = desc.get("index")
            if not task_id:
                continue
            if selected_index is not None and int(index or 0) != selected_index:
                continue
            task_ids.append(task_id)
        if task_ids:
            return task_ids
    if selected_index is not None:
        return [f"{source_task_id}_plus_{selected_index}"]
    return [f"{source_task_id}_plus_{idx}" for idx in range(1, expected_per_source + 1)]


worker_failed_invocations = 0
for path in sorted(log_dir.glob("worker_*.failed.txt")):
    worker_failed_invocations += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

step1_failed_task_ids: list[str] = []
step2_failed_source_task_ids: list[str] = []
step2_incomplete_source_task_ids: list[str] = []
missing_bundle_task_ids: list[str] = []

completed_variants = 0
failed_variants = 0
in_progress_variants = 0
not_started_variants = 0
materialized_variants = 0

for source_task_id in candidates:
    bundle_path = run_dir / "tasks" / source_task_id / "bundle.json"
    if not bundle_path.exists():
        missing_bundle_task_ids.append(source_task_id)
        step1_failed_task_ids.append(source_task_id)
        continue

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    step1 = bundle.get("step1") or {}
    step1_status = str(step1.get("status") or "not_started")
    if step1_status != "completed":
        step1_failed_task_ids.append(source_task_id)
        continue

    expected_task_ids = selected_task_ids(source_task_id, bundle)
    items = (bundle.get("step2") or {}).get("items") or []
    item_by_task_id = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            item_by_task_id[task_id] = item

    source_has_failed_variant = False
    source_has_incomplete_variant = False
    for task_id in expected_task_ids:
        item = item_by_task_id.get(task_id)
        if item is None:
            not_started_variants += 1
            source_has_incomplete_variant = True
            continue

        task_dir = str(item.get("task_dir") or "").strip()
        if task_dir and Path(task_dir).exists():
            materialized_variants += 1

        status = str(item.get("status") or "not_started").strip() or "not_started"
        if status == "completed":
            completed_variants += 1
        elif status == "failed":
            failed_variants += 1
            source_has_failed_variant = True
        elif status == "not_started":
            not_started_variants += 1
            source_has_incomplete_variant = True
        else:
            in_progress_variants += 1
            source_has_incomplete_variant = True

    if source_has_failed_variant:
        step2_failed_source_task_ids.append(source_task_id)
    elif source_has_incomplete_variant:
        step2_incomplete_source_task_ids.append(source_task_id)

source_tasks_with_failures = sorted(set(step1_failed_task_ids) | set(step2_failed_source_task_ids))
source_tasks_with_issues = sorted(set(source_tasks_with_failures) | set(step2_incomplete_source_task_ids))
expected_variants = len(candidates) * expected_per_source

obj.update(
    {
        "schema_version": "create_query.run_summary.v2",
        "worker_failed_invocations": worker_failed_invocations,
        "expected_variants": expected_variants,
        "materialized_variants": materialized_variants,
        "completed_variants": completed_variants,
        "failed_variants": failed_variants,
        "in_progress_variants": in_progress_variants,
        "not_started_variants": not_started_variants,
        "missing_variants": max(expected_variants - materialized_variants, 0),
        "step1_failed_tasks": len(step1_failed_task_ids),
        "step2_failed_source_tasks": len(step2_failed_source_task_ids),
        "step2_incomplete_source_tasks": len(step2_incomplete_source_task_ids),
        "failed_tasks": len(source_tasks_with_failures),
        "source_tasks_with_issues": len(source_tasks_with_issues),
        "step1_failed_task_ids": sorted(set(step1_failed_task_ids)),
        "step2_failed_source_task_ids": sorted(set(step2_failed_source_task_ids)),
        "step2_incomplete_source_task_ids": sorted(set(step2_incomplete_source_task_ids)),
        "missing_bundle_task_ids": sorted(set(missing_bundle_task_ids)),
    }
)
summary_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

    mapfile -t summary_counts < <(python - "$RUN_SUMMARY" <<'PY'
import json
import sys
from pathlib import Path

obj = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(obj.get("failed_tasks", 0))
print(obj.get("source_tasks_with_issues", obj.get("failed_tasks", 0)))
PY
)
    failed_count="${summary_counts[0]:-0}"
    issue_count="${summary_counts[1]:-0}"
    echo "failed_tasks: $failed_count"
    if [[ "$issue_count" != "0" ]]; then
      rc=1
    fi
    exit "$rc"
    ;;
  *)
    echo "Unsupported mode: $MODE" >&2
    echo "Usage: bash create_query/run_batch.sh [list|one|shard|batch]" >&2
    exit 2
    ;;
esac
