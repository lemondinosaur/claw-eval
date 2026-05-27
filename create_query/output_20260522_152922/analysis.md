# `output_20260522_152922/generated` 任务质量分析

本文基于静态阅读做分析，没有实际执行任务、没有跑模型，只对：

- 生成任务的 `task.yaml`
- 对应原始 `tasks/<TASK_ID>/task.yaml`
- 代表性生成 fixtures
- `create_query/step2_new.py` 的生成约束

做质量判断。

## 1. 总体结论

整体上，这批 query 的质量可以概括为：

- `task 构造准确性：高`
- `语义多样性：中高`
- `真实难度扩展：中高`
- `自然度/用户感：中`

如果目标是：

- `用于 trace-only 运行、扩大场景覆盖面`：这批数据总体是可用的，而且不少样本质量不错。
- `作为正式 benchmark task 直接纳入高质量评测集`：还不够，需要二次 QA，尤其是自然度、难度标签、以及 grader 补齐。

最核心的判断是：

1. 这些任务大多不是“乱写 prompt”，而是能和对应 fixtures 对上，构造上基本成立。
2. 多样性主要体现在“同一工具拓扑下换业务场景、换推理焦点、换边界条件”，而不是换工具链。
3. 难度确实比原题普遍更高，但很多题的 `difficulty` 元数据仍然保持原值，这会低估真实复杂度。
4. 一部分题目已经从“自然用户请求”滑向了“带字段名和解题提示的 benchmark spec”。

## 2. 全量统计

对 `generated/` 下全部任务做静态统计后，得到：

- 生成任务总数：`267`
- 来源模板数（去掉 `_plus_i` 后）：`56`
- 完整产出 5 个变体的 source task：`45`
- 只产出 4 个变体的 source task：`9`
- 只产出 3 个变体的 source task：`2`
- 语言分布：`zh=237`，`en=30`
- 服务数分布：`1服务=94`，`2服务=49`，`3服务=55`，`4服务=17`，`5服务=38`，`6服务=14`
- 平均 prompt 长度：约 `426` 字符
- 最短 prompt：`88` 字符
- 最长 prompt：`1273` 字符

这说明两件事：

1. 这批生成不是少量试验样本，而是已经覆盖了相当多的 source task。
2. prompt 普遍明显变长，很多任务从“短请求”扩成了“带约束、带边界、带输出格式的复杂请求”。

## 3. 生成器约束决定了“多样性的边界”

这批任务的多样性有一个非常明确的上限：`step2_new.py` 强制保留原任务的大部分运行骨架。

相关代码见：

- [step2_new.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/step2_new.py:253)
- [step2_new.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/step2_new.py:273)
- [step2_new.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/step2_new.py:313)

具体来说，生成器会强制保持：

- `version`
- `category`
- `difficulty`
- `tags`
- `tools`
- `tool_endpoints`
- `sandbox_files`
- `environment`
- `services` 的结构与数量

只允许变化：

- `task_id`
- `task_name`
- `prompt.text`
- fixtures 内容
- service env 中指向 fixtures 的路径

所以这批任务的多样性，本质上是：

- `高语义多样性`
- `中交互多样性`
- `低拓扑多样性`

换句话说，它很擅长把：

- “同一个 helpdesk triage”
- “同一个 Gmail summarization”
- “同一个 inventory reorder”

扩成很多不同业务背景下的新 query；

但它不擅长把一个简单单服务任务，变成真正跨服务、跨工具的全新玩法，因为生成器本身就不允许这么做。

## 4. 样本对比

下面挑 5 组代表性样本看。

### 4.1 `T017zh_ticket_triage`：构造准确，场景切换自然

- 原始任务：[原始 task](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/T017zh_ticket_triage/task.yaml:1)
- 生成任务代表：[plus_4](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T017zh_ticket_triage_plus_4/task.yaml:1)
- 对应 fixtures：[tickets.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T017zh_ticket_triage_plus_4/fixtures/helpdesk/tickets.json:1)

原题非常简单：

- “看看待处理工单，分个类、排个优先级、标关联”

生成后的 5 个变体分别切到：

- 通用排障分流
- 支付链路异常
- 门禁/考勤分诊
- 身份中心/SSO 故障
- 网络工单 triage

