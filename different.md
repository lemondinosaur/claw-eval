# `claw-eval` 与 `claw-eval-reference` 差异分析

## 1. 总览

这两个仓库不是两套完全不同的评测框架，而是同一套 Claw-Eval 主干上的两种工作形态。

- `src/claw_eval` 两边都是 41 个文件，真正不同的只有 4 个：
  `src/claw_eval/cli.py`、`src/claw_eval/config.py`、`src/claw_eval/runner/services.py`、`src/claw_eval/runner/providers/openai_compat.py`
- `mock_services` 两边都是 24 个二级文件，只有 2 个不同：
  `mock_services/web_real/search_serp.py`、`mock_services/web_real_injection/search_serp.py`
- `tasks/` 两边都是 300 个任务，split 数量完全一致：
  `T=161`、`C=38`、`M=101`
- 300 个任务里，只有 3 个文件不同：
  `tasks/M029_video_surveillance_clip/grader.py`
  `tasks/M051_video_surveillance_intrusion/grader.py`
  `tasks/M051_video_surveillance_intrusion/task.yaml`

结论很明确：

- `claw-eval-reference` 更像“官方对齐/跑通样板”
- 当前 `claw-eval` 不是重写 benchmark，而是在 reference 主干上继续扩出：
  非 Docker 运行分支、trace-only 分支、任务拆分/造题/训练数据导出分支

所以当前仓库的“功能更全”是事实，但它也因此出现了一些没有完全打磨齐的接口衔接点。

## 2. 两边共用的主执行链路

这部分主干基本一致，决定了两者的大部分行为其实是同构的。

- CLI 入口都在 `src/claw_eval/cli.py`，暴露 `run`、`batch`、`grade`、`build-image`、`cleanup`、`list`
- 任务定义都由 `src/claw_eval/models/task.py` 的 `TaskDefinition.from_yaml()` 读取
- 服务管理都由 `src/claw_eval/runner/services.py` 的 `ServiceManager` 负责：
  按 `task.yaml` 启动 mock service，做 health check，trial 之间做 `reset_all()`
- 实际 agent loop 都走 `src/claw_eval/runner/loop.py` 的 `run_task()`：
  system prompt 构造 -> prompt media 装载 -> 模型调用 -> tool dispatch -> trace 写入
- HTTP 工具分发都走 `src/claw_eval/runner/dispatcher.py`
- 开启 sandbox tools 时，都走 `src/claw_eval/runner/sandbox_dispatcher.py`
- Docker sandbox 生命周期都由 `src/claw_eval/runner/sandbox_runner.py` 负责：
  拉容器 -> 注入 `sandbox_files` -> 宿主机跑 agent loop -> 收集结果 -> 删容器
- trace 结构都定义在 `src/claw_eval/models/trace.py`
- grader 加载都走 `src/claw_eval/graders/registry.py`

也就是说，两边“真正的差异”不在核心 loop、trace schema、grader 框架本身，而主要集中在：

- CLI 编排层
- config / service 注入链
- web search key 管理
- 少数视频任务的 grading 方式
- 当前仓库额外长出来的非主线能力

## 3. 主执行流程上的核心差异

### 3.1 当前仓库在 CLI 上新增了 `trace-only` 分支

差异文件：`src/claw_eval/cli.py`

当前仓库新增了 `--no-grade` / `--trace-only`，并把它接到了 3 条路径上：

- `claw-eval run`
- `claw-eval _run-inner`
- `claw-eval batch`

实现效果是：

- 不创建 judge
- 不执行 post-hoc grader
- sandbox 模式下不注入 `sandbox_grader_files`
- sandbox 模式下不收集 `env_snapshot`
- trace 文件只写到 `trace_end`，不再 append `grading_result`
- batch summary 会改成 trace-only 统计，而不是 score/pass 统计

reference 没有这条分支，它默认把“跑任务”和“打分”看成一个连续动作。

这说明当前仓库已经不只是 benchmark harness，而是在兼做：

