#!/bin/bash
set -euo pipefail

# ====================================================================
# vLLM serving: Qwen3-VL-8B-Instruct
# ====================================================================

ENV_DIR="/share-new/xiongjunqi/envs/vllm_env"
MODEL_PATH="/share-new/xiongjunqi/Qwen3-VL-8B-Instruct"
HOST="0.0.0.0"
PORT=8128

# Activate environment
source "${ENV_DIR}/bin/activate"

echo "=== Starting vLLM server ==="
echo "Model:  ${MODEL_PATH}"
echo "Listen: http://${HOST}:${PORT}"
echo "GPUs:   ${CUDA_VISIBLE_DEVICES:-all}"
echo ""

python -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --tensor-parallel-size 8 \
    --trust-remote-code \
    --max-model-len 262144 \
    --dtype auto \
    --gpu-memory-utilization 0.9 \
    --served-model-name qwen3-vl-8b-instruct