这个例子我认为质量是高的，原因是：

- 任务目标没有偏离原工具能力，仍然只需要 `helpdesk_list/get/update`
- 场景切换明显，但依然是“单轮 triage”而不是硬塞别的能力
- 生成的工单内容和 prompt 的判别逻辑能对上，例如 `plus_4` 里确实有：
  - 同租户同时间窗的 SSO 故障
  - 外部 IdP 维护中案例
  - 账号被禁用/锁定案例
  - 指引类噪声工单
  - 已自行解决仅需归档的干扰项

这说明它不是只把 prompt 写复杂，而是连 fixtures 也跟着重构了。

这一组在“构造准确性”和“语义多样性”上都比较强。

### 4.2 `T094_pinbench_project_alpha_summary`：同一 Gmail 工具下，语义视角扩展得很好

- 原始任务：[原始 task](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/T094_pinbench_project_alpha_summary/task.yaml:1)
- 生成任务代表：[plus_3](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T094_pinbench_project_alpha_summary_plus_3/task.yaml:1)
- 对应 fixtures：[inbox.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T094_pinbench_project_alpha_summary_plus_3/fixtures/gmail/inbox.json:1)

原题只是：

- 搜索 Project Alpha 邮件
- 输出 overview / timeline / risks / impact / current status

生成后的 5 个变体，分别把总结焦点改成：

- 迁移与安全
- 事故复盘
- 供应商交付与合同风险
- 范围取舍与决策路径
- 利益相关方对齐状态

这一组最好的地方是：

- 它证明了“同一个 Gmail-only 任务”也能做出有意义的 query 多样性
- 不是简单换说法，而是换“阅读视角”
- `plus_3` 的邮件内容里确实存在：
  - 供应商里程碑
  - SLA 定义变更
  - payment node 冲突
  - 合同 revision
  - contingency 方案

所以任务是成立的，不是空泛总结题。

这一组的问题也很明显：

- prompt 对输出结构规定得很死
- answer space 被压得比较窄
- 更像“定制化结构化提取题”，而不是自然办公请求

结论：

- `多样性：强`
- `构造准确：高`
- `自然度：中`

### 4.3 `T141zh_sla_compliance_audit`：边界条件设计好，但开始偏 benchmark-spec

- 原始任务：[原始 task](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/T141zh_sla_compliance_audit/task.yaml:1)
- 生成任务代表：[plus_5](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T141zh_sla_compliance_audit_plus_5/task.yaml:1)
- 对应 fixtures：
  - [integrations.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T141zh_sla_compliance_audit_plus_5/fixtures/config/integrations.json:1)
  - [tickets.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T141zh_sla_compliance_audit_plus_5/fixtures/helpdesk/tickets.json:1)
  - [jobs.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T141zh_sla_compliance_audit_plus_5/fixtures/scheduler/jobs.json:1)

这组是我认为“真实难度扩展”做得比较好的一组。

原题只是：

- 看 SLA 规则
- 对比工单响应时间
- 检查自动化
- 输出报告

生成后的变体已经引入了：

- 地区/渠道映射偏差
- 静默堆积与补偿机制失效
- 升级阈值与响应阈值不一致
- 历史回放与时区解析边界

其中 `plus_5` 是一个好例子：

- prompt 要求处理 `Z` 和 `+08:00`
- fixtures 里真的有这两种时间格式
- config 里真的有 `time_standard` 与解析异常
- scheduler 里也真的有“历史回放/修正报表”失败记录

这说明：

- 题目构造是准确的
- 不是“嘴上说时区”，底层数据没准备

但这组也暴露了一个问题：

- prompt 中已经出现 `applied_policy_version`、`response_time_minutes` 这类偏实现字段
- 从“用户说人话”逐渐走向“测试说明书”

所以它更适合作为 benchmark-style reasoning task，而不是自然流办公 query。

### 4.4 `T161zh_automation_failure_recovery`：技术链路更复杂，但有一定“答案泄漏”

