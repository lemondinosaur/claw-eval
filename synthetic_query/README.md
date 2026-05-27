# synthetic_query design

## 目标

`synthetic_query/` 不应该再走 `create_query/` 那种“围绕一个现有题目做近邻扩写”的路线，也不应该重复 `mutlimodal_create_query/` 那种“围绕一份原始多模态资产做派生”的路线。

这里更适合承接第三条线：

- `benchmark-near augmentation`：围绕已有 bench task 做同类增广，补密度。
- `source-grounded multimodal derivation`：围绕媒体资产做多模态派生，补感知覆盖。
- `environment-grounded compositional synthesis`：围绕 service / tool / state / side effect 做组合式合成，补跨服务泛化和结构多样性。

如果要把 Claw-Eval 的数据构建描述得更完整、更不像“对着榜单刷题”，推荐把整体叙事明确成一个三轨数据引擎，而 `synthetic_query/` 就是其中专门负责组合泛化的那一轨。

---

## 先说结论

当前仓库里已有两条造题路线，各自合理，但覆盖的不是同一个问题：

- `create_query/` 解决的是“如何在不破坏 task schema 和 grader 结构的前提下，沿着已有 general task 的邻域扩增样本”。
- `mutlimodal_create_query/` 解决的是“如何从同一份 image / video / document 资产中，派生出多种不同题型”。
- `synthetic_query/` 应该解决的是“如何把现有十几个 service 与更细粒度 function affordance 混合重组，构造出不依赖单一源 task、但仍然可验证、可审计、可复现的新工作流任务”。

更直接地说：

- `create_query` 是 task-centric。
- `mutlimodal_create_query` 是 source-centric。
- `synthetic_query` 应该是 affordance-centric / workflow-centric。

---

## 对当前代码库的观察

下面这些结论都直接来自当前仓库代码，而不是抽象想象。

### 1. `create_query/` 目前是强约束的 benchmark-near 改写

从 `create_query/step1.py` 和 `create_query/step2.py` 可以看出，它的设计目标很明确：

- Step 1 明确要求新题与原题属于“同一大类能力”，并尽量复用“相同或高度相近的 service/tool schema”。
- Step 2 明确要求新生成的 `task.yaml` 以原 task 为模板，保持顶层 key、`prompt`、`environment`、`tools`、`tool_endpoints`、`scoring_components` 等结构对齐。
- `create_query/common.py` 还额外限制了生成范围：只收 `general` 标签任务、跳过 `user_agent.enabled`、跳过带 `prompt.attachments` 的任务、排除 `web_search` / `web_fetch`，并只接受 `.json` / `.txt` fixtures。

所以这条线的价值不是“发明新结构”，而是：

- 保持评测风格一致。
- 在已有 task 邻域增加语义变化和场景变化。
- 以较低风险扩充与当前 bench 接壤的题目密度。

这没有问题，但它天然不会把你带到“新的服务编排结构”上去。

### 2. `mutlimodal_create_query/` 目前是 source-centric 派生

`mutlimodal_create_query/README.md` 也很清楚：

- 多模态不以已有 task 为核心。
- 多模态更适合先做 source packaging，再从统一资产包上派生 query blueprint。
- 它真正关心的是 evidence binding、annotation、gold 隐藏、输出约束。

这条线解决的是“同源多题型”，不是“跨 service 组合”。

### 3. 当前 general 任务空间里，跨服务组合还远没有被榨干

按当前仓库 `tasks/*/task.yaml` 的真实统计，带 `general` 标签的任务有这些特征：

- 共 `199` 个 `general` 标签任务。
- 其中 `60` 个显式带 `multi_service` 标签。
- 平均每题 `4.78` 个 tool、`1.91` 个 service。
- 最多可到 `16` 个 tool、`6` 个 service。
- 已出现的 service 组合不少，但单服务任务仍然占大头，尤其 `web_real` 单服务任务非常多。

也就是说，当前 bench 里已经存在不少多服务任务，但“service composition space”远未穷尽。

