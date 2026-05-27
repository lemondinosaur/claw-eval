# 当前核心代码修改情况分析

## 1. 分析范围

本分析基于当前工作区相对 `HEAD` 的本地改动，重点只看核心运行链路，不展开 `create_query/`、任务生成脚本、训练数据、任务数据集等外围内容。

本次纳入分析的核心文件只有这些：

- `src/claw_eval/cli.py`
- `src/claw_eval/models/trace.py`
- `src/claw_eval/models/__init__.py`
- `src/claw_eval/runner/loop.py`
- `src/claw_eval/trace/reader.py`
- `src/claw_eval/trace/writer.py`

本次未展开分析但已观察到的非核心改动：

- `.gitignore` 新增忽略 `create_query/log/`
- `analysis.md` 被删除

## 2. 总体结论

这轮核心代码修改的重点，不是在模型调用协议或任务定义层，也不是 `create_query` 这种外围接口，而是在下面三条主线：

1. **trace 可观测性增强**
   现在 trace 不再只记录对话消息、工具调用和结束信息，还会额外保存“实际下发给模型的 system prompt”和“实际暴露给模型的工具列表”。

2. **执行与评分彻底解耦**
   CLI 新增 `--no-grade` / `--trace-only` 模式，支持“只跑 agent 并产出 trace，不立即做 post-hoc grading”。这个能力已经打通到单任务、本地模式、sandbox 模式、batch 模式，以及 `--continue` 的恢复逻辑。

3. **mock service 启动路径统一**
   所有 `ServiceManager` 调用点都改成从 repo root 启动服务，避免多进程/不同入口下因为相对路径不同导致 `python mock_services/...` 起不来。

可以把这轮改动理解成：**核心执行框架从“跑完立刻评分”的单一路径，升级成“先稳定地产生完整 trace，再按需要评分”的双阶段架构**。

## 3. 逐文件分析

### 3.1 `src/claw_eval/models/trace.py`

这里是 trace 事件模型的扩展，属于本次修改的基础层。

新增了两个事件类型：

- `SystemPromptSnapshot`
  - 保存最终渲染后的 `system_prompt`
  - 这里保存的是模型真实看到的文本，不是模板，也不是未拼接 prefix/suffix 的中间态

- `ToolsSnapshot`
  - 保存本轮任务实际暴露给模型的 `tools`
  - 类型是 `list[ToolSpec]`
  - 这里记录的是最终工具集合，包含任务原生工具、sandbox 工具、agent 内建工具等

同时，`TraceEvent` 联合类型被扩展，正式纳入：

- `SystemPromptSnapshot`
- `ToolsSnapshot`
- `CompactEvent`
- `GradingResult`

这意味着 trace schema 从“只覆盖主执行事件”，扩展成了“覆盖完整评测生命周期事件”。

### 3.2 `src/claw_eval/models/__init__.py`

这是与上一处模型变更配套的导出层修正。

新增导出：

- `CompactEvent`
- `GradingResult`
- `SystemPromptSnapshot`
- `ToolsSnapshot`

作用很直接：避免新 trace 类型只能从深层模块导入，保证外部代码继续通过 `claw_eval.models` 统一拿到全部公共模型。

### 3.3 `src/claw_eval/runner/loop.py`

这是核心执行链路中最关键的一处行为修改。

在 `run_task()` 里，trace 写入顺序被扩展成：

1. 写 `TraceStart`
2. 立刻写 `ToolsSnapshot`
3. 构造最终 `system_prompt`
4. 写 `SystemPromptSnapshot`
5. 再进入正常对话和工具调用循环

这里有两个关键点：

- `ToolsSnapshot` 记录的是 `task_tools`，而 `task_tools` 在写入前已经完成了工具合并：
  - task 自带 tools
  - sandbox tools（去重后）
  - agent tools（如 todo / compact）

- `SystemPromptSnapshot` 记录的是最终 prompt：
  - 已包含 `build_system_prompt()` 的主体
  - 已拼接 `model_cfg.system_prompt_prefix`
  - 如果启用了 user agent，也已拼接 `ua_cfg.system_prompt_suffix`

代码里的注释也说明了设计意图：

- system prompt 会被保存到 trace
- 但不会被塞进 grader 当前消费的“对话消息列表”里

也就是说，这次修改**增强的是可回放性和可审计性，不是 grader 的评分输入语义**。

### 3.4 `src/claw_eval/trace/writer.py`

这里的修改是为了适配新的 trace schema。

主要变化有两个：

- `write_event()` 的类型签名从若干硬编码事件，改成统一接受 `TraceEvent`
- 打开文件时显式使用 `encoding="utf-8"`

这两个变化分别解决了两个问题：

- 新增事件类型以后，writer 不需要再逐次扩签名
- trace 文件的编码行为更稳定，尤其是 system prompt、中文任务内容、评分理由等都可能包含非 ASCII 文本

### 3.5 `src/claw_eval/trace/reader.py`