- 纯 trace 生成器
- 任务/数据构造中间层
- 非 Docker 实验管线

### 3.2 当前仓库把 `continue` 逻辑扩成了“认 `trace_end` 就算完成”

差异文件：`src/claw_eval/cli.py`

reference 的 `--continue` 只认 `grading_result`，即：

- 一个 trace 里没有 `grading_result`
- 那它就被视作“不完整 trial”

当前仓库把 `_scan_completed_trials()` 和 `_load_completed_results()` 改成了双模式：

- 默认仍然认 `grading_result`
- trace-only 模式下，只有 `trace_end` 也能算“完成”

这正好配合上面的 `--no-grade`。

这套改动从功能上是自洽的，但它只把 `continue` 和 batch summary 接好了，没有把整个后处理生态都接齐，后面会单独说。

### 3.3 当前仓库把任务路径和 repo 根路径都“实路径化”了

差异文件：`src/claw_eval/cli.py`

当前仓库在 `_resolve_task_yaml()` 里改成了 `Path.resolve()`，并额外引入了 `_repo_root()`。

这两个改动看起来小，实际上非常关键，因为它们直接服务于当前仓库新增的：

- `tasks_non_docker_splits/`
- `tasks_non_docker_non_web/`

这两个目录不是原始 `tasks/`，而是“拆分后的任务入口目录”。当前代码通过 `resolve()` 把 symlink/task path 指回真实任务目录，再通过 `_repo_root()` 把 mock service、src、grader 的相对路径锚回真实仓库根。

reference 没有这层处理，它更默认“你就是在原始 repo 根目录下直接跑 `tasks/`”。

### 3.4 当前仓库把 `ServiceManager` 的工作目录统一固定到 repo root

差异文件：

- `src/claw_eval/cli.py`
- `src/claw_eval/runner/services.py`

reference 在 batch worker 里有一处关键调用：

- `ServiceManager(..., cwd=tasks_dir.parent, ...)`

如果 `tasks_dir` 指向的是拆分目录，比如 `tasks_non_docker_splits/non_docker_general/plain`，那 `tasks_dir.parent` 就不再是真实 repo 根，`task.yaml` 里像

- `python mock_services/gmail/server.py`
- `python mock_services/calendar/server.py`

这样的相对命令就可能找不到。

当前仓库统一改成：

- 所有 `ServiceManager(...)` 都用 `cwd=_repo_root()`

这基本就是在给“拆分任务目录”“symlink 入口目录”“非官方执行入口”兜底。

这一点是当前仓库相对 reference 的一个实质性增强。

## 4. 配置、服务注入与真实 web search 的差异

### 4.1 reference 有一条完整的 `SERP_API_KEYS` 注入链，当前仓库把它拆掉了

相关差异文件：

- `src/claw_eval/config.py`
- `src/claw_eval/runner/services.py`
- `mock_services/web_real/search_serp.py`
- `mock_services/web_real_injection/search_serp.py`
- `scripts/run_eval.sh`
- `scripts/continue_eval.sh`

reference 的设计链路是完整的：

- `Config.defaults` 里有 `serp_api_keys`
- `load_config()` 还试图从 `models.json` 回填 `serp_api_keys`
- `ServiceManager` 启动 web service 时会把多个 key 作为 `SERP_API_KEYS` 注入环境变量
- `web_real/search_serp.py` 和 `web_real_injection/search_serp.py` 会：
  从 `SERP_API_KEYS` 取 key pool
  遇到 `403/429` 时把当前 key 标记为 exhausted
  自动轮转到下一个 key
- `scripts/run_eval.sh` / `continue_eval.sh` 生成 config 时也会把 `serp_api_keys` 写进去

当前仓库把这条链基本全拆了：

- `Config.defaults` 去掉了 `serp_api_keys`
- `ServiceManager` 不再注入 `SERP_API_KEYS`
- 两个 `search_serp.py` 都退化成只读单个 `SERP_DEV_KEY`
- `run_eval.sh` / `continue_eval.sh` 也不再把 `serp_api_keys` 写进 config

