# 当前框架 vs `claw-eval-reference` 的一致性分析

## 比对范围

本次只读代码，重点看四类内容：

- 任务加载与入口分发：`src/claw_eval/cli.py`, `src/claw_eval/config.py`
- 主推理链路：`src/claw_eval/runner/loop.py`, `runner/providers/openai_compat.py`, `runner/system_prompt.py`, `runner/media_loader.py`, `models/task.py`
- trace 记录：`src/claw_eval/models/trace.py`, `trace/writer.py`, `trace/reader.py`
- mock service 与任务数据入口：`mock_services/**/*.py`, `tasks/**/*.yaml`

另外做了源码级 hash 对比：

- `src/claw_eval/**/*.py` 只有 `cli.py` 和 `config.py` 与 reference 不同。
- `mock_services/**/*.py` 与 reference 全量一致。
- 共享配置 `config_general.yaml`、`config_multimodal.yaml`、`config_user_agent.yaml` 与 reference 一致。

## 先给结论

如果前提是下面这些条件同时成立：

- 跑的是当前仓库自己的 `tasks/`
- 运行目录就是当前仓库根目录 `/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval`
- 用的是共享配置 `config_general.yaml` / `config_multimodal.yaml` / `config_user_agent.yaml`
- 没有开启当前仓库新增的 `--no-grade/--trace-only`
- 也没有改用当前仓库新增的 `config_qwen3vl_non_docker_*` 这类新配置

那么，当前框架和 reference 在“主 agent 推理链路”上可以认为是同一套逻辑，送进主模型的消息构造方式、工具调用方式、mock service 行为、主 trace 事件顺序，都是一致的。

但如果问题变成更严格的这句：

> “前面得到的 traces，是否和真正送进 OpenAI server 的上下文绝对一致？”

答案是否定的。不是当前仓库特有问题，reference 也一样。现在这套 trace 记录的是“内部消息对象”和若干补充快照，不是每次发给模型的 raw request dump。对当前 `tasks/` 来说，最大的通用不一致点主要在：

- system prompt 单独记录，不在 `message` 事件里
- `tool_result` / `tool_use` 会在发送前转换成 OpenAI 兼容格式
- `reasoning_content` 会记进 trace，但不会回放给下一轮模型
- trace 不记录 `temperature` / `reasoning_effort` / `extra_body` / `stream` 这类请求参数
- provider 重试、user-agent 额外模型调用，不会完整落到主 trace 里

也就是说：

- “当前框架 vs reference 的主推理逻辑是否一致”：大体是，一致。
- “trace 文件是否等于 raw model context / raw HTTP request”：不是，做不到绝对一致。

## 1. 当前框架和 reference 哪些部分其实没变

### 1.1 主推理 loop 没变

`src/claw_eval/runner/loop.py` 与 reference 字节级一致。也就是说下面这些行为没有分叉：

- system prompt 构造后放入 `messages[0]`，初始用户消息放入 `messages[1]`：`runner/loop.py:324-340`
- 每轮都调用同一个 `provider.chat(messages, tools=task_tools)`：`runner/loop.py:413-420`
- assistant 回复、tool dispatch、tool result、tool media 注入的顺序一致：`runner/loop.py:422-537`
- 结束时统一写 `trace_end`：`runner/loop.py:560-583`

### 1.2 OpenAI 兼容 provider 没变

`src/claw_eval/runner/providers/openai_compat.py` 与 reference 字节级一致。以下逻辑完全一样：

- 内部 `Message` 到 OpenAI request message 的转换：`openai_compat.py:137-231`
- 请求参数组装：`openai_compat.py:278-290`
- provider 侧重试：`openai_compat.py:291-347`
- streamed response 拼装与 response parse：`openai_compat.py:361-469`, `475-544`

### 1.3 prompt / media / task schema / trace schema 没变

下面这些文件也与 reference 一致：

- `runner/system_prompt.py`
- `runner/media_loader.py`
- `models/task.py`
- `models/trace.py`
- `trace/writer.py`
- `trace/reader.py`

这意味着：

- task.yaml 的解析方式一致：`models/task.py:131-168`
- system prompt 的拼接方式一致：`system_prompt.py:151-180`
- 附件识别、读取、编码方式一致：`media_loader.py:75-223`
- trace 事件类型和字段完全一致：`models/trace.py:36-163`

### 1.4 mock service 行为没变