一些现有的典型组合已经说明了这条路是可行的：

- `calendar + contacts + gmail`
- `crm + finance + inventory`
- `crm + helpdesk + kb`
- `config + helpdesk + kb + scheduler`
- `calendar + contacts + gmail + inventory + kb + todo`

这些任务已经在证明：Claw-Eval 的 general split 不只是单一 API QA，而是在做小型 workflow orchestration。

### 4. 当前 service 层已经具备“组合合成”的底座

从 `mock_services/*/server.py` 看，当前服务并不只是只读查询，它们已经具备明显的状态变化能力和 audit 能力。

服务族可以粗分为四类：

- 协作与沟通：`gmail`、`calendar`、`contacts`、`todo`、`notes`
- 运维与控制面：`config`、`scheduler`、`helpdesk`、`kb`
- 业务数据：`crm`、`finance`、`inventory`、`rss`
- 外部信息与感知：`web`、`web_real`、`web_real_injection`、`documents`、`ocr`、`caption`

更重要的是，很多“低频但高价值”的写操作已经在服务代码里存在，只是在现有 bench 里暴露得不够均匀：

- `contacts_send_message`
- `inventory_create_order`
- `kb_update_article`
- `notes_share`
- `scheduler_create_job`
- `scheduler_update_job`
- `scheduler_delete_job`
- `todo_delete_task`
- `config_update_integration`
- `config_send_notification`
- `finance_submit_report` / `finance_report_submit`
- `crm_export_report`
- `rss_publish`
- `send_report`

这些能力非常适合拿来构造“先分析、再决策、再执行”的闭环任务，而不是只做查找和总结。

### 5. 代码里已经存在 safety / robustness 的真实抓手

这对 `synthetic_query/` 很关键，因为这意味着新题不需要只追求“完成”，而可以天然带上“能不能安全地完成、能不能稳定地完成”。

当前仓库里已经有三个直接可复用的机制：

- 多个 mock service 提供 `/audit`，可以检查真实副作用，而不是只看最终文字输出。
- `mock_services/_base.py` 支持 `ERROR_RATE` 注入 `429` / `500` / slow response，天然可做 robustness 变体。
- 一些服务本身就内含安全陷阱。
  - `config_get_integration` 会返回完整敏感配置，容易诱发 secrets 泄露。
  - `web_real_injection` 会把 prompt injection 直接插进真实搜索结果和页面内容里。

这意味着 `synthetic_query` 生成的任务完全可以把：

- 能力
- 安全
- 鲁棒性

一起设计进去，而不是只造一个“更难的答案题”。

### 6. 现有 runtime 已经支持可审计、可验证的生成闭环

当前 repo 的 `TaskDefinition`、trace、grader、validator 已经把一条比较完整的验证链路准备好了：

- `src/claw_eval/models/task.py` 定义了 `services`、`tools`、`tool_endpoints`、`expected_actions`、`safety_checks`。
- `src/claw_eval/runner/services.py` 能自动拉起 task 声明的 mock services。
- `src/claw_eval/runner/dispatcher.py` 会把每次 tool call 记录成 `ToolDispatch`。
- `src/claw_eval/models/trace.py` 里已经有 trace、audit、media、grading 的统一事件结构。
- `scripts/validate_tasks.py` 会校验 YAML、fixture、endpoint、grader、跨服务一致性。

换句话说，`synthetic_query/` 真正缺的不是“如何把 task 跑起来”，而是“如何从 service affordance 出发系统地生成 task blueprint”。

---

## 为什么还需要第三条线

如果只有前两条线，数据构建会有两个明显倾向：

- 要么太靠近已有题目范式，容易给人一种“顺着公开 benchmark 表面结构继续扩”的感觉。
- 要么太靠近单个原始 source，导致多样性主要来自内容差异，而不是 workflow 结构差异。

`synthetic_query/` 的作用就是把多样性提升到更高一层：

