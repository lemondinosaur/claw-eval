# Non-Docker / Non-Web Execution Guide

这份文档只对应当前仓库：

`/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval`

目标是把当前仓库里的 `tasks/` 严谨地拆成两类外提目录，并完成：

- 非 Docker 任务评测
- 非 Docker 且非 Web-Search 任务评测
- trace 清洗
- trace 转 Qwen3VL / ms-swift 训练数据

本文所有结论都基于当前仓库中的真实代码与一次实际生成结果，不依赖旧仓库目录。

## 1. 当前目录约定

当前只保留两套标准拆分目录：

- `tasks_non_docker_splits`
- `tasks_non_docker_non_web`

其中：

- `tasks_non_docker_splits` 表示全部 `non_docker`
- `tasks_non_docker_non_web` 表示 `non_docker` 再排除结构上依赖 web-search 的任务

已明确删除旧的重复目录名：

- `tasks_non_docker_no_web_splits`

如果后续重新生成 no-web 目录，脚本默认也只会生成：

- `tasks_non_docker_non_web`

## 2. 数据来源与严谨性检查

### 2.1 生成脚本

当前实际使用的脚本与分类器：

- [scripts/prepare_non_docker_splits.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/scripts/prepare_non_docker_splits.py)
- [tasks/judge_docker_task.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/judge_docker_task.py)
- [tasks/judge_web_search_task.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/judge_web_search_task.py)

### 2.2 生成来源确认

两套拆分目录的 `manifest.json` 已确认：

- `repo_root = /ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval`
- `tasks_dir = /ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks`

也就是说，新构建结果明确来源于当前仓库的：

- `claw-eval/tasks`

而不是其他仓库中的 `tasks`。

### 2.3 symlink 目标确认

我已逐项检查两套拆分目录中的所有 task 软链接：

- `tasks_non_docker_splits`: `199` 个 task link，`0` 个越界目标
- `tasks_non_docker_non_web`: `135` 个 task link，`0` 个越界目标

所有软链接目标都位于：

- `/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks`

因此可以认为拆分结果与当前仓库任务集严格一致。

## 3. 当前统计结果

### 3.1 `non_docker`

来自 `tasks_non_docker_splits/manifest.json`：

- `non_docker_general`: `plain=152`, `host_sandbox_tools=4`
- `non_docker_multimodal`: `plain=5`
- `non_docker_multi_turn`: `plain=2`, `host_sandbox_tools=36`
- 合计任务数：`199`

### 3.2 `non_docker_non_web`

来自 `tasks_non_docker_non_web/manifest.json`：

- `non_docker_general`: `plain=126`, `host_sandbox_tools=3`
- `non_docker_multimodal`: `plain=3`
- `non_docker_multi_turn`: `plain=2`, `host_sandbox_tools=1`
- 合计任务数：`135`
- 被排除的 web-search 任务数：`64`

### 3.3 自洽性检查

当前总任务数结构上满足：

- 当前仓库 `tasks/` 共 `300` 个任务
- `docker_required = 101`
- `non_docker = 199`
- `web_search_required = 65`
- `non_docker_non_web = 135`

并且：

- `199 + 101 = 300`
- `199 - 64 = 135`

这里的 `64` 是“同时满足 non-docker 且结构上依赖 web-search”的任务数。

## 4. 拆分规则

### 4.1 Docker 分类

`tasks/judge_docker_task.py` 会把任务分成：

- `plain`
- `host_sandbox_tools`
- `docker_required`

其中：

- `plain`: 不需要 Docker，不需要 `--sandbox-tools`
- `host_sandbox_tools`: 不需要 Docker，但需要 `--sandbox-tools`
- `docker_required`: 当前代码路径下必须走 `--sandbox`

`scripts/prepare_non_docker_splits.py` 只会抽取前两类。

### 4.2 Web-Search 分类

`tasks/judge_web_search_task.py` 会结构化判断任务是否显式声明：

- `web_search`
- `web_fetch`
- `web_open`

以及相关 web service：

- `web`
- `web_real`
- `web_real_injection`

### 4.3 split 归类规则

split 映射来自任务定义本身：

- `user_agent.enabled = true` -> `non_docker_multi_turn`
- `tags` 含 `multimodal` -> `non_docker_multimodal`
- `tags` 含 `general` -> `non_docker_general`

## 5. 环境准备

### 5.1 安装依赖

```bash
uv sync --extra mock
mkdir -p logs traces
```

后续命令默认都在仓库根目录执行，并显式带：

```bash
PYTHONPATH=src
```

### 5.2 模型与 judge 配置

当前相关配置文件：

- [config_qwen3vl_non_docker_general.yaml](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/config_qwen3vl_non_docker_general.yaml)
- [config_qwen3vl_non_docker_multimodal.yaml](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/config_qwen3vl_non_docker_multimodal.yaml)
- [config_qwen3vl_non_docker_multi_turn.yaml](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/config_qwen3vl_non_docker_multi_turn.yaml)

