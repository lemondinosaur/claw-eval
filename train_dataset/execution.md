# trace_dir_to_msswift 执行说明

## 作用
把 `trace_dir` 里的 trace JSONL 转成 ms-swift 训练数据，每行输出一个样本：

```json
{"messages": [...], "tools": [...]}
```

## 使用

```bash
python train_dataset/trace_dir_to_msswift.py \
  --trace-dir /path/to/traces \
  --output-jsonl /path/to/output.jsonl
```

## 参数

- `--trace-dir`：输入 trace 目录，只处理 `*.jsonl`
- `--output-jsonl`：输出文件路径
- `--tasks-dir`：用于回退恢复工具定义，默认是仓库根目录下的 `tasks`
- `--strict`：遇到单个 trace 转换失败时直接退出

## 处理逻辑

- 优先使用 trace 里的 `tools_snapshot`
- 如果没有 `tools_snapshot`，会回退读取对应 `tasks/<task_id>/task.yaml`
- 只保留 canonical 的 `messages` 和 `tools`
- `system` prompt 会插入到首条消息

## 结果

- 每个可转换 trace 输出一行 JSON
- 无法转换的 trace 默认跳过并记录日志

## 常见问题

- `trace directory does not exist`：检查 `--trace-dir`
- `Missing task.yaml`：检查 `--tasks-dir` 是否指向正确仓库
- `No .jsonl trace files found`：确认目录下有 trace 文件