- 不是只换题面，而是换工作流结构。
- 不是只换实体，而是换证据流和副作用链。
- 不是只换 domain，而是换 service 组合、操作类型、风险边界和错误恢复路径。

因此，这条线的核心目标不该写成“再造一些 bench 题”，而应该写成：

> 从可审计的 service affordance 空间中，程序化采样跨服务工作流，并将其物化为可执行、可验证、可复现的 agent tasks，用于评估组合泛化、策略稳健性与安全约束遵循能力。

---

## 推荐给 `synthetic_query/` 的定位

推荐把这条线命名为：

- 中文：`能力图谱驱动的服务组合式造题`
- 中文：`环境落地的工作流合成`
- 英文：`Affordance-Driven Compositional Task Synthesis`
- 英文：`Environment-Grounded Workflow Synthesis`

如果要选一个最稳妥的总称，我建议用：

`environment-grounded compositional synthesis`

这个说法的好处是同时保住了三点：

- `environment-grounded`：不是空口编故事，任务必须能落到当前 service runtime、fixture、audit、grader 上。
- `compositional`：强调不是现有 task 的近邻改写，而是从更底层的操作与依赖关系组合。
- `synthesis`：保留“数据构建”的方法论中立性，不像“benchmark hacking”。

---

## 推荐总叙事

如果后续需要在 README、论文、技术报告里统一口径，可以把 Claw-Eval 的数据构建描述成：

> Claw-Eval 采用三轨数据构建范式：  
> 1) benchmark-near augmentation，用于在现有任务范式附近补足局部密度与语言变体；  
> 2) source-grounded multimodal derivation，用于从统一媒体资产中派生多种感知与生成任务；  
> 3) environment-grounded compositional synthesis，用于从 service affordance、世界状态依赖和可审计副作用中程序化构造新的跨服务工作流。  
> 这三条路线共同覆盖“局部近邻泛化”“同源多题型派生”“跨服务组合泛化”三种不同的数据多样性来源。

这套说法比“对着 bench 造题”更完整，也更好听，因为它把三种数据来源分开了。

---

## `synthetic_query/` 的核心思想

这里不应该把“task”当成最小生成单位，而应该把下面四样东西当成最小生成单位：

1. `service affordance`
2. `world state`
3. `workflow dependency`
4. `verifiable side effect`

一个新 task 的本质不是一句 prompt，而是：

- 当前世界里有哪些实体和状态。
- 哪些状态分散在不同 service 中。
- agent 需要怎样跨 service 收集证据、做出判断、执行动作。
- 哪些动作必须发生，哪些动作绝对不能发生。
- grader 如何从 trace / audit / final answer 共同验证。

---

## 建议的 pipeline

建议把 `synthetic_query` 明确分成下面七个阶段。

### Stage 0. Canonical Affordance Graph

先不要直接从 task 出发，先从 service 出发。

这里要做两件事：

- 把当前仓库里所有 service 的读操作、写操作、危险操作、可审计副作用抽成统一 affordance 图谱。
- 做一次“tool alias canonicalization”，把现有 task 里的命名漂移收敛掉。

为什么需要 canonicalization：

- 当前仓库里已经能看到同一类能力在不同 task 中有不同别名。
  - `finance_submit_report` 和 `finance_report_submit`
  - `inventory_get_item` 和 `inventory_get_product`
  - `send_notification`、`config_send_notification`、`send_report`
- 如果不先归一，后面的组合生成会被历史命名噪声拖住。

建议内部统一成 canonical op，例如：

- `gmail.list_messages`
- `gmail.get_message`
- `gmail.send_message`
- `calendar.create_event`
- `scheduler.update_job`
- `config.update_integration`
- `finance.submit_report`
- `rss.publish`

每个 affordance 节点建议至少记录：

```json
{
  "canonical_op": "scheduler.update_job",
  "service": "scheduler",
  "mode": "write",
  "input_fields": ["job_id", "enabled", "cron_expression", "name", "action", "tags"],
  "preconditions": ["job exists"],
  "side_effect_audit_key": "updated_jobs",
  "risk_tags": ["ops_change"],
  "aliases": ["scheduler_update_job"]
}
```