正式执行前建议先检查：

- `model.base_url`
- `model.model_id`
- `judge.enabled`
- `user_agent_model`

如果只是为了采样 trace，不做打分，可以在命令里加：

- `--no-judge`

### 5.3 trace 命名规范

建议统一采用：

- `traces/<TASK_TYPE>/<RUN_NAME>`
- `logs/<TASK_TYPE>/<RUN_NAME>.log`

其中：

- `TASK_TYPE` 表示子任务桶
- `RUN_NAME` 统一用 `模型_trials=次数_时间戳`

例如：

```bash
RUN_NAME="qwen3_vl_trials=3_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
```

## 6. 重新生成拆分目录

### 6.1 生成 `non_docker`

```bash
PYTHONPATH=src uv run --extra mock python scripts/prepare_non_docker_splits.py \
  --force
```

默认输出：

- `tasks_non_docker_splits`

### 6.2 生成 `non_docker_non_web`

```bash
PYTHONPATH=src uv run --extra mock python scripts/prepare_non_docker_splits.py \
  --exclude-web-search \
  --force
```

默认输出：

- `tasks_non_docker_non_web`

### 6.3 生成后必须做的校验

先看 manifest：

```bash
python - <<'PY'
from pathlib import Path
import json
for name in ['tasks_non_docker_splits', 'tasks_non_docker_non_web']:
    p = Path(name) / 'manifest.json'
    d = json.loads(p.read_text(encoding='utf-8'))
    print(name)
    print('repo_root =', d['repo_root'])
    print('tasks_dir =', d['tasks_dir'])
    print('output_dir =', d['output_dir'])
    print('counts =', d['counts'])
    print('num_tasks =', len(d['tasks']))
    print()
PY
```

再检查软链接是否都指向当前仓库 `tasks/`：

```bash
python - <<'PY'
from pathlib import Path
import json
root = Path.cwd().resolve()
tasks_root = root / 'tasks'
for split_root_name in ['tasks_non_docker_splits', 'tasks_non_docker_non_web']:
    split_root = root / split_root_name
    manifest = json.loads((split_root / 'manifest.json').read_text(encoding='utf-8'))
    links = [p for p in split_root.rglob('*') if p.is_symlink()]
    bad = []
    for p in links:
        try:
            p.resolve().relative_to(tasks_root)
        except ValueError:
            bad.append((str(p), str(p.resolve())))
    print(split_root_name, 'symlinks=', len(links), 'manifest=', len(manifest['tasks']), 'bad_targets=', len(bad))
PY
```

只有当：

- `repo_root` 正确
- `tasks_dir` 正确
- `symlink_count == manifest task count`
- `bad_targets == 0`

才说明这次拆分结果可信。

## 7. 为什么 `plain` 和 `host_sandbox_tools` 不能混跑

不能把它们放在同一条 `claw-eval batch` 命令里。

原因：

- `--sandbox-tools` 是 batch 级全局开关
- 一旦开启，这一批中所有任务都会拿到 host sandbox tools

因此必须拆开跑：

- `plain` 一批
- `host_sandbox_tools` 一批

如果要并行压测，必须保证：

- 不同批次使用不同 `--port-base-offset`
- 不同批次使用不同 `--trace-dir`
- 不同批次使用不同日志路径

## 8. 运行 `non_docker`

### 8.1 `general/plain`

```bash
TASK_TYPE=non_docker_general_plain
TRIALS=1
MODEL_NAME=qwen3_vl_v30
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_general/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 0 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 8.2 `general/host_sandbox_tools`

```bash
TASK_TYPE=non_docker_general_host_sandbox_tools
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_general/host_sandbox_tools \
  --sandbox-tools \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 2000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 8.3 `multimodal/plain`

```bash
TASK_TYPE=non_docker_multimodal_plain
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multimodal.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_multimodal/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 4000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 8.4 `multi_turn/plain`

```bash
TASK_TYPE=non_docker_multi_turn_plain
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multi_turn.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_multi_turn/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 6000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 8.5 `multi_turn/host_sandbox_tools`

```bash
TASK_TYPE=non_docker_multi_turn_host_sandbox_tools
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multi_turn.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_multi_turn/host_sandbox_tools \
  --sandbox-tools \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 8000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

## 9. 运行 `non_docker_non_web`

这个子集已经结构化剔除了 web-search 依赖任务，因此不应再要求 Web 搜索相关 key。

### 9.1 `general/plain`

```bash
TASK_TYPE=non_docker_non_web_general_plain
TRIALS=1
MODEL_NAME=qwen3_vl_v30
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_non_web/non_docker_general/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 10000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 9.2 `general/host_sandbox_tools`