代码层面的直接后果：

- reference 支持多 key 配额轮转
- 当前仓库只剩单 key 模式
- web_real / web_real_injection 任务在配额打满、403、429 时更脆弱

如果你的目标是“和官方样板尽量一致地稳定跑 web-real 任务”，这是当前仓库最明显的一处回退。

### 4.2 reference 的 `models.json` fallback 代码意图是完整的，但实现上有一个潜在缺口

差异文件：`src/claw_eval/config.py`

reference 的 `config.py` 里有 `_load_models_json()`，意图很清楚：

- 若 config 里没有 `serp_api_keys`
- 则从 `models.json` 里补

但 reference 这份实现里没有 `import json`。

所以纯看代码，fallback 路径如果真的被走到，会有 `NameError` 风险。也就是说：

- reference 的“设计意图”比当前完整
- 但这段 fallback 实现本身也不是完全无瑕

不过这不改变主结论：当前仓库确实把整条 `SERP_API_KEYS` 能力移除了。

## 5. Provider 层差异

差异文件：`src/claw_eval/runner/providers/openai_compat.py`

当前仓库比 reference 多了一步“把 assistant 的 `reasoning_content` 再传回模型端”：

- 普通 assistant message 会加 `reasoning`
- 带 tool call 的 assistant message 也会加 `reasoning`

reference 虽然已经能从响应里解析 reasoning（`reasoning_content` / `reasoning`），但不会在后续消息里继续把它传回兼容端点。

这说明当前仓库更偏向兼容“thinking model / reasoning model”的连续对话回放。

这不是 benchmark 主流程的决定性差异，但它会影响：

- DeepSeek-R1 / QwQ / 类 reasoning 模型的多轮上下文回放
- 一些 OpenAI-compatible backend 对 reasoning 字段的保留行为

## 6. Docker / sandbox 环境差异

### 6.1 当前 `Dockerfile.agent` 比 reference 更瘦

差异文件：`Dockerfile.agent`

两边都保留了：

- `ffmpeg`
- `poppler-utils`
- `playwright + chromium`
- sandbox server

reference 额外安装了：

- `wget`
- `curl`

当前仓库把这两个去掉了。

直接影响不是 sandbox server API 本身，因为 `/download` 端点并不依赖 `wget/curl`，它只是从容器内路径读文件。

但对 agent 在 sandbox 里通过 `Bash` 执行命令来说，reference 的容器环境更接近“常见调试容器”，当前容器少了两个常用命令行工具。

### 6.2 当前 trace-only 模式下，sandbox 后处理会被跳过

差异文件：`src/claw_eval/cli.py`

当前仓库在 sandbox + `--no-grade` 下会跳过：

- `inject_grader_files()`
- `_collect_env_snapshot()`
- `_save_env_snapshot()`

这对“只要 trace，不要打分”的场景是合理的。

但它也意味着：

- 这类 trace 并不天然具备之后完整重打分所需的环境证据

尤其对依赖以下数据的 grader：

- `env_snapshot_files`
- `env_snapshot_commands`
- `local_grader_files`

后续只拿 trace 文件未必够。

## 7. 当前 `trace-only` 能力的集成边界

这是当前仓库里最值得注意的一组“功能已加上，但生态没完全跟上”的地方。

### 7.1 `cmd_grade` 并没有补齐 trace-only 所需的环境恢复

差异文件：`src/claw_eval/cli.py`

当前仓库新增了 `--no-grade`，但 `claw-eval grade` 这条命令仍然只是：

- 读 trace
- 读 task
- 直接调用 grader

它不会：

- 重做 `env_snapshot`
- 注入 `sandbox_grader_files`
- 自动补 `local_grader_files`

所以 trace-only 跑出来的产物，并不等价于“晚点再 `grade` 就一定能还原出同样结果”。

### 7.2 `cleanup_traces.py` 仍然把“没有 `grading_result` 的 trace”视作异常

文件：`cleanup_traces.py`

这份脚本没有因为 trace-only 模式而改逻辑，它依旧默认：