这一步做完，后续就不再从“task 暴露了哪些 tool 名称”理解能力，而是从“系统真实支持哪些操作原语”理解能力。

### Stage 1. World State Synthesis

第二步不是写 prompt，而是先生成一个跨 service 的“隐藏世界状态”。

这个世界状态应该至少包含：

- 实体层
  - 人、客户、联系人、供应商、工单、任务、会议、文章、库存项、交易、配置项
- 关系层
  - 谁和谁有关联
  - 哪个 job 依赖哪个 integration
  - 哪个 ticket 指向哪个客户 / 哪个库存异常 / 哪个知识库方案
- 状态层
  - active / failed / suspended / missing / overdue / low_stock / vip / degraded
- 时间层
  - 事件发生顺序、失败起点、依赖传播链、mock_today 对齐后的时间窗口

这一步的产物不该是“某个 service 的 fixture”，而应该是一个统一的 world model，再把它投影成各 service 的 fixture。

一个很典型的跨服务链可以像这样：

- `config` 某个 integration 认证过期
- `scheduler` 依赖它的多个 job 连续失败
- `inventory` 下游库存同步失真
- `helpdesk` 出现用户投诉
- `kb` 里存在类似故障的 SOP
- `gmail` 或 `contacts` 里有需要通知的人

这类链条比“单服务里找答案”更像真实工作流。

### Stage 2. Workflow Graph Sampling

有了隐藏世界状态后，再从中采样 workflow graph。

这里的最小单位不是“题型模板”，而是“操作依赖图”：

- 哪些节点必须先读
- 哪些判断依赖哪些证据
- 哪些动作可选
- 哪些动作禁止
- 最终要落到什么副作用

建议至少覆盖下面几类 workflow motif：

- `read -> summarize`
  - 典型是跨 service 相关性分析、季度回顾、风险排查
- `read -> decide -> write`
  - 典型是日程协调、工单分派、库存补单、报告提交
- `read -> verify -> redact`
  - 典型是配置审计、隐私合规、prompt injection 抵抗
- `read -> recover -> notify`
  - 典型是自动化故障恢复、集成修复、任务重调度
- `read -> curate -> publish`
  - 典型是 newsletter / briefing / KB 更新
- `read -> branch`
  - 典型是“某条件满足则发送，否则只存草稿”“某 ticket 可关闭，某 ticket 绝不能关闭”

这里最好显式控制组合新颖性，而不是只控制语言多样性。

推荐的 novelty 维度：

- service combo novelty
- canonical op sequence novelty
- hidden causal chain novelty
- allowed vs forbidden action pattern novelty
- final artifact novelty

### Stage 3. Task Blueprint Construction

这一步才开始写用户任务描述，但 prompt 只是 blueprint 的一部分。

每个 blueprint 至少应包含：

```json
{
  "task_id": "SQ001zh_ops_cascade_recovery",
  "language": "zh",
  "service_combo": ["scheduler", "config", "helpdesk", "inventory", "kb"],
  "workflow_motif": "read-decide-write",
  "user_request": "自然语言任务描述",
  "hidden_world_assumptions": ["..."],
  "required_reads": ["scheduler.get_job", "config.get_integration"],
  "required_writes": ["helpdesk.update_ticket"],
  "forbidden_actions": ["helpdesk.close_ticket"],
  "expected_side_effects": [
    {"service": "helpdesk", "audit_key": "updated_tickets", "count": 2}
  ],
  "final_output_requirements": ["故障链说明", "恢复建议", "优先级排序"],
  "safety_risks": ["secret_leak", "over_action"],
  "robustness_knobs": {"error_rate": 0.2}
}
```

一个关键原则：

- blueprint 必须先写清楚“世界状态、证据、动作、禁行动作、审计点”，再去写自然语言 prompt。
- prompt 只是对 blueprint 的语言表面化，不是任务本体。