- 原始任务：[原始 task](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/T161zh_automation_failure_recovery/task.yaml:1)
- 生成任务代表：[plus_4](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/task.yaml:1)
- 对应 fixtures：
  - [integrations.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/fixtures/config/integrations.json:1)
  - [jobs.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/fixtures/scheduler/jobs.json:1)
  - [items.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/fixtures/inventory/items.json:1)
  - [tickets.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/fixtures/helpdesk/tickets.json:1)
  - [articles.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T161zh_automation_failure_recovery_plus_4/fixtures/kb/articles.json:1)

这一组在技术完整性上是强的。

`plus_4` 引入了：

- MQ replay storm
- 幂等键规则不稳定
- ledger 写入失败
- 汇总不可信
- 对账/结算链路停止
- 恢复过程中的停放、限流、DLQ、补偿重跑

而 fixtures 里确实对应准备了：

- integration notes 说明幂等键字段不稳定
- scheduler 里有 `JOB-R1 -> JOB-R2` 依赖链
- inventory items 有 `partial/error`、`last_sync` 抖动
- 工单和 KB 文章都围绕这条链路

所以从“构造是否成立”角度看，这组是成立的。

但它的问题也最明显：

- prompt 里已经直接写出了候选根因方向
- 甚至把恢复动作都提示到了“停放/限流/死信处理/幂等键修复”
- 用户请求更像“请按这个已知思路把证据串起来”，而不是“请你真的去发现问题”

这会降低 discovery 难度，转而提高整理与验证难度。

因此这类题的真实变化不是“更开放”，而是“更长链、更工程化、但更带路”。

### 4.5 `T019zh_inventory_check`：难度扩展很大，但标签和自然度都跟不上

- 原始任务：[原始 task](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/tasks/T019zh_inventory_check/task.yaml:1)
- 生成任务代表：[plus_5](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T019zh_inventory_check_plus_5/task.yaml:1)
- 对应 fixtures：[products.json](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/output_20260522_152922/generated/T019zh_inventory_check_plus_5/fixtures/inventory/products.json:1)

这是一个很典型的“从超简单题扩成复杂规则题”的例子。

原题只有一句：

- “看看库存哪些要补货了，帮我下单。”

而 `plus_5` 已经变成：

- 引入 `season_multiplier`
- 区分 `sales_already_seasonal`
- 引入 `immediate_safety_days`
- 明确给出计算公式
- 要求解释补货依据和优先级

从构造准确性上说，这题是成立的：

- fixtures 里确实准备了这些字段
- inventory tool 也足够支持列表、单品读取、下单

但它暴露出两个很强的问题：

1. `difficulty` 仍然是 `easy`
2. prompt 的写法已经非常像“出题说明/隐藏 rubric 外露”

这个例子很能说明全批次的一个共性：

- 实际推理负担变重了
- 但元数据没有同步升级
- 自然用户感下降了

## 5. 这批 query 的优点

综合看，这批 query 的优点主要有 5 个。

### 5.1 大多数样本不是“只换词”，而是换了业务语境

例如：

- 工单 triage 从通用 IT 故障切到了支付、门禁、SSO、网络
- Gmail 总结从 generic summary 切到了 incident / procurement / stakeholder alignment
- SLA 审核从普通超时比对切到了时区回放、升级阈值、补偿失效

这种变化是有实际意义的。

### 5.2 代表性样本里，prompt 与 fixtures 的对位普遍是成立的

我抽查的样本中，没有发现明显的：

- prompt 要求某字段，但 fixture 根本没有
- prompt 要求某行为，但工具根本做不到
- prompt 需要跨服务信息，但任务其实只有单服务

这说明“构造准确性”整体不错。

### 5.3 边界条件设计明显增强了难度

常见增强方式包括：

- 时间戳/时区边界
- 依赖链路
- 误导性噪声项
- 弱关联和强关联区分
- 多来源矛盾证据对照
- 部分失败 / stale / partial 状态判断

这些都比原题更有 benchmark 价值。

### 5.4 同一 source task 的 5 个变体通常不是重复改写

从抽样看，很多 source task 的 5 个 `plus_i` 都是明显不同的子场景，而不是：

- 只换同义词
- 只换实体名
- 只把 prompt 写长

这一点比我预期要好。

### 5.5 基本没有明显污染 prompt 的路径引用

抽样统计中，没有看到 prompt 直接泄露 `/workspace/...` 之类的本地路径提示，这一点是干净的。