reader 端同步做了兼容扩展。

新增识别的事件类型：

- `system_prompt`
- `tools_snapshot`
- `compact`

`read_events()` 现在返回统一的 `TraceEvent`，并且也显式按 UTF-8 读取。

更重要的是 `load_trace()` 的行为：

- 现在明确会跳过
  - `SystemPromptSnapshot`
  - `ToolsSnapshot`
  - `CompactEvent`
  - `GradingResult`
- 仍然只把下面这些内容组装给 grader：
  - `TraceStart`
  - `TraceMessage`
  - `ToolDispatch`
  - `AuditSnapshot`
  - `MediaLoad`
  - `TraceEnd`

这说明本次 trace 扩展采取的是**向前兼容策略**：

- trace 文件更完整了
- 但现有 grader 的入参结构没有被打破

### 3.6 `src/claw_eval/cli.py`

这是本轮改动量最大、影响面最广的文件，变化可以拆成四组来看。

#### 3.6.1 路径解析与 repo root 统一

新增：

- `_repo_root()`

改动：

- `_resolve_task_yaml()` 现在返回绝对路径
- 所有 `ServiceManager(...)` 调用都统一传入 `cwd=_repo_root()`

这项修改非常重要，因为当前任务里的 service command 大量写成：

- `python mock_services/crm/server.py`
- `python mock_services/web_real/server.py`
- `python mock_services/calendar/server.py`

这些命令本质依赖“当前工作目录就在仓库根目录”。如果从别的目录起服务，就会找不到 `mock_services/...` 相对路径。

本次修改之后，下面这些入口的 service 启动上下文全部统一：

- `cmd_run()` 本地模式
- `cmd_run()` sandbox 模式
- `cmd_run_inner()`
- `_run_single_task()`

这解决的是**跨入口一致性问题**，尤其对 batch / 多进程 worker 更关键。

#### 3.6.2 新增 trace-only 执行模式

本次最大的功能改动是新增：

- `--no-grade`
- `--trace-only`

它们是同一个参数的两个别名，已经接入：

- `run`
- `_run-inner`
- `batch`

控制逻辑通过 `do_grade = not args.no_grade` 统一收敛。

一旦关闭 grading，下面这些动作都会被跳过：

- judge 初始化
- sandbox grader files 注入
- env snapshot 采集与落盘
- local grader files 读取
- `load_trace()` 后的评分逻辑
- `grading_result` 追加写回 trace
- 单任务/批量评分结果输出

换句话说，`--trace-only` 模式下，系统只负责：

- 起服务
- 跑 agent
- 生成完整 trace
- 输出 trace 路径
- 在 batch 模式下汇总 token/time 等运行信息

这使“执行”和“评分”两个阶段被真正拆开。

#### 3.6.3 sandbox 后处理被条件化

在旧路径里，sandbox 模式跑完 agent 后，一定会继续做两件事：

- 注入 grader-only files
- 采集 env snapshot

现在这两步被放进 `if do_grade:` 分支里。

这有两个直接效果：

1. `trace-only` 模式明显更轻
   - 少一次 grader 文件注入
   - 少一次容器环境快照采集
   - 少一批 snapshot 落盘

2. 语义更干净
   - 只想跑 trace 时，不再掺入任何只服务于 grader 的后处理动作

#### 3.6.4 batch / continue 语义被扩展

这部分是本轮改动里最值得注意的一组，因为它把 `trace-only` 模式真正接进了批处理恢复机制。

新增或扩展的关键点包括：

- `_run_single_task(..., no_grade=False)`
- `_scan_completed_trials(trace_dir, include_trace_end_only=False)`
- `_load_completed_results(trace_dir, include_trace_end_only=False)`

其核心思想是：

- **有 grading 时**
  - 一个 trial 是否完成，仍然以 `grading_result` 为准

- **没有 grading 时**
  - 一个 trial 是否完成，改为以 `trace_end` 为准

这使 `batch --continue` 在 trace-only 模式下也能正确恢复，不再必须依赖评分事件。

具体落地表现如下：

- `cmd_batch()` 在 `--continue` 时会按 `do_grade` 决定扫描规则
- trace-only 结果可以从 `trace_end` 中重建：
  - turns
  - token 统计
  - model/tool/other/wall time
- `batch_summary.json` 新增 `grading_enabled`
- trace-only 模式下 summary 改为输出：
  - `Trace-only tasks`
  - `Completed trials`
  - 总 token / 总时间

这部分说明 CLI 已经不再把“评分”当成 batch 成功的唯一结束标志。

## 4. 执行链路变化

### 4.1 修改前的主路径

大致是：

1. 运行任务
2. 写常规 trace
3. 读取 trace
4. 立即 grading
5. 把 `grading_result` 追加回 trace
6. batch 恢复依赖 `grading_result`

这种模式的问题是：