`mock_services/**/*.py` 与 reference 全量一致。

这点很重要，因为很多任务的真实上下文不是 prompt 本身，而是 agent 调工具后从 mock service 拿到的 JSON / 文本。既然 mock service 源码没变，那么在“同样的 fixture 路径、同样的 env、同样的端口”前提下，工具返回内容也不变。

## 2. 当前框架相对 reference 的真实代码差异

### 2.1 `cli.py` 新增了 `Path.resolve()`，影响路径稳定性，但不改主逻辑

当前仓库：

- `_resolve_task_yaml()` 会把 task 路径解析成绝对路径：`src/claw_eval/cli.py:20-31`

reference：

- 返回原始 `Path`，不做 `resolve()`

影响：

- 对当前环境下从 repo root 运行 `--task tasks/...` 的情况，基本无差别。
- 对符号链接、`..` 路径、从别的目录调用 CLI 的情况，当前仓库更稳定。
- 这会影响 `task_yaml.parent`、`task_yaml.parent.parent`、sandbox file 注入和本地 grader 文件读取时使用的是绝对路径还是相对路径。

结论：

- 这是“路径稳健性修复”，不是“推理逻辑改动”。
- 对你现在这台机器、现在这个 cwd，不构成 active diff。

### 2.2 `ServiceManager(..., cwd=...)` 的差异

当前仓库在 `run` / `_run-inner` / `_run_single_task` 中都显式传了 repo root：

- `src/claw_eval/cli.py:373`
- `src/claw_eval/cli.py:513`
- `src/claw_eval/cli.py:640`
- `src/claw_eval/cli.py:883`

reference 的情况：

- `run` / `_run-inner` 不传 `cwd`，默认用 `Path.cwd()`
- batch worker `_run_single_task` 用的是 `cwd=tasks_dir.parent`

影响要分开看：

- 对 batch worker 路径：当前 `cwd=_repo_root()`，reference `cwd=tasks_dir.parent`。对于当前仓库自己的 `tasks/`，二者其实都等于 repo root，所以这里没有 active diff。
- 对 `run` 和 `_run-inner`：当前仓库强制 repo root，reference 依赖进程启动时的当前目录。

这点为什么重要：

- 当前 `tasks/*.yaml` 里大量 service command 是相对路径，绝大多数都是 `python mock_services/...`。我统计到当前 `tasks/` 里共有 383 个带路径成分的相对 service command。
- 同时很多 task 给 service 注入的 fixture env 也是相对路径，比如 `GMAIL_FIXTURES: tasks/T032.../fixtures/...`、`DOCUMENTS_BASE_DIR: tasks/T097...`。

所以：

- 如果从 repo root 运行，当前与 reference 一致。
- 如果从非 repo root 运行，reference 可能直接找不到 `mock_services/...` 或把相对 fixture 路径解析错；当前仓库则固定按 repo root 解析。

结论：

- 对你现在这个 cwd，`ServiceManager cwd` 差异不改变结果。
- 但从“框架是否绝对等价”的角度说，当前仓库比 reference 更强约束、更稳定，已经不是完全同一个调用前提了。

### 2.3 新增了 `--no-grade / --trace-only`

这是 `cli.py` 最大的功能差异，入口在：

- `src/claw_eval/cli.py:1724`
- `src/claw_eval/cli.py:1742`
- `src/claw_eval/cli.py:1776`

它会带来这些变化：

- 不创建 judge：`cli.py:368`, `504`, `615`, `851`
- sandbox 模式下不再注入 grader-only files、不采 env snapshot：`cli.py:397-405`, `908-913`
- 不再读取 `local_grader_files`：`cli.py:414-429`, `536-553`, `927-944`
- 不再 append `grading_result`：`cli.py:411-412`, `533-534`, `653-654`, `948-963`
- batch `--continue` 在 trace-only 模式下，把 `trace_end` 也当作 completed marker：`cli.py:1053-1187`, `1243-1244`, `1530`

关键点：

- 这些改动都发生在 `run_task(...)` 之后，或者 batch 汇总阶段。
- 也就是说，主 agent loop、主模型输入、工具调用、`trace_start -> message -> tool_dispatch -> trace_end` 这条链路本身没有被改。
- 但 JSONL 文件会少一个 `grading_result` 事件；batch 的 `batch_results.json` / `batch_summary.json` 也会变。

所以：

