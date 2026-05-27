# create_query 执行说明

这份文档只描述当前有效路径：

`step1.py -> step2_new.py`

`step2.py` 可以视为旧版，不是默认 batch 入口。

## 1. 先记住两个事实

1. 最小输入单元是 `tasks/<SOURCE_TASK_ID>/`，不是单个 fixture 文件。
2. `step2_new.py` 生成的最终目录不包含 `grader.py`，只保留运行时 `task.yaml + fixtures/...`。

## 2. 环境准备

在仓库根目录执行：

```bash
cd /ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval
export CREATE_QUERY_BASE_URL="https://app.ppapi.ai/v1"
export CREATE_QUERY_API_KEY="<YOUR_API_KEY>"
export CREATE_QUERY_MODEL="deepseek-v4-flash"
```

脚本走的是 OpenAI-compatible `/chat/completions` 接口。

## 3. 预期输出结构

每次运行会落到一个 run 目录，默认名是 `create_query/output_<北京时间戳>/`。

```text
create_query/output_<TS>/
  candidates.general.no_network.no_real_reference.txt
  run_summary.json
  log/
    worker_XX.log
    worker_XX.err.log
    worker_XX.failed.txt
    worker_XX.tasks.txt
  tasks/
    <SOURCE_TASK_ID>/
      bundle.json
      step1/
        prompt.txt
        raw.txt
        output.json
      step2/
        manifest.json
        <NEW_TASK_ID>/
          prompt.txt
          raw.txt
          parsed.json
  generated/
    <NEW_TASK_ID>/
      task.yaml
      fixtures/...
```

各层含义：

- `step1/output.json`: 固定 5 条 `new_task_descriptions`
- `bundle.json`: 单个源任务的总状态，Step1/Step2 都写这里
- `step2/<NEW_TASK_ID>/parsed.json`: Step2 模型输出的解析结果
- `generated/<NEW_TASK_ID>/`: 最终可运行任务目录
- `manifest.json`: 这个源任务实际生成了哪些 `plus_i`

最终 `generated/<NEW_TASK_ID>/` 还有几个硬约束：

- 一定有 `task.yaml`
- 一定有与源任务完全一致的 `fixtures/...` 相对路径集合
- 不会生成 `grader.py`
- `task.yaml` 会复用源任务的 `services`、`tools`、`tool_endpoints`、`sandbox_files`、`environment`
- `task.yaml` 会去掉 judge/scoring 相关字段

## 4. 单任务执行

最简单的单任务方式：

```bash
PYTHONPATH=src python -m create_query.step1 --task T001zh_email_triage
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage
```

说明：

- 第一句会生成 5 条新题描述
- 第二句默认把 `plus_1` 到 `plus_5` 全部展开
- `step2_new --task ...` 会优先去找这个任务最近一次 run 里的 Step1 结果

如果你只想生成一个最终题：

```bash
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage --only-index 1
```

这时只会生成：

- `T001zh_email_triage_plus_1`

## 5. 分步调试执行

调试时建议显式固定同一个 `RUN_DIR`，不要依赖“自动找最近一次”。

```bash
export RUN_DIR="$PWD/create_query/output_debug_T001"
```

### 5.1 只看 Step1 prompt

```bash
PYTHONPATH=src python -m create_query.step1 \
  --task T001zh_email_triage \
  --output-dir "$RUN_DIR" \
  --dry-run
```

检查：

- `$RUN_DIR/tasks/T001zh_email_triage/step1/prompt.txt`

### 5.2 真正跑 Step1

```bash
PYTHONPATH=src python -m create_query.step1 \
  --task T001zh_email_triage \
  --output-dir "$RUN_DIR"
```

检查：

- `$RUN_DIR/tasks/T001zh_email_triage/step1/raw.txt`
- `$RUN_DIR/tasks/T001zh_email_triage/step1/output.json`
- `$RUN_DIR/tasks/T001zh_email_triage/bundle.json`

### 5.3 只看 Step2 某个 `plus_i` 的 prompt

```bash
PYTHONPATH=src python -m create_query.step2_new \
  --task T001zh_email_triage \
  --output-dir "$RUN_DIR" \
  --only-index 1 \
  --dry-run
```