- 没有 `grading_result` 就是 abnormal trace

因此如果把 trace-only 结果直接喂给 `cleanup_traces.py`，它会把这类 trace 当异常删掉。

### 7.3 `score_summary.py` 仍然是 grading-result 中心的

文件：`score_summary.py`

这份脚本抽取的是：

- `grading_result`
- `trace_end`

但前者是主入口，trace-only 产物不会进入正常 score 汇总语义。

所以当前仓库的 `trace-only` 是“可生成 trace”，但不是“已完全融入原有清理/汇总/重评分工具链”。

## 8. task 层面的差异

300 个任务里只有两组视频任务发生了变化，而且变化都集中在“grading 取证方式”。

### 8.1 `M029_video_surveillance_clip`

差异文件：

- `tasks/M029_video_surveillance_clip/grader.py`

reference 的做法：

- sandbox 先在容器里用 `ffmpeg` 抽帧
- `env_snapshot_files` 收集 `/workspace/grading_frames/*.png`
- grader 直接从 `env_snapshot` 里读这些已抽好的 frame

当前仓库的做法：

- grader 不再读 `env_snapshot` frame
- 而是从 `env_snapshot` 中拿 `clip.mp4`
- 在宿主机侧临时落地 mp4
- 再调用宿主机 `ffprobe` / `ffmpeg` 重新采样帧

关键问题是：

- `M029` 的 `task.yaml` 没改
- 仍然保留了 sandbox 里的抽帧 `env_snapshot_commands`
- 仍然收集 `/workspace/grading_frames/*.png`

所以当前 `M029` 的状态是：

- grading 逻辑已经迁到宿主机
- 但 sandbox 端仍在做旧的 frame extraction
- 这些 frame snapshot 现在是冗余的

这是一处明显的“迁移只做了一半”。

### 8.2 `M051_video_surveillance_intrusion`

差异文件：

- `tasks/M051_video_surveillance_intrusion/grader.py`
- `tasks/M051_video_surveillance_intrusion/task.yaml`

这里 current 的方向和 `M029` 一样，也是改成：

- 用宿主机侧 `ffprobe` / `ffmpeg` 从 `clip.mp4` 重新抽帧

但这次 `task.yaml` 也跟着一起收敛了：

- 删除了旧的 sandbox 抽帧命令
- `env_snapshot_files` 不再收集 `grading_frames/*.png`
- 只保留 `/workspace/clip.mp4`
- `env_snapshot_timeout` 从 `30` 降到 `10`

所以 `M051` 是一致的：

- sandbox 只负责保留 clip
- grader 自己在宿主机抽帧

### 8.3 这两组视频任务改动的含义

当前仓库显然在把部分视频题从：

- “容器里预抽帧 -> grader 读 snapshot”

迁到：

- “容器只保留结果视频 -> grader 在宿主机重抽帧”

这样做的优点：

- 减少对 `env_snapshot` 中大量图片文件的依赖
- `M051` 这类任务能显著缩短 post-run snapshot 时间

代价：

- grader 现在依赖宿主机必须有 `ffprobe` / `ffmpeg`
- `M029` 这类没清理干净的任务会出现冗余 snapshot 成本

## 9. 脚本与 config surface 的差异

### 9.1 当前脚本更偏“本地实验/多分支运行”

差异文件：

- `scripts/run_eval.sh`
- `scripts/continue_eval.sh`

reference 的脚本偏“完整 benchmark 跑法样板”：

- 生成 full-eval config
- 把 `serp_api_keys` 写进去
- 默认 `--parallel 10`

当前脚本改成了：

- 不再生成 `serp_api_keys`
- 默认 `--parallel 6`

这说明当前仓库一方面是在降低资源压力，另一方面也默认接受了“web real 更依赖单 key”的现状。

### 9.2 当前仓库新增了两类脚本

新增脚本：

- `scripts/import_claw_eval_fixtures.py`
- `scripts/prepare_non_docker_splits.py`

它们不是 benchmark 主流程的一部分，而是：