### Stage 4. Artifact Materialization

有了 blueprint，再去物化：

- `task.yaml`
- `grader.py`
- `fixtures/...`

这一步应尽量复用当前 repo 的 schema 约束：

- `TaskDefinition`
- `tool_endpoints`
- `safety_checks`
- `expected_actions`
- `judge_rubric`
- `reference_solution`

建议生成时采用“world model -> per-service projection”的方式：

- 先生成统一世界状态。
- 再把它拆成 `fixtures/gmail/*.json`、`fixtures/helpdesk/*.json`、`fixtures/config/*.json`。
- 最后把这些 fixture 路径注入 `services[].env`。

这样能避免跨服务 ID 不一致、时间线对不上、状态无法互证的问题。

### Stage 5. Execution-Based Validation

这是最重要的一步，也是让整个 pipeline 不像“刷榜”的关键。

建议每个候选 task 至少过五层检查：

1. schema validation  
   直接走 `TaskDefinition.from_yaml` 和 `scripts/validate_tasks.py`

2. fixture consistency validation  
   检查跨 service 的实体引用、时间线、状态迁移是否自洽

3. grader sanity validation  
   检查 grader 不会出现明显误判、弱约束、只靠 final answer 字符串

4. solvability validation  
   用一个较强 teacher agent 或参考脚本跑通，确认任务可解

5. non-triviality validation  
   用若干 baseline agent 试跑，过滤掉过于简单、过于模板化、或者根本不可解的 case

推荐额外记录这些指标：

- 所需最少读操作数
- 所需最少写操作数
- service coverage
- canonical op coverage
- forbidden action density
- robustness stress level
- answer ambiguity risk

### Stage 6. Diversity Filtering

候选题跑通以后，还不能直接入库。

还应再过一层“多样性过滤”，避免大量看起来不同、实则结构近似的题目堆进来。

建议按下面几层做去重或降采样：

- 同一 service combo 下，canonical op sequence 完全相同
- 同一 workflow motif 下，隐藏因果链几乎相同
- 只是改领域词汇、实体名、日期，而证据流和动作流没变
- grader 结构和 gold rubric 过于相似

这里的目标不是把每个组合都塞满，而是让每一题都对能力空间有增量覆盖。

### Stage 7. Human Review and Release Split

最后一层仍然建议保留人工审核。

人工审核重点不是看“写得像不像题”，而是看：

- gold 是否唯一且可验证
- 是否存在大量等价正确解但 grader 接不住
- 是否存在隐式信息泄露
- 是否有不合理的世界状态巧合
- 是否对某个公开 bench 范式过拟合

release split 也建议按结构切，而不只是随机切：

- 按 service combo 留出未见组合
- 按 workflow motif 留出未见模式
- 按 canonical op 留出低频写操作
- 按安全风险类型留出未见陷阱

这样才能真正测“组合泛化”，而不是测“见过没有”。

---

## 这条线适合造什么样的题

相比 `create_query/`，`synthetic_query/` 更适合造下面这几类任务。

### 1. 关联还原类

要求 agent 跨多个 service 还原完整问题链，而不是只读一个地方。

例如：

- integration 错误 -> scheduler 失败 -> inventory 数据异常 -> helpdesk 投诉 -> kb 解决方案

### 2. 条件执行类

要求 agent 不只是分析，还要根据条件决定“发 / 不发”“关 / 不关”“建 / 不建”。

例如：

- 有联系人就发送，无联系人只存草稿
- 高优 ticket 更新标签，但分析阶段不能关闭
- 找到根因后重启某 job，但不能误动其他 job

### 3. 安全约束类

要求 agent 在可行动的同时克制自己。

例如：

- `config_get_integration` 可读到 secrets，但输出必须脱敏
- `web_real_injection` 页面里有注入指令，但不能跟随
- 存在“危险写操作”工具，但本题只允许分析，不允许执行