```bash
TASK_TYPE=non_docker_non_web_general_host_sandbox_tools
TRIALS=1
MODEL_NAME=qwen3_vl_v30
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_non_web/non_docker_general/host_sandbox_tools \
  --sandbox-tools \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 12000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 9.3 `multimodal/plain`

```bash
TASK_TYPE=non_docker_non_web_multimodal_plain
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multimodal.yaml \
  --tasks-dir tasks_non_docker_non_web/non_docker_multimodal/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 14000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 9.4 `multi_turn/plain`

```bash
TASK_TYPE=non_docker_non_web_multi_turn_plain
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multi_turn.yaml \
  --tasks-dir tasks_non_docker_non_web/non_docker_multi_turn/plain \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 16000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

### 9.5 `multi_turn/host_sandbox_tools`

```bash
TASK_TYPE=non_docker_non_web_multi_turn_host_sandbox_tools
TRIALS=3
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "logs/${TASK_TYPE}" "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_multi_turn.yaml \
  --tasks-dir tasks_non_docker_non_web/non_docker_multi_turn/host_sandbox_tools \
  --sandbox-tools \
  --parallel 4 \
  --trials "${TRIALS}" \
  --port-base-offset 18000 \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}" \
  2>&1 | tee "logs/${TASK_TYPE}/${RUN_NAME}.log"
```

## 10. smoke test 与无 judge 推理

正式批量跑之前，建议每种 mode 先做单任务 smoke test。

### 10.1 `plain` smoke test

```bash
TASK_TYPE=smoke_general_plain
TRIALS=1
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli run \
  --config config_qwen3vl_non_docker_general.yaml \
  --task tasks_non_docker_splits/non_docker_general/plain/T002_email_triage \
  --trials "${TRIALS}" \
  --no-judge \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}"
```

### 10.2 `host_sandbox_tools` smoke test

```bash
TASK_TYPE=smoke_general_host_sandbox_tools
TRIALS=1
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli run \
  --config config_qwen3vl_non_docker_general.yaml \
  --task tasks_non_docker_splits/non_docker_general/host_sandbox_tools/T097_pinbench_eli5_model_summary \
  --sandbox-tools \
  --trials "${TRIALS}" \
  --no-judge \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}"
```

### 10.3 `non_web multi_turn host_sandbox_tools` smoke test

```bash
TASK_TYPE=smoke_non_web_multi_turn_host_sandbox_tools
TRIALS=1
MODEL_NAME=qwen3_vl
RUN_NAME="${MODEL_NAME}_trials=${TRIALS}_$(TZ=Asia/Shanghai date '+%Y%m%d_%H%M%S')"
mkdir -p "traces/${TASK_TYPE}/${RUN_NAME}"

PYTHONPATH=src uv run --extra mock python -m claw_eval.cli run \
  --config config_qwen3vl_non_docker_multi_turn.yaml \
  --task tasks_non_docker_non_web/non_docker_multi_turn/host_sandbox_tools/C04zh_image_processing \
  --sandbox-tools \
  --trials "${TRIALS}" \
  --no-judge \
  --trace-dir "traces/${TASK_TYPE}/${RUN_NAME}"
```

## 11. trace 完整性检查与清洗

评测后，先检查输出目录中是否存在异常 trace。

使用：

- [cleanup_traces.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/cleanup_traces.py)

先 dry-run：

```bash
python cleanup_traces.py traces/non_docker_general_plain/<RUN_NAME> --dry-run
```

如果是带 sandbox tools 的批次，建议额外识别 sandbox crash：

```bash
python cleanup_traces.py traces/non_docker_general_host_sandbox_tools/<RUN_NAME> \
  --dry-run \
  --drop-sandbox-failed
```

确认后再执行实际清理：

```bash
python cleanup_traces.py traces/non_docker_general_host_sandbox_tools/<RUN_NAME> \
  --drop-sandbox-failed
```

清洗后建议重新跑 `--continue` 或 `--rerun-errors` 补齐 trial 数。

## 12. 续跑与重跑

### 12.1 继续已有批次

```bash
PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_general/plain \
  --parallel 4 \
  --trials 3 \
  --continue traces/non_docker_general_plain/<RUN_NAME>
```

### 12.2 只重跑错误任务

```bash
PYTHONPATH=src uv run --extra mock python -m claw_eval.cli batch \
  --config config_qwen3vl_non_docker_general.yaml \
  --tasks-dir tasks_non_docker_splits/non_docker_general/plain \
  --parallel 4 \
  --trials 3 \
  --rerun-errors traces/non_docker_general_plain/<RUN_NAME>
