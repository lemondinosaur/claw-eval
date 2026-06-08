#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval
MS_SWIFT_ROOT=/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/ms-swift
MS_SWIFT_PYTHON=/ytech_m2v5_hdd/workspace/kling_mm/wangzekun/miniconda3/envs/msswift/bin/python
SWIFT_BIN=/ytech_m2v5_hdd/workspace/kling_mm/wangzekun/miniconda3/envs/msswift/bin/swift

TRACE_DIR=${TRACE_DIR:-${REPO_ROOT}/traces/pa_gemini-3.1-pro-preview_26-06-07-18-25/_mm_turns}
DATASET_DIR=${DATASET_DIR:-${REPO_ROOT}/train_dataset}
DATASET_TAG=${DATASET_TAG:-pa_gemini_3_1_pro_preview_26_06_07_18_25}
RAW_DATASET=${DATASET_DIR}/${DATASET_TAG}.turn.mm_swift_aligned.msswift.jsonl

MODEL_PATH=/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/model_list/Qwen3-VL-8B-Instruct
OUTPUT_ROOT=/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/output
RUN_NAME=${RUN_NAME:-qwen3_vl_8b_claw_turn_32k_img256_8gpu}
OUTPUT_DIR=${OUTPUT_ROOT}/${RUN_NAME}

CUDA_DEVICES=${CUDA_DEVICES:-0,1,2,3,4,5,6,7}
NPROC=${NPROC:-8}
MASTER_PORT=${MASTER_PORT:-29501}
DATA_SEED=${DATA_SEED:-42}
REBUILD_DATASET=${REBUILD_DATASET:-0}

MAX_LENGTH=32768
IMAGE_TOKEN_BUDGET=256
VIDEO_TOKEN_BUDGET=256
PATCH_FACTOR=32
MAX_PIXELS=$((IMAGE_TOKEN_BUDGET * PATCH_FACTOR * PATCH_FACTOR))
FILTERED_DATASET=${DATASET_DIR}/${DATASET_TAG}.turn.mm_swift_aligned.maxlen${MAX_LENGTH}.img${IMAGE_TOKEN_BUDGET}.vid${VIDEO_TOKEN_BUDGET}.filtered.msswift.jsonl

SEQUENCE_PARALLEL_SIZE=${SEQUENCE_PARALLEL_SIZE:-1}

mkdir -p "${DATASET_DIR}" "${OUTPUT_DIR}"

if [[ ! -d "${TRACE_DIR}" ]]; then
    echo "TRACE_DIR does not exist: ${TRACE_DIR}" >&2
    exit 1
fi

if [[ ! -x "${MS_SWIFT_PYTHON}" ]]; then
    echo "MS_SWIFT_PYTHON does not exist: ${MS_SWIFT_PYTHON}" >&2
    exit 1
fi

if [[ ! -x "${SWIFT_BIN}" ]]; then
    echo "SWIFT_BIN does not exist: ${SWIFT_BIN}" >&2
    exit 1
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
    echo "MODEL_PATH does not exist: ${MODEL_PATH}" >&2
    exit 1
fi

cd "${MS_SWIFT_ROOT}"

export MS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONPATH="${MS_SWIFT_ROOT}:${PYTHONPATH:-}"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:128
export MODEL_SEQ_LEN="${MAX_LENGTH}"
export IMAGE_MAX_TOKEN_NUM="${IMAGE_TOKEN_BUDGET}"
export VIDEO_MAX_TOKEN_NUM="${VIDEO_TOKEN_BUDGET}"

if [[ "${REBUILD_DATASET}" == "1" || ! -f "${RAW_DATASET}" ]]; then
    "${MS_SWIFT_PYTHON}" "${REPO_ROOT}/train_dataset/trace_dir_to_msswift_multimodal.py" \
        --trace-dir "${TRACE_DIR}" \
        --output-jsonl "${RAW_DATASET}"
fi

if [[ "${REBUILD_DATASET}" == "1" || ! -f "${FILTERED_DATASET}" || "${RAW_DATASET}" -nt "${FILTERED_DATASET}" ]]; then
    "${MS_SWIFT_PYTHON}" "${REPO_ROOT}/train_dataset/filter_msswift_by_length.py" \
        --input-jsonl "${RAW_DATASET}" \
        --output-jsonl "${FILTERED_DATASET}" \
        --model "${MODEL_PATH}" \
        --max-length "${MAX_LENGTH}" \
        --max-pixels "${MAX_PIXELS}" \
        --image-max-token-num "${IMAGE_TOKEN_BUDGET}" \
        --video-max-token-num "${VIDEO_TOKEN_BUDGET}" \
        --agent-template hermes \
        --template-backend swift \
        --overwrite
fi

echo "Trace dir: ${TRACE_DIR}"
echo "Raw dataset: ${RAW_DATASET}"
echo "Training dataset: ${FILTERED_DATASET}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Shuffle: ms-swift trainer dataloader shuffle enabled with data_seed=${DATA_SEED}"
echo "Context: max_length=${MAX_LENGTH}"
echo "Vision budget: image/video frame max token=${IMAGE_TOKEN_BUDGET}, max_pixels=${MAX_PIXELS}"

CUDA_VISIBLE_DEVICES="${CUDA_DEVICES}" \
NPROC_PER_NODE="${NPROC}" \
MASTER_PORT="${MASTER_PORT}" \
"${SWIFT_BIN}" sft \
    --model "${MODEL_PATH}" \
    --dataset "${FILTERED_DATASET}" \
    --agent_template hermes \
    --template_backend swift \
    --use_chat_template true \
    --load_from_cache_file true \
    --split_dataset_ratio 0 \
    --dataset_shuffle true \
    --data_seed "${DATA_SEED}" \
    --strict true \
    --train_type full \
    --torch_dtype bfloat16 \
    --num_train_epochs 1 \
    --per_device_train_batch_size 1 \
    --per_device_eval_batch_size 1 \
    --learning_rate 2e-5 \
    --gradient_accumulation_steps 1 \
    --gradient_checkpointing true \
    --vit_gradient_checkpointing false \
    --attn_impl flash_attn \
    --padding_free true \
    --sequence_parallel_size "${SEQUENCE_PARALLEL_SIZE}" \
    --eval_strategy no \
    --save_strategy steps \
    --save_steps 50 \
    --save_total_limit 3 \
    --save_only_model true \
    --logging_steps 5 \
    --max_length 40000 \
    --max_pixels "${MAX_PIXELS}" \
    --warmup_ratio 0.05 \
    --dataset_num_proc 8 \
    --dataloader_num_workers 8 \
    --deepspeed zero2 \
    --output_dir "${OUTPUT_DIR}" \
    --report_to tensorboard