- 如果你只关心“主推理过程和主 trace 轨迹”，`--no-grade` 本身不改推理。
- 如果你把“完整 trace 文件必须和 reference 一模一样”作为标准，那它当然不一致。

### 2.4 `config.py` 的 `json` import 修复

当前仓库在 `src/claw_eval/config.py:5` 显式 `import json`。

reference 缺这个 import，但下面又调用了 `json.load(...)`：`config.py:165-174`

这只在一种情况下会触发差异：

- config 里没有 `defaults.serp_api_keys`
- 并且当前目录或 repo root 存在 `models.json`

当前工作区现在没有 `models.json`，所以这条差异当前是 dormant 的，不会影响你现在的 run。

但从框架层面说：

- 当前仓库可以正常从 `models.json` 回填 `SERP_API_KEYS`
- reference 在触发这条分支时会直接报错

如果触发，会影响 `web_real` 类服务，因为 `ServiceManager` 会把 `SERP_API_KEYS` 注入 service env：`runner/services.py:121-123`

### 2.5 `scripts/run_eval.sh` 只改了并发数

当前仓库把脚本里的 `--parallel 10` 改成了 `--parallel 6`。

这会影响：

- 批量任务完成顺序
- 总 wall time
- batch summary 的输出时序

不会影响：

- 单个 task 的 prompt 构造
- 单个 task 的 tool schema
- 单个 task 的 mock service 返回格式

## 3. 对当前 `/tasks` 数据集本身的附加结论

这些结论是对当前仓库 `tasks/` 目录做静态扫描得出的：

- 所有 task 的 `environment.enable_compact` 都是 `false`
- 所有 task 的 `environment.enable_todo` 都是 `false`
- 有 38 个 task 开启了 `user_agent.enabled=true`
- 有 9 个 task 在 prompt 里使用了 `attachments`

这几个统计很关键，因为它决定了后面“trace 和 server context 不完全一致”的问题，哪些是当前活跃的，哪些只是框架级潜在问题。

## 4. 真正需要警惕的：trace 并不等于 raw model context

这部分 current 和 reference 都一样，我单独列，因为这是你更关心的点。

## 4.1 system prompt 不在 `message` 事件里

主 loop 里：

- system prompt 放进 `messages[0]`：`runner/loop.py:337-340`
- 但 trace 里不是把它写成普通 `message` 事件，而是单独写 `SystemPromptSnapshot`：`runner/loop.py:342-347`
- 初始只写了 user message：`runner/loop.py:349-353`

所以：

- 如果你只拿 trace 里的 `message` 事件去还原模型上下文，必然少了 system prompt。
- 必须把 `system_prompt` 事件和 `message` 事件一起拼起来，才接近真实输入。

## 4.2 trace 记录的是内部 `Message`，发送前还会做一次 OpenAI 兼容转换

转换逻辑在 `openai_compat.py:182-231`。

具体不一致点：

- trace 里的 `tool_result` 是一个 `role="user"` 消息，里面放多个 `ToolResultBlock`
- 发送给 OpenAI server 时，会被拆成一个或多个 `role="tool"` 消息：`openai_compat.py:188-199`

- trace 里的 assistant `tool_use` 是结构化 block
- 发送时会被转成 `tool_calls`，其中 `arguments` 会变成 JSON 字符串：`openai_compat.py:201-224`

- trace 里的 image/audio/video 是内部 block
- 发送时会变成 OpenAI 的 `image_url` / `input_audio` / 文本化 video 占位：`openai_compat.py:137-179`

所以：

- trace 不是 raw request body
- 它只是能被当前 provider 再次转换成 raw request 的中间表示

## 4.3 `reasoning_content` 会写进 trace，但不会回放给下一轮模型

provider parse response 时，会把 `reasoning_content` 存进 `Message.reasoning_content`：`openai_compat.py:540-544`

但 `_message_to_openai()` 完全不使用这个字段：`openai_compat.py:182-231`

这意味着：

- trace 里 assistant message 可能带有 reasoning / thinking 文本
- 下一轮真正发给模型的上下文里，不会包含这部分内容

所以如果你把 trace 直接当成“模型看见的完整历史”，会多算一块内容。

## 4.4 trace 不保存完整请求参数

真实发请求时，provider 会带上这些参数：

- `model`
- `messages`
- `tools`
- `temperature`
- `extra_body`
- `reasoning_effort`
- streaming 相关参数