## 6. 这批 query 的主要问题

问题也比较集中。

### 6.1 多样性主要停留在语义层，不在任务拓扑层

原因不是模型偷懒，而是生成器设计如此。

由于 [step2_new.py](/ytech_m2v8_hdd/workspace/kling_mm/tangjiafu/claw-eval/create_query/step2_new.py:273) 强制保持 `tools`、`tool_endpoints`、`environment`、`sandbox_files`、`difficulty` 基本不变，所以：

- 一个 Gmail-only task 永远还是 Gmail-only
- 一个 helpdesk-only task 永远还是 helpdesk-only
- 一个 inventory-only task 永远还是 inventory-only

这限制了“真正新任务类型”的产生。

### 6.2 prompt 越来越像 benchmark spec，而不像用户 query

这是我认为当前质量上最明显的问题。

典型表现：

- 写出实现字段名
- 写出判定公式
- 写出预期根因链
- 写出恢复动作列表
- 写出应该怎么分类的 rubric

这种写法对 benchmark 很友好，但对“自然用户请求”不友好。

### 6.3 有些题存在一定程度的“答案泄漏”

代表样本是 `T161zh_automation_failure_recovery_plus_4`。

问题不在于它难度低，而在于：

- 真正需要 agent 去发现的根因方向
- 已经在 prompt 中被半公开提示了

这会把任务从“诊断”推向“验证已知假设”。

### 6.4 `difficulty` 元数据没有反映真实难度变化

这一点很关键。

例如：

- `T019zh_inventory_check` 原题极短且简单，但 `plus_5` 已经有多条件公式与边界逻辑，仍然标成 `easy`
- 统计上看，不少 source task 的 prompt 长度扩张倍率很高，但 difficulty 仍保持原值

这不是偶发现象，而是生成器设计造成的，因为它强制保持原 difficulty 不变。

### 6.5 产出覆盖不是完整 5/5

从 56 个 source task 来看：

- `45` 个拿到完整 5 变体
- `11` 个是不完整的

这不一定说明 query 质量差，但说明流水线的稳定性或后处理通过率还不够满。

## 7. 我对这批任务的最终评价

如果把目标拆成两个方向，我会给出不同结论。

### 7.1 如果目标是“扩充 trace 采样池”

结论：`可以用，而且整体质量不错`

理由：

- 大多数任务在语义上确实是新场景
- 代表性样本构造基本成立
- reasoning 边界比原题更丰富
- 足以用来观察 agent 在更多业务情境里的表现

### 7.2 如果目标是“扩成高质量 benchmark 题库”

结论：`还需要第二轮人工 QA`

重点要补的不是运行层，而是题目层：

- 给 prompt 去掉过强的 rubric 味
- 降低字段名外露程度
- 修掉答案泄漏较明显的题
- 重新标注 difficulty
- 如要进入正式评测，再生成或补写 grader

## 8. 建议的下一步

我建议把这批任务分成三类处理。

### A 类：可直接用于 trace-only 扩充

特征：

- prompt-fixture 对位清楚
- 没有明显答案泄漏
- 虽然结构化，但仍像合理用户请求

我抽样里比较接近这一类的有：

- `T017zh_ticket_triage_plus_4`
- `T094_pinbench_project_alpha_summary_plus_3`
- `T141zh_sla_compliance_audit_plus_5`

### B 类：任务成立，但需要“自然化改写”

特征：

- 题目能做
- fixtures 也支撑
- 但 prompt 太像 spec

典型：

- `T019zh_inventory_check_plus_5`
- `T141zh_sla_compliance_audit_plus_2`

### C 类：任务成立，但需要“去答案提示”

特征：

- 链路复杂
- 工程感强
- prompt 已经把根因方向或恢复动作透露太多

典型：

- `T161zh_automation_failure_recovery_plus_4`

## 9. 一句话总结

这批 generated query 不是“随便写写”的低质量扩写，相反，`构造准确性整体较高`，`样本层面的语义多样性也明显增加`；但它目前最大的问题是：`越来越像 benchmark spec，而不像真实用户 query`，同时 `difficulty 元数据没有跟上真实复杂度`。因此，它很适合先做 trace 采样池，不建议原样直接视作最终高质量 benchmark 题集。