- 前者把官方数据集 fixture 安全导入当前 `tasks/`
- 后者把当前 repo 的“非 Docker 可运行子集”物化成独立目录

reference 不包含这两块，说明 reference 更像“评测样板仓”，当前则更像“工作仓”。

### 9.3 当前仓库的示例 config 方向也变了

reference 新增的是：

- `config_deepseek-v4-flash_general.yaml`
- `config_deepseek-v4-flash_multimodal.yaml`
- `config_deepseek-v4-flash_multiturn.yaml`
- `config_gemini-3-flash-preview_general.yaml`
- `config_gemini-3-flash-preview_multimodal.yaml`
- `config_gemini-3-flash-preview_multiturn.yaml`

这些都是“完整 benchmark 跑法”的样板配置，且包含 `serp_api_keys`。

当前仓库新增的是：

- `config_qwen3vl_non_docker_general.yaml`
- `config_qwen3vl_non_docker_multi_turn.yaml`
- `config_qwen3vl_non_docker_multimodal.yaml`

这些配置的特点是：

- `sandbox.enabled: false`
- `trace_dir` 指向非 Docker 输出目录
- 明显面向 vLLM / 本地部署模型

也就是说：

- reference 的配置更偏“官方 benchmark 评测”
- current 的配置更偏“非 Docker 子集实验”

## 10. 当前仓库新增的扩展能力

这些目录不是 benchmark 主执行链路，但它们解释了为什么 current 会长出上面那些 CLI / 路径 / trace-only 改动。

### 10.1 非 Docker 任务判定与拆分

新增代码：

- `tasks/judge_docker_task.py`
- `tasks/judge_web_search_task.py`
- `scripts/prepare_non_docker_splits.py`
- `tasks_non_docker_splits/`
- `tasks_non_docker_non_web/`

这条支线的作用是：

- 结构化判断任务是否必须依赖 Docker sandbox
- 再结构化判断是否需要 web search
- 最后物化出可跑的子集目录

从当前仓库已经提交的 manifest 看，拆分结果是：

- `tasks_non_docker_splits/manifest.json`
  - `non_docker_general`: `plain=152`, `host_sandbox_tools=4`
  - `non_docker_multimodal`: `plain=5`
  - `non_docker_multi_turn`: `plain=2`, `host_sandbox_tools=36`
- `tasks_non_docker_non_web/manifest.json`
  - `non_docker_general`: `plain=126`, `host_sandbox_tools=3`
  - `non_docker_multimodal`: `plain=3`
  - `non_docker_multi_turn`: `plain=2`, `host_sandbox_tools=1`

这解释了当前仓库为什么一定要：

- 支持 symlink / split 目录
- 固定 service cwd 到 repo root
- 支持 trace-only 跑法

reference 没有这条支线，因此也不需要这些配套。

### 10.2 任务生成管线 `create_query/`

代码目录：

- `create_query/common.py`
- `create_query/step1.py`
- `create_query/step2_new.py`
- `create_query/run_batch.sh`

这套实现不是评测器，而是“基于现有 task 造新 task”的数据构造工具。

从代码看，它的核心行为是：

- 只选 generation scope 内的任务：
  `general`、非 `user_agent`、无 `attachments`、fixture 仅限 `.json/.txt`、不包含 `web_search/web_fetch`
- 还会读取 `judge_task/task_tags.jsonl`，进一步过滤
  `needs_network=false`
  `needs_real_reference_file=false`
- `step2_new.py` 会：
  基于原 task runtime skeleton 生成新 task
  删除 grader/scoring 相关字段
  重写 service env 中的 fixture 路径
  明确禁止生成 `grader.py`
- 产物写到 `create_query/output_*/generated/`，而不是直接回写 `tasks/`

这说明当前仓库已经把自己扩成了：

- benchmark harness
- benchmark-adjacent task generation workspace

reference 不包含这套系统。

### 10.3 `synthetic_query/` 目前还没有代码实现

当前仓库有 `synthetic_query/README.md`，但没有可执行 pipeline 代码。