见 `openai_compat.py:278-290`, `361-373`

但 trace 里：

- 只记了 `TraceStart.model`：`models/trace.py:36-43`
- tools 只在 `ToolsSnapshot` 里有：`models/trace.py:52-57`
- 没有把 `temperature` / `reasoning_effort` / `extra_body` / `base_url` / `stream` 落盘

所以：

- 如果你的“上下文”定义得很严格，要求等于完整 request JSON，那 trace 不够。
- 如果你只把“上下文”理解成 `system prompt + tools + messages`，那 trace 大部分能还原，但仍不是 100%。

## 4.5 provider 重试不会完整出现在 trace 里

`OpenAICompatProvider.chat()` 对 retryable error 最多会重试 5 次：`openai_compat.py:291-347`

trace 里只会留下最终成功的 assistant message，不会把每次失败请求都单独记成事件。

所以：

- 从 server 视角看，某一轮上下文可能被重复发了多次
- 从 trace 视角看，只像“调用了一次”

如果你是拿 trace 去和服务端 access log 对齐，这里一定对不上。

## 4.6 user-agent 额外模型调用不在主 trace 里

当前 `tasks/` 有 38 个 task 开启了 `user_agent.enabled=true`。

这类任务里，主 loop 会调用 `user_agent.generate_response(...)`：`runner/loop.py:439-454`

而 `UserAgent` 本身会单独向模型发起请求：`runner/user_agent.py:59-87`

trace 里只记录最终插回会话的 `[user_agent] ...` 文本：

- `runner/loop.py:450-453`

不会记录：

- user-agent 自己看到的 transcript prompt
- user-agent 自己的 usage
- user-agent 的 retry

所以：

- 如果你只关心“主 agent 下一轮看到的对话文本”，trace 里是有的
- 如果你关心“这个框架到底还向模型 server 额外发了哪些请求”，主 trace 不完整

## 4.7 sandbox media tool 的 `tool_dispatch.response_body` 不等于模型真正看到的内容

在 `runner/sandbox_dispatcher.py:190-246`：

- 对 `ReadMedia` / `BrowserScreenshot` / 某些 `Read` 响应，会提取 frames
- 图片会先压缩成 JPEG，再作为 `ImageBlock` 注入会话：`sandbox_dispatcher.py:210-217`
- 文本部分会把原始 `frames` 去掉，只留下 summary JSON：`sandbox_dispatcher.py:219-225`

所以：

- `tool_dispatch.response_body` 里可能有完整 frames/base64
- 但模型实际看到的是“精简过的文本摘要 + 压缩后的图片”

如果你后续做 trace-to-dataset，应该优先信 `message` 事件里的 media 注入消息，而不是直接信 `tool_dispatch.response_body`。

## 4.8 image stripping / image cap 是 in-memory 变更，不会新增 trace 事件

主 loop 每轮在真正调模型前，都会执行：

- `_strip_old_turn_images(...)`：`runner/loop.py:403-406`
- `_cap_conversation_images(...)`：`runner/loop.py:408-411`

这两个函数会直接改 `messages` 内存对象：

- 删除旧轮次的 image block：`runner/loop.py:108-137`
- 或把旧 image block 替换成文本占位：`runner/loop.py:65-105`

但不会把“改写后的旧消息”重新写一遍 trace。

所以：

- 长对话、多图片任务里，trace 里保留的旧图片，不一定还存在于后续真正发给模型的上下文

这是当前 `tasks/` 的一个活跃潜在点，尤其对有附件的任务、以及启用 sandbox tools 后产生截图/读图结果的任务有效。

## 4.9 compact 相关的大不一致，框架里存在，但对当前 `/tasks` 是 dormant

框架里 `compact.py` 会：

- 对旧 tool result 做微压缩：`compact.py:69-120`
- 做 auto compact summary：`compact.py:214-265`

而这些总结请求本身不会完整记进主 trace；summary 文本也不会像普通 `message` 那样被单独记录。

更重要的是，compact 内部调用了额外的 `provider.chat(...)`：`compact.py:252-257`

但主 loop 的 `total_usage` 并没有把这部分 token 记进 `trace_end`。

不过这次扫描当前 `tasks/` 的结果是：

- `enable_compact=true` 的 task 数量是 0

所以对你现在关心的当前 `tasks/`，这一块是 dormant 的；它是框架级缺口，但不是当前数据集上的 active diff。

