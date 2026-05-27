# create_query

当前有效的造题流程是：

`step1.py -> step2_new.py`

`step2.py` 还在目录里，但不是当前默认路径，`run_batch.sh` 也默认调用 `create_query.step2_new`。

## 这个目录在做什么

- 从 `tasks/<SOURCE_TASK_ID>/` 读取一个已有单轮任务
- `step1.py` 先产出 5 条新题描述
- `step2_new.py` 再把这些描述展开成最终 task 目录
- 所有中间文件和最终产物都写在 `create_query/output_*` 下，不回写 `tasks/`

最小输入单元不是单个 fixture 文件，而是一个完整的源任务目录：

- `tasks/<SOURCE_TASK_ID>/task.yaml`
- `tasks/<SOURCE_TASK_ID>/fixtures/**/*.json|txt`

如果你只想生成一个最终题，可以在 Step2 用 `--only-index 1..5`。

## 任务筛选范围

批量模式只处理满足下面条件的任务：

- 有 `task.yaml`
- `tags` 包含 `general`
- `user_agent.enabled == false`
- `prompt.attachments` 为空
- `fixtures/` 至少有一个文件，且后缀只能是 `.json` / `.txt`
- `tools` 不包含 `web_search` / `web_fetch`
- `judge_task/task_tags.jsonl` 里 `needs_network == false`
- `judge_task/task_tags.jsonl` 里 `needs_real_reference_file == false`

## 预期输出结构

```text
create_query/
  step1.py
  step2_new.py
  run_batch.sh
  output_<BJ_TIMESTAMP>/
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

其中：

- `step1/output.json` 固定包含 5 条 `new_task_descriptions`
- `tasks/<SOURCE_TASK_ID>/bundle.json` 是单个源任务的总状态文件
- `step2/<NEW_TASK_ID>/parsed.json` 是 Step2 模型输出的解析结果
- `generated/<NEW_TASK_ID>/` 是最终可运行任务目录

## 最终生成目录的约束

`generated/<NEW_TASK_ID>/` 的预期是：

- 必有 `task.yaml`
- 必有与源任务完全同路径集合的 `fixtures/...`
- 不生成 `grader.py`
- `task.yaml` 会保留源任务的运行时结构，例如 `services`、`tools`、`tool_endpoints`、`sandbox_files`、`environment`
- `task.yaml` 会去掉 judge/scoring 相关字段

也就是说，`step2_new.py` 主要是在“复用原任务运行骨架”的前提下，替换：

- `task_id`
- `task_name`
- `prompt.text`
- `fixtures/...`
- `services[*].env` 里指向 fixture 的路径

## 最常用命令

单任务：

```bash
PYTHONPATH=src python -m create_query.step1 --task T001zh_email_triage
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage
```

只生成一个 `plus_i`：

```bash
PYTHONPATH=src python -m create_query.step2_new --task T001zh_email_triage --only-index 1
```

看 batch 候选：

```bash
bash create_query/run_batch.sh list
```

完整执行说明见 [execution.md](./execution.md)。