检查：

- `$RUN_DIR/tasks/T001zh_email_triage/step2/T001zh_email_triage_plus_1/prompt.txt`

### 5.4 真正跑 Step2 某个 `plus_i`

```bash
PYTHONPATH=src python -m create_query.step2_new \
  --task T001zh_email_triage \
  --output-dir "$RUN_DIR" \
  --only-index 1
```

检查：

- `$RUN_DIR/tasks/T001zh_email_triage/step2/T001zh_email_triage_plus_1/raw.txt`
- `$RUN_DIR/tasks/T001zh_email_triage/step2/T001zh_email_triage_plus_1/parsed.json`
- `$RUN_DIR/generated/T001zh_email_triage_plus_1/task.yaml`
- `$RUN_DIR/generated/T001zh_email_triage_plus_1/fixtures/...`

如果你要强制指定某个 Step1 结果文件，而不是靠 `--task` 自动找：

```bash
PYTHONPATH=src python -m create_query.step2_new \
  --step1-json "$RUN_DIR/tasks/T001zh_email_triage/step1/output.json" \
  --output-dir "$RUN_DIR" \
  --only-index 1
```

### 5.5 覆盖已有输出

如果最终目录已经存在，需要显式允许覆盖：

```bash
PYTHONPATH=src python -m create_query.step2_new \
  --task T001zh_email_triage \
  --output-dir "$RUN_DIR" \
  --only-index 1 \
  --overwrite
```

## 6. 完整 batch 执行

### 6.1 先看候选任务

```bash
bash create_query/run_batch.sh list
```

它会先生成：

- `candidates.general.no_network.no_real_reference.txt`
- `run_summary.json`

候选范围是：

- `general` 任务
- `user_agent.enabled == false`
- `prompt.attachments` 为空
- fixtures 只有 `.json` / `.txt`
- 不依赖网络和真实参考文件
- 不包含 `web_search` / `web_fetch`

### 6.2 只跑一个任务

```bash
TASK=T001zh_email_triage WORKER=1 bash create_query/run_batch.sh one
```

这条命令内部做的事情就是：

1. `step1 --task T001zh_email_triage`
2. `step2_new --task T001zh_email_triage`

可配变量：

- `ONLY_INDEX=1`: 只生成一个 `plus_i`
- `OVERWRITE=1`: 覆盖已有最终目录
- `CREATE_QUERY_RUN_DIR=/abs/path/...`: 指定输出目录

例子：

```bash
TASK=T001zh_email_triage ONLY_INDEX=1 OVERWRITE=1 \
  bash create_query/run_batch.sh one
```

### 6.3 跑一个 shard

```bash
WORKERS=8 WORKER=3 bash create_query/run_batch.sh shard
```

含义是：

- 总共按 8 片切任务
- 当前只跑第 3 片

### 6.4 跑完整 batch

```bash
WORKERS=8 bash create_query/run_batch.sh batch
```

默认 `WORKERS=4`，不传时就是 4。

batch 模式会：

1. 先写候选清单
2. 再起多个 worker 并行跑
3. 每个 worker 先跑 Step1，再跑 Step2
4. 失败任务写进 `worker_XX.failed.txt`
5. 全部结束后回写 `run_summary.json` 里的 `failed_tasks`

## 7. 常见查看点

看某个源任务整体状态：

- `create_query/output_<TS>/tasks/<SOURCE_TASK_ID>/bundle.json`

看某个最终题是否真的生成成功：

- `create_query/output_<TS>/generated/<NEW_TASK_ID>/task.yaml`

看 batch 失败明细：

- `create_query/output_<TS>/log/worker_XX.err.log`
- `create_query/output_<TS>/log/worker_XX.failed.txt`

## 8. 一个容易踩坑的点

`--output-dir` 如果传相对路径，会被解释为相对于 `create_query/`。

调试时最稳妥的写法是传绝对路径，例如：

```bash
export RUN_DIR="$PWD/create_query/output_debug_T001"
```

这样 Step1 和 Step2 一定会落到同一个 run 目录里。