所以这部分现在还只是设计占位，不是已经接入当前评测主链的能力。

### 10.4 训练数据导出更强

差异文件：

- `train_dataset/trace_dir_to_msswift.py`
- `train_dataset/execution.md`

reference 的版本只支持：

- `--trace-dir -> ms-swift jsonl`

当前仓库扩成了两种用法：

- 从 trace 目录转换
- 对“已经导出的 jsonl”再做一次 ms-swift 兼容清洗

并且 current 会强制把：

- `tool_call`
- `tool`
- `tool_response`

的 `content` 统一序列化为字符串，避免 mixed schema 让 ms-swift / `datasets/json` 读取失败。

当前仓库还直接提交了：

- `train_dataset/58pair_20260522_152922.jsonl`
- `train_dataset/58pair_20260522_152922.msswift.jsonl`

这再次说明 current 不只是 benchmark runner，而是在做数据生产。

### 10.5 标签目录 `judge_task/`

新增内容：

- `judge_task/tag_tasks.py`
- `judge_task/task_tags.jsonl`

这套逻辑会给任务打出 3 个结构标签：

- `needs_real_reference_file`
- `needs_network`
- `needs_docker`

这不是评测主链，但它被 `create_query/common.py` 直接消费，用来筛选造题候选。

## 11. 仓库卫生与产物管理差异

### 11.1 当前仓库把大量生成产物一起提交了

当前仓库独有且已落地的目录/产物包括：

- `logs/`
- `traces/`
- `create_query/output_20260522_152922/`
- `train_dataset/*.jsonl`

reference 基本没有这些运行产物。

### 11.2 `.gitignore` 策略也变了

差异文件：`.gitignore`

reference 会忽略：

- `traces/`
- `logs/`

当前仓库把这些忽略规则注释掉了。

所以 current 更像一个“带着实验产物一起工作的活仓库”，reference 更像“干净的样板仓库”。

这本身不影响执行逻辑，但会影响：

- 仓库可读性
- diff 噪声
- 复现实验时对“源代码”和“运行结果”的边界感

## 12. reference 独有但不在主运行链上的内容

reference 还有几样 current 没带过来的辅助内容：

- `package.json`
- `package-lock.json`
- `node_modules/`
- `test_opus.py`
- `2604.06132.pdf`

其中真正和代码有关的是：

- `package.json` 只声明了 `@anthropic-ai/claude-code`
- `test_opus.py` 是一个简单的 OpenAI-compatible API 调用样例

它们都不在 benchmark 主执行路径上，更像 reference 的本地复现/试验痕迹。

## 13. 总结判断

如果把两边的定位压缩成一句话：

- `claw-eval-reference` 是“更收敛、更接近官方样板的 benchmark 执行模板”
- 当前 `claw-eval` 是“在同一主干上继续扩出非 Docker、trace 生成、造题、训练数据导出等分支能力的工作仓”

当前仓库相对 reference 的主要增强点：

- 支持非 Docker 子集与拆分目录
- 支持 trace-only
- 支持任务生成与训练数据导出
- 对 symlink / split 目录的路径处理更稳

当前仓库相对 reference 的主要退化或未完全收口点：

- `SERP_API_KEYS` 多 key 轮转链路被拆掉，真实 web search 更脆
- `trace-only` 还没完全融入 `grade` / `cleanup_traces.py` / `score_summary.py`
- `M029` 视频任务迁移到宿主机抽帧后，旧的 sandbox 抽帧 snapshot 仍未清理
- Docker 镜像里少了 `wget/curl`
- 仓库里混入了较多生成产物，源码与产物边界比 reference 弱

如果后续目标是“保留 current 的扩展能力，同时尽量向 reference/官方样板回对齐”，优先应该回看的就是这 4 处：

- 恢复或替代 `SERP_API_KEYS` 轮转链
- 把 trace-only 的后处理链补齐
- 清理 `M029` 的冗余 env snapshot
- 明确当前 Docker 镜像是否还需要保留 `wget/curl`