### 4. 低频写操作闭环类

现有 bench 中低频但很有价值的写操作，特别适合在这里系统化暴露。

例如：

- `scheduler_create_job` / `update_job` / `delete_job`
- `config_update_integration`
- `inventory_create_order`
- `rss_publish`
- `notes_share`
- `kb_update_article`

### 5. 多阶段交付类

要求最终结果同时包含：

- 结构化分析
- 一个或多个真实副作用
- 安全约束遵循

这类任务更能区分“会说”和“会做”。

---

## 如何避免“刻意刷榜”的观感

这一点建议在 README 里写得非常明确。

### 不要把它描述成“对着现有题型继续扩”

更好的说法是：

- 我们不是从已有 task 文本出发，而是从可执行 service affordance 和隐藏世界状态出发。
- 新任务与现有 task 可以共享能力边界，但不要求共享 prompt 结构、证据组织方式或动作顺序。

### 不要只强调数量

更好的说法是：

- 重点不是多造几百道题，而是提高 service composition coverage、low-frequency operation coverage、workflow motif coverage。

### 不要只强调“更难”

更好的说法是：

- 我们追求的是结构多样性、可验证性和组合泛化，而不是纯粹堆叠步骤数。

### 不要只靠 LLM judge

更好的说法是：

- 题目保留 hybrid grading：trace、audit、environment state、deterministic checks、LLM judge 共同构成验证链。

### 不要只做正向完成

更好的说法是：

- 生成时同时覆盖 required actions、forbidden actions、safety traps、robustness variants。

### 不要把 benchmark-near 这条线藏起来

这点反而应该说清楚：

- `create_query/` 仍然保留，用来补局部密度和风格连续性。
- `synthetic_query/` 则专门负责拉开结构分布，防止整个数据集只在已有 benchmark 邻域里采样。

这会让整体叙事更诚实，也更完整。

---

## 推荐目录结构

如果后续要真做这条 pipeline，建议 `synthetic_query/` 目录按“图谱 / 世界 / 蓝图 / 产物 / 校验”拆，而不是按“step1 / step2”拆。

```text
synthetic_query/
  README.md
  affordances/
    canonical_ops.json
    alias_map.json
    risk_tags.json
  world_models/
    templates/
    generated/
  workflow_blueprints/
    motifs/
    generated/
  task_candidates/
    raw/
    materialized/
    validated/
  reports/
    coverage/
    novelty/
    validation/
```

其中：

- `affordances/` 管 service 能力图谱，不直接管某道题。
- `world_models/` 管隐藏世界状态。
- `workflow_blueprints/` 管任务依赖图和动作约束。
- `task_candidates/` 才是真正的 task 落盘产物。
- `reports/` 管覆盖度、新颖性、可解性等统计，而不只是存 prompt。

---

## 与现有两条线的关系

建议最终把三条线写成互补关系，而不是替代关系。

### `create_query/`

适合：

- 围绕已有 general task 做近邻扩增
- 保持 schema 稳定
- 快速补局部密度

不适合：

- 发明新的 service 组合
- 发明新的动作闭环
- 系统覆盖低频 affordance

### `mutlimodal_create_query/`

适合：

- 围绕同一媒体 source 派生不同问题
- 做 evidence binding
- 做感知类 gold 和引用约束

不适合：

- 做大量跨 service workflow synthesis

### `synthetic_query/`

适合：

- 从 service affordance 空间生成全新工作流
- 把读操作、写操作、禁行动作、安全陷阱一起纳入任务结构
- 系统提升 general split 的结构多样性

---

## 相关工作与可借鉴点

下面这些工作不是为了“拼参考文献”，而是它们分别支撑了这条 pipeline 的不同部分。

### Claw-Eval

链接：<https://arxiv.org/abs/2604.06132>

可借鉴点：

- 三证据通道：execution trace、audit log、environment snapshot
- completion / safety / robustness 联合评估
- `Pass^k` 式多次试跑，强调一致性而不是单次运气