- 执行和评分强绑定
- trace 不包含完整 prompt/tool 上下文
- 如果只想先批量生成 trace，再离线评分，流程不顺

### 4.2 修改后的主路径

现在实际变成两条路径：

**路径 A：正常评分模式**

1. 运行任务
2. trace 写入 `TraceStart + ToolsSnapshot + SystemPromptSnapshot + ... + TraceEnd`
3. 执行 grading
4. 追加 `GradingResult`

**路径 B：trace-only 模式**

1. 运行任务
2. trace 写入 `TraceStart + ToolsSnapshot + SystemPromptSnapshot + ... + TraceEnd`
3. 直接结束，不做 grading

这使 trace 成为真正的一等产物，而不是“评分前的中间文件”。

## 5. 对系统行为的实际影响

### 5.1 明显收益

- **可复现性更强**
  现在可以从 trace 里完整还原：
  - 模型到底看到了什么 system prompt
  - 模型当时到底有哪些工具可用

- **调试更直接**
  以前排查“为什么模型没用某个工具”或“为什么 prompt 行为异常”时，需要反推运行上下文；现在 trace 里直接有快照。

- **批量跑数更灵活**
  可以先大规模生成 trace，后续再统一评分，尤其适合：
  - judge 配置经常变化
  - 想重复 regrade
  - 想把执行成本和评分成本拆开管理

- **多进程服务启动更稳**
  repo root 统一后，service command 的相对路径依赖被收敛，batch worker 更不容易因为 cwd 漂移而失败。

### 5.2 明确未改变的部分

- agent 主循环没有被改写
- 模型调用方式没有本质变化
- tool dispatch 机制没有被重构
- grader 评分公式没有变化
- `load_trace()` 给 grader 的主数据结构仍然保持原样

所以本轮改动更偏“框架层增强”，不是“agent 推理逻辑重写”。

## 6. 需要注意的边界与风险

### 6.1 trace 体积会变大

新增保存的两类信息：

- 完整 system prompt
- 完整 tools schema

它们都会让单个 trace 文件变大，尤其是工具 schema 较长、prompt 较复杂时更明显。

### 6.2 prompt 内容现在会持久化到磁盘

这在调试上是优点，但也意味着：

- 如果 system prompt 里以后拼入了内部策略文本、实验前缀或敏感调试信息
- 那么这些内容会直接进入 trace 文件

目前这是设计上的主动选择，但后续要对 trace 分享范围有意识。

### 6.3 `load_trace()` 仍然不会把 prompt/tool 快照交给 grader

这是当前设计里一个很明确的取舍：

- trace 里已经有了 prompt/tool 信息
- 但 grader 还是看不到这些内容

所以如果后面要做“基于 prompt/tool 可用性”的分析或评分，仍然需要改 `load_trace()` 或增加新的 grader 入参。

### 6.4 `--continue` 的 summary 统计仍有一致性风险

这一点需要单独提醒。

当前 `cmd_batch()` 在 `--continue` 场景下，虽然会重新扫描 trace 目录并重建 `results`，但终端 summary 和 `batch_summary.json` 里使用的部分聚合变量，例如：

- `avg_score_final`
- `n_pass_hat`
- `n_pass_at`

仍然来自“本次进程内累计值”，不一定完全等于“合并后全部结果”的重新统计值。

也就是说：

- `batch_results.json` 更接近权威结果
- `continue` 场景下的 summary 仍可能只部分反映本次增量执行

这不是 `trace-only` 新功能的主目标，但它是当前核心代码里仍然存在的一个统计一致性边界。

### 6.5 编码处理还没有完全统一到底

本次 `TraceWriter` 和 `trace.reader.read_events()` 已显式改成 UTF-8，但仍有少量附属路径使用默认编码，例如：

- `_append_grading_to_trace()`
- `_scan_completed_trials()`
- `_load_completed_results()`

在当前 UTF-8 环境下通常没问题，但从一致性上看还没完全收口。

## 7. 当前代码状态判断

如果只看核心代码，这轮修改已经形成了一个比较清晰的新结构：

- **trace 记录层**：更完整
- **执行入口层**：支持 trace-only
- **批处理恢复层**：支持按 `trace_end` 恢复
- **服务管理层**：cwd 更统一

整体方向是合理的，而且改动之间是相互闭环的，不是只改了命令行参数却没把底层打通。

从“架构意图”上看，这轮代码的核心目标可以概括为一句话：

> 把 claw-eval 从“执行后立即评分的单阶段框架”，改造成“trace 先行、评分可选、恢复可追踪”的双阶段评测框架。

## 8. 静态校验结果

我已经做过一次静态编译检查：

- `python -m compileall src/claw_eval`

结果通过，说明当前这批核心 Python 改动至少没有明显语法错误。

需要说明的是，我这次没有实际跑一轮完整任务，因此这里的结论是：

- **静态结构和代码路径分析已完成**
- **语法层面正常**
- **运行时语义结论来自源码分析，不是完整端到端回归结果**