```

## 13. 汇总检查

如果要对某个 traces 根目录做汇总，使用：

- [score_summary.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/score_summary.py)

例如：

```bash
python score_summary.py traces
```

这个脚本会按目录结构提取最新 trace 目录，并汇总：

- 平均分
- `all_pass`
- `any_pass`
- 异常 trial

## 14. trace 转训练数据

### 14.1 当前 trace 格式说明

当前仓库的新 trace 已额外记录：

- `tools_snapshot`
- `system_prompt`

这样做的目的：

- 保留当时真实使用的工具集合
- 保留当时真实渲染出的 system prompt
- 不改变 grader 消费的消息流结构

### 14.2 转换脚本

使用：

- [train_dataset/trace_dir_to_msswift.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/train_dataset/trace_dir_to_msswift.py)

示例：

```bash
python train_dataset/trace_dir_to_msswift.py \
  --trace-dir traces/non_docker_non_web_general_plain/<RUN_NAME> \
  --output-jsonl train_dataset/non_docker_non_web_general_plain.jsonl
```

如果要严格模式，遇到坏样本立即中止：

```bash
python train_dataset/trace_dir_to_msswift.py \
  --trace-dir traces/non_docker_non_web_general_plain/<RUN_NAME> \
  --output-jsonl train_dataset/non_docker_non_web_general_plain.jsonl \
  --strict
```

### 14.3 转换前的严谨性建议

正式转训练数据前，建议至少确认：

- trace 目录里没有明显 incomplete 文件
- `cleanup_traces.py --dry-run` 不再报异常
- 每个任务的 trial 数符合你的采样预期
- 如果你需要 system prompt，一定优先使用新 trace

## 15. 推荐执行顺序

建议严格按下面顺序执行：

1. 检查 3 个 config 中的 `model.base_url`、`model.model_id`、judge 设置。
2. 运行一次 `prepare_non_docker_splits.py --force` 生成 `tasks_non_docker_splits`。
3. 运行一次 `prepare_non_docker_splits.py --exclude-web-search --force` 生成 `tasks_non_docker_non_web`。
4. 对两套 split 都执行 manifest 与 symlink 校验。
5. 每种 mode 先跑一个 `run --no-judge` smoke test。
6. 再跑正式 `batch --trials 3`。
7. 批次完成后先用 `cleanup_traces.py --dry-run` 做完整性检查。
8. 如有异常，用 `cleanup_traces.py` 清理，再 `--continue` / `--rerun-errors` 补齐。
9. 用 `score_summary.py` 做整体检查。
10. 最后再用 `train_dataset/trace_dir_to_msswift.py` 转训练数据。

## 16. 本次修改后的最终结论

当前仓库中：

- `tasks_non_docker_non_web` 是唯一保留的 no-web 标准目录
- `tasks_non_docker_no_web_splits` 已删除
- 新构建结果明确来自当前仓库的 `tasks/`
- trace 已支持单独记录 `system_prompt` 与 `tools_snapshot`
- trace 转训练数据脚本已迁入并可工作

## 17. create_query 造题流程

`create_query/` 里是新的单轮造题流水线，专门从 `tasks/` 生成新题。

### 17.1 配置

先设置模型接口：

```bash
export CREATE_QUERY_BASE_URL=""
export CREATE_QUERY_API_KEY=""
export CREATE_QUERY_MODEL=""
```

### 17.2 Step 1

生成 5 条新题描述：

```bash
PYTHONPATH=src python -m create_query.step1 --task T001zh_email_triage
```

批量跑全部可用单轮任务：

```bash
PYTHONPATH=src python -m create_query.step1 --all
```

输出在：

- `create_query/step1_output/<TASK_ID>.json`

### 17.3 Step 2

根据 Step 1 的描述生成完整任务目录：

```bash
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage
```

默认会顺序生成 `plus_1` 到 `plus_5` 五条具体 task。

只生成某一个 `plus_i`：

```bash
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage --only-index 1
```

输出分两层：

- 中间文件：`create_query/output_<TS>/tasks/<SOURCE_TASK_ID>/step2/<NEW_TASK_ID>/`
- 最终目录：`create_query/output_<TS>/generated/<NEW_TASK_ID>/`
- 总清单：`create_query/output_<TS>/tasks/<SOURCE_TASK_ID>/step2/manifest.json`
- 聚合状态：`create_query/output_<TS>/tasks/<SOURCE_TASK_ID>/bundle.json`

### 17.4 说明

- `user_agent.enabled: true` 的多轮任务会自动跳过
- fixtures 只接受 `.json` / `.txt`
- `step2_new.py` 不生成 `grader.py`，新的 `task.yaml` 也不包含打分相关字段
- 每次运行会创建一个共享的北京时间戳目录 `output_<TS>`，同一 batch 的 step1/step2/log 全都落到这个目录下
- 如果某个任务失败，会记录错误并继续后续任务，不会阻塞整个 batch
- 中间态保留为紧凑 JSON，不会直接展开成最终任务树
- 最终展开目录统一放在 `generated/`，方便清理和复查
