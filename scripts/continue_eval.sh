#!/bin/bash
set -euo pipefail

# ====================================================================
# continue_eval.sh — 续跑某模型缺失/失败的任务
#
# 用法:
#   bash scripts/continue_eval.sh <model-name> <trace-dir>
#
# 示例:
#   bash scripts/continue_eval.sh claude-sonnet-4.6 traces/ep-cff11p-1777152084888610498_26-05-01-05-22
#
# 工作原理:
#   使用 --continue 模式扫描已有 trace 文件，只重跑缺失的任务。
#   只要 T/C/M 中任意一个类型缺失，就自动续跑该类型。
# ====================================================================

MODEL_NAME="${1:?Usage: $0 <model-name> <trace-dir>}"
TRACE_DIR="${2:?Usage: $0 <model-name> <trace-dir>}"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Auto-detecting missing task types in ${TRACE_DIR} ==="

T_COUNT=$(find "${TRACE_DIR}" -name "T*.jsonl" 2>/dev/null | wc -l)
C_COUNT=$(find "${TRACE_DIR}" -name "C*.jsonl" 2>/dev/null | wc -l)
M_COUNT=$(find "${TRACE_DIR}" -name "M*.jsonl" 2>/dev/null | wc -l)

echo "  Found: T=${T_COUNT}, C=${C_COUNT}, M=${M_COUNT}"

RUN_GENERAL=false
RUN_MULTITURN=false
RUN_MULTIMODAL=false

[[ $T_COUNT -gt 0 ]] && RUN_GENERAL=true
[[ $C_COUNT -gt 0 ]] && RUN_MULTITURN=true
[[ $M_COUNT -gt 0 ]] && RUN_MULTIMODAL=true

if [[ "$RUN_GENERAL" == false && "$RUN_MULTITURN" == false && "$RUN_MULTIMODAL" == false ]]; then
    echo "No existing traces found (T=0, C=0, M=0). Nothing to continue."
    exit 0
fi

echo "  Will run: general=${RUN_GENERAL}, multiturn=${RUN_MULTITURN}, multimodal=${RUN_MULTIMODAL}"
echo ""

source .venv/bin/activate

LOG_DIR="${PROJECT_DIR}/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/continue_${MODEL_NAME}_${TIMESTAMP}.log"
mkdir -p "${LOG_DIR}"

echo "=== Claw-Eval: Continue ${MODEL_NAME} ==="
echo "Trace dir: ${TRACE_DIR}"
echo "Log file:  ${LOG_FILE}"
echo ""

# Generate configs if not exist
python3 - "$MODEL_NAME" << 'PYEOF'
import json, sys
from pathlib import Path

model_name = sys.argv[1]
models = json.loads(Path("models.json").read_text())

model_info = None
base_url = api_key = None
for url, keys in models.items():
    if not url.startswith("http"):
        continue
    for key, model_map in keys.items():
        if model_name in model_map:
            model_info = model_map[model_name]
            base_url, api_key = url, key
            break
    if model_info:
        break

if not model_info:
    print(f"ERROR: Model '{model_name}' not found in models.json", file=sys.stderr)
    sys.exit(1)

judge_general = models["judge_general"]
judge_multiturn = models["judge_multiturn"]
user_agent = models["user_agent"]
serp_api_keys = models.get("serp_api_keys", [])

model_id = model_info["model_id"]
context_window = model_info.get("context_window", 262144)
input_modalities = model_info.get("input_modalities", ["text", "image"])
extra_body = model_info.get("extra_body")

def extra_body_yaml():
    if not extra_body:
        return ""
    import yaml as _yaml
    dumped = _yaml.dump({"extra_body": extra_body}, default_flow_style=False).strip()
    return "  " + dumped.replace("\n", "\n  ") + "\n"

eb_lines = extra_body_yaml()

def write_yaml(path, content):
    Path(path).write_text(content)
    print(f"  Generated: {path}")

