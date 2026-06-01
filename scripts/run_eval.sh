#!/bin/bash
set -euo pipefail

# ====================================================================
# run_eval.sh — 一键评测
#
# 用法:
#   bash scripts/run_eval.sh <model-name> [--dry-run] [--only general,multiturn,multimodal]
#
# 示例:
#   bash scripts/run_eval.sh claude-sonnet-4.6
#   bash scripts/run_eval.sh claude-sonnet-4.6 --dry-run
#   bash scripts/run_eval.sh claude-sonnet-4.6 --only multimodal
#   bash scripts/run_eval.sh claude-sonnet-4.6 --only general,multiturn
# ====================================================================

MODEL_NAME="${1:?Usage: $0 <model-name> [--dry-run] [--only general,multiturn,multimodal]}"
shift

DRY_RUN=false
RUN_GENERAL=true
RUN_MULTITURN=true
RUN_MULTIMODAL=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --only)
            RUN_GENERAL=false
            RUN_MULTITURN=false
            RUN_MULTIMODAL=false
            IFS=',' read -ra PARTS <<< "${2:?--only requires a comma-separated list}"
            for part in "${PARTS[@]}"; do
                case "$part" in
                    general) RUN_GENERAL=true ;;
                    multiturn) RUN_MULTITURN=true ;;
                    multimodal) RUN_MULTIMODAL=true ;;
                    *) echo "ERROR: unknown eval type '$part'" >&2; exit 1 ;;
                esac
            done
            shift 2
            ;;
        *)
            echo "ERROR: unknown option '$1'" >&2; exit 1
            ;;
    esac
done

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
source .venv/bin/activate

echo "=== Claw-Eval: ${MODEL_NAME} ==="
echo "Project dir: ${PROJECT_DIR}"

# Setup logging
LOG_DIR="${PROJECT_DIR}/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/eval_${MODEL_NAME}_${TIMESTAMP}.log"
mkdir -p "${LOG_DIR}"
echo "Log file:  ${LOG_FILE}"

START_MARKER=$(mktemp)

# --- Step 1: Generate 3 yaml configs from models.json (skip if exists) ---
echo ""
echo "--- Generating configs for ${MODEL_NAME} ---"

python3 - "$MODEL_NAME" << 'PYEOF'
import json, sys
from pathlib import Path

model_name = sys.argv[1]
models = json.loads(Path("models.json").read_text())

# Find model in registry
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

def write_yaml(path, content):
    if Path(path).exists():
        print(f"  Skipped (exists): {path}")
        return
    Path(path).write_text(content)
    print(f"  Generated: {path}")

def extra_body_yaml():
    if not extra_body:
        return ""
    import yaml as _yaml
    dumped = _yaml.dump({"extra_body": extra_body}, default_flow_style=False).strip()
    return "  " + dumped.replace("\n", "\n  ") + "\n"

eb_lines = extra_body_yaml()

# --- General config (T-prefix tasks) ---
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
write_yaml(f"config_{model_name}_general.yaml", general)

# --- Multi-turn config (C-prefix tasks) ---
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
write_yaml(f"config_{model_name}_multiturn.yaml", multiturn)

# --- Multimodal config (M-prefix tasks) ---
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
write_yaml(f"config_{model_name}_multimodal.yaml", multimodal)

print("  Done.")
PYEOF

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "--- Dry run: configs generated, skipping evaluation ---"
    exit 0
fi

# --- Steps 2-5 piped to log file ---
{
# --- Step 2: Clean up residual containers ---
echo ""
echo "--- Cleaning residual containers ---"
CONTAINERS=$(docker ps -aq --filter "ancestor=claw-eval-agent:latest" 2>/dev/null || true)
if [ -n "$CONTAINERS" ]; then
    echo "$CONTAINERS" | xargs docker rm -f
    echo "  Cleaned $(echo "$CONTAINERS" | wc -l) container(s)"
else
    echo "  No residual containers found"
fi

# --- Step 3: Run evaluations ---
echo ""

if [ "$RUN_GENERAL" = true ]; then
    echo "=== Running General evaluation (T-prefix) ==="
    claw-eval batch --config "config_${MODEL_NAME}_general.yaml" \
        --filter T --trials 3 --parallel 6 --sandbox --port-base-offset 3000
    echo ""
fi

if [ "$RUN_MULTITURN" = true ]; then
    echo "=== Running Multi-turn evaluation (C-prefix) ==="
    claw-eval batch --config "config_${MODEL_NAME}_multiturn.yaml" \
        --filter C --trials 3 --parallel 6 --sandbox --port-base-offset 3000
    echo ""
fi

if [ "$RUN_MULTIMODAL" = true ]; then
    echo "=== Running Multimodal evaluation (M-prefix) ==="
    claw-eval batch --config "config_${MODEL_NAME}_multimodal.yaml" \
        --filter M --trials 3 --parallel 6 --sandbox --port-base-offset 3000
    echo ""
fi

echo "=== All evaluations complete for ${MODEL_NAME} ==="

# --- Step 4: Clean abnormal + sandbox-failed traces ---
echo ""
echo "=== Cleaning abnormal + sandbox-failed traces ==="
while IFS= read -r d; do
    echo "--- ${d} ---"
    python "${PROJECT_DIR}/cleanup_traces.py" "${d}" --drop-sandbox-failed
done < <(find traces -mindepth 1 -maxdepth 1 -type d -newer "$START_MARKER" | sort)
echo ""

# --- Step 5: Score summary for every trace dir created in this run ---
echo ""
echo "=== Score summaries ==="
while IFS= read -r d; do
    echo ""
    echo "--- ${d} ---"
    python "${PROJECT_DIR}/score_summary.py" "${d}" --fix
done < <(find traces -mindepth 1 -maxdepth 1 -type d -newer "$START_MARKER" | sort)
rm -f "$START_MARKER"

} 2>&1 | tee -a "${LOG_FILE}"