## 5. 配置层面的非等价点

这部分不是源码分叉，而是“当前仓库多了新的可选配置”，如果你实际跑的是这些配置，那就不能再说和 reference 等价。

### 5.1 共享配置是一样的

以下文件与 reference 一致：

- `config_general.yaml`
- `config_multimodal.yaml`
- `config_user_agent.yaml`

所以如果你用的是这三份之一，且 CLI flag 相同，主推理配置层没有分叉。

### 5.2 当前仓库新增的非 docker / qwen 配置当然不等价

比如：

- `config_qwen3vl_non_docker_general.yaml`
- `config_qwen3vl_non_docker_multi_turn.yaml`
- `config_qwen3vl_non_docker_multimodal.yaml`

这些会直接改：

- `model_id`
- `base_url`
- `context_window`
- `sandbox.enabled`
- 部分 `media` 参数

这已经不是“框架一致性”问题，而是“你根本换了一套运行配置”。

### 5.3 `config_gemini_20mb_multimodal_trace_only.yaml` 不是纯粹的“只关打分”

这个文件除了 `judge.enabled: false` 之外，还改了：

- `model_id`: `pa/gemini-3-flash-preview`
- `base_url`: `https://openrouter.ai/api/v1`
- `trace_dir`
- `sandbox.enabled: true`
- 多模态预算和压缩参数：`max_images_per_turn=8`, `max_conversation_images=24`, `tool_image_max_dimension=512`, `tool_image_quality=35`

见 `config_gemini_20mb_multimodal_trace_only.yaml:1-33`

所以：

- 它不只是“不打分”
- 它也会直接改变多模态上下文内容

另外，代码里真正控制“是否跳过 grading”的不是这个 config 文件名，而是 CLI 的 `--no-grade/--trace-only` 开关：`src/claw_eval/cli.py:1724`, `1776`

也就是说：

- `judge.enabled=false` 只是不走 LLM judge
- 不代表 deterministic grader 也被跳过
- 真正 trace-only 要看有没有传 `--no-grade`

## 6. 最终判断

### 6.1 如果你问的是“当前框架和 reference，在当前 repo 的 `tasks/` 上，主推理方式是否一致”

结论：

- 在当前 cwd 就是 repo root、且使用共享配置、且不是 `--no-grade` 的前提下，可以认为一致。
- 主 loop、provider、task parser、trace schema、mock service 都没有分叉。
- 入口层差异主要是路径稳健性修复和 trace-only 分支，不改正常模式下的主 agent 推理。

### 6.2 如果你问的是“trace 能不能 1:1 代表送进 OpenAI server 的上下文”

结论：

- 不能保证绝对一致。
- 当前和 reference 都做不到。

当前 `tasks/` 下最现实、最活跃的不一致点是：

- system prompt 单独记，不在 message 流里
- `tool_result` / `tool_use` 要经过 provider 转换
- `reasoning_content` 记了但不会回放
- raw request 参数没落盘
- provider retry 不落主 trace
- user-agent 额外模型调用不落主 trace
- 长多模态对话里，图片可能在发送前被静默剥离 / 截帽

当前 `tasks/` 下目前处于 dormant 的大缺口是：

- compact summary 请求和 compact 后上下文没有被完整记录

### 6.3 一句话落地

如果你的目标只是确认“当前仓库有没有把 reference 的主推理链路改坏”，答案是基本没有，核心链路还是同一套。

如果你的目标是把“trace 文件”直接当成“发给模型 server 的精确原始上下文”，答案是不行，当前和 reference 都不满足这个标准；现在的 trace 更像是一个可回放的内部语义轨迹，而不是 raw request log。

## 7. 如果以后要做到“trace 和 server request 绝对一致”，缺什么

按现在代码，至少还缺下面这些日志点：

- 在 `OpenAICompatProvider.chat()` 内部、`client.chat.completions.create(...)` 之前，直接落盘最终 `kwargs`
- 把每次 retry 的请求也单独记下来，而不是只记最终成功结果
- 把 user-agent 的请求/响应/usage 单独记 trace
- 把 compact summary 的请求/响应/usage 单独记 trace
- 在 `_strip_old_turn_images()`、`_cap_conversation_images()`、`micro_compact()` 之后，记录“真正即将发送”的 message snapshot

不补这些点，就不能把当前 trace 当成严格意义上的 raw model request archive。