general_path = f"config_{model_name}_general.yaml"
if not Path(general_path).exists():
    general = f"""# {model_name} — General tasks (T-prefix)
model:
  api_key: {api_key}
  base_url: {base_url}
  model_id: {model_id}
  context_window: {context_window}
{eb_lines}
judge:
  api_key: {judge_general['api_key']}
  base_url: {judge_general['base_url']}
  model_id: {judge_general['model_id']}
  enabled: true

defaults:
  trace_dir: traces
  tasks_dir: tasks
  serp_api_keys: {json.dumps(serp_api_keys)}
"""
    write_yaml(general_path, general)

multiturn_path = f"config_{model_name}_multiturn.yaml"
if not Path(multiturn_path).exists():
    multiturn = f"""# {model_name} — Multi-turn tasks (C-prefix)
model:
  api_key: {api_key}
  base_url: {base_url}
  model_id: {model_id}
  context_window: {context_window}
{eb_lines}
judge:
  api_key: {judge_multiturn['api_key']}
  base_url: {judge_multiturn['base_url']}
  model_id: {judge_multiturn['model_id']}
  enabled: true

user_agent_model:
  api_key: {user_agent['api_key']}
  base_url: {user_agent['base_url']}
  model_id: {user_agent['model_id']}

defaults:
  trace_dir: traces
  tasks_dir: tasks
  serp_api_keys: {json.dumps(serp_api_keys)}
"""
    write_yaml(multiturn_path, multiturn)

multimodal_path = f"config_{model_name}_multimodal.yaml"
if not Path(multimodal_path).exists():
    modalities_str = json.dumps(input_modalities)
    multimodal = f"""# {model_name} — Multimodal tasks (M-prefix)
model:
  api_key: {api_key}
  base_url: {base_url}
  model_id: {model_id}
  context_window: {context_window}
  input_modalities: {modalities_str}
{eb_lines}
judge:
  api_key: {judge_general['api_key']}
  base_url: {judge_general['base_url']}
  model_id: {judge_general['model_id']}
  enabled: true

defaults:
  trace_dir: traces
  tasks_dir: tasks
  serp_api_keys: {json.dumps(serp_api_keys)}
"""
    write_yaml(multimodal_path, multimodal)
PYEOF

# Clean residual containers
echo "--- Cleaning residual containers ---"
docker ps -aq --filter "ancestor=claw-eval-agent:latest" 2>/dev/null | xargs -r docker rm -f 2>/dev/null || true
echo ""

# Clean abnormal (no grading_result) traces so --continue will re-run them.
# Also drop graded trials that crashed mid-run due to sandbox issues (low score
# + high Connection-refused rate) — those would otherwise be counted as "done".
echo "--- Cleaning abnormal + sandbox-failed traces in ${TRACE_DIR} ---"
python "${PROJECT_DIR}/cleanup_traces.py" "${TRACE_DIR}" --drop-sandbox-failed
echo ""

{
    if [ "$RUN_GENERAL" = true ]; then
        echo "=== Continuing General tasks (T-prefix) ==="
        claw-eval batch --config "config_${MODEL_NAME}_general.yaml" \
            --filter T --trials 3 --parallel 6 --sandbox --port-base-offset 3000 \
            --continue "${TRACE_DIR}"
        echo ""
    fi

    if [ "$RUN_MULTITURN" = true ]; then
        echo "=== Continuing Multi-turn tasks (C-prefix) ==="
        claw-eval batch --config "config_${MODEL_NAME}_multiturn.yaml" \
            --filter C --trials 3 --parallel 6 --sandbox --port-base-offset 3000 \
            --continue "${TRACE_DIR}"
        echo ""
    fi

    if [ "$RUN_MULTIMODAL" = true ]; then
        echo "=== Continuing Multimodal tasks (M-prefix) ==="
        claw-eval batch --config "config_${MODEL_NAME}_multimodal.yaml" \
            --filter M --trials 3 --parallel 6 --sandbox --port-base-offset 3000 \
            --continue "${TRACE_DIR}"
        echo ""
    fi

    echo "=== Continue complete for ${MODEL_NAME} ==="

    echo ""
    echo "=== Score summary for ${TRACE_DIR} ==="
    python "${PROJECT_DIR}/score_summary.py" "${TRACE_DIR}" --fix
} 2>&1 | tee -a "${LOG_FILE}"