### τ-bench

链接：<https://arxiv.org/abs/2406.12045>

可借鉴点：

- 通过最终数据库状态和目标状态比对做 faithful evaluation
- 把 reliability 明确纳入 `pass^k`

### ToolSandbox

链接：<https://arxiv.org/abs/2408.04682>

可借鉴点：

- stateful tool execution
- 工具之间的隐式状态依赖
- on-policy user simulator
- intermediate milestone + final milestone 的动态评估

### APIGen

链接：<https://arxiv.org/abs/2406.18518>

可借鉴点：

- 从 executable API 出发做数据合成
- 三层验证：格式检查、实际执行、语义验证

这和 `synthetic_query` 最像，因为它说明“函数级可执行性”本身就可以成为数据生成的主轴。

### BFCL

链接：<https://www2.eecs.berkeley.edu/Pubs/TechRpts/2025/EECS-2025-184.html>

可借鉴点：

- 从 function-calling 角度理解 agent evaluation
- 大规模 API 覆盖
- 用可扩展验证代理执行验证瓶颈

对本仓库的启发是：

- `synthetic_query` 应优先覆盖 canonical operation space，而不是只覆盖题面空间。

### τ²-bench

链接：<https://arxiv.org/abs/2506.07982>

可借鉴点：

- 直接提出了 compositional task generator
- 从 atomic components 程序化生成 diverse、verifiable tasks
- 显式区分 reasoning error 和 communication / coordination error

这个工作和 `synthetic_query` 的方法论最接近。

### AgentInstruct

链接：<https://arxiv.org/abs/2407.03502>

可借鉴点：

- 从 raw data source 出发自动生成 prompt + response
- 强调 synthetic data 的质量和多样性来自 pipeline 设计，而不是只来自大模型写得像不像

### WildClawBench

链接：<https://arxiv.org/abs/2605.10912>

可借鉴点：

- native-runtime、long-horizon、hybrid grading
- 强调真实 runtime 下的 agent 行为和 mock-sandbox 并不等价

对当前 repo 的启发是：

- `synthetic_query` 虽然仍在 mock-service 空间里生成，但应尽量往更完整的 workflow 和 side effect 走，而不是退回到纯问答式 synthetic data。

---

## 一段推荐的“对外描述”

如果后续要把这部分写进论文、项目页或对外说明，可以直接使用下面这段中文。

### 短版

> Claw-Eval 的数据构建并不依赖单一路线。除 benchmark-near 的任务扩增与 source-grounded 的多模态派生外，我们还引入了 environment-grounded compositional synthesis：从可执行 service affordance、隐藏世界状态与可审计副作用出发，程序化合成新的跨服务工作流任务。该设计使数据多样性不仅来自题面改写，也来自 service 组合、证据流结构、动作闭环、安全约束与鲁棒性压力的系统变化。

### 长版

> We construct Claw-Eval with a hybrid data engine rather than a single benchmark-imitation pipeline. In addition to benchmark-near augmentation for local task density and source-grounded multimodal derivation for perception coverage, we introduce environment-grounded compositional synthesis for general agent workflows. This track samples tasks from a canonical service-affordance graph, hidden world-state dependencies, and auditable side effects, then materializes them into executable tasks with trace-based and audit-based verification. As a result, diversity is introduced not only at the surface level of prompt wording, but also at the structural level of service composition, operation sequencing, safety constraints, and robustness stressors.

---

## 最终建议

`synthetic_query/` 最值得做的，不是再复制一遍 `create_query` 的 step1 / step2，而是把“生成单位”从现有 task 提升到：

- service affordance graph
- hidden world state
- workflow dependency
- verifiable side effect

如果这样设计，这条线就会自然承担下面这个角色：

- 它不是为了刷已有 bench 的邻域分布。
- 它是为了把 Claw-Eval 从“任务集合”升级成“能力空间覆盖更完整的 agent evaluation suite”。

这也是最适合当前仓库结构的一种说法。
