# CLAW-Eval 复现报告

**日期**: 2026-05-23 ~ 2026-05-24  
**模型**: deepseek-v4-flash, gemini-3-flash-preview  
**API 代理**: app.ppapi.ai/v1  
**论文基准**: Claw-Eval: Towards Trustworthy Evaluation of Autonomous Agents (2026)

---

## 一、总体结果

| 模型 | 分组 | Pass^1 | Pass^3 | Avg Score | Trials |
|------|------|--------|--------|-----------|--------|
| deepseek-v4-flash | General (161 tasks) | 84.5% | 65.8% | 0.794 | 483 |
| deepseek-v4-flash | Multi-turn (38 tasks) | 63.2% | 15.8% | 0.671 | 112* |
| gemini-3-flash-preview | Multimodal (101 tasks) | 32.7% | 7.9% | 0.430 | 303 |

> Pass^1: 至少 1 trial 通过; Pass^3: 3 trials 全部通过  
> \* Multi-turn 有 2 个 trial 未完成，应有 114 trials

### 与论文数据对照

论文 (Table 3, Table 4) 未直接评测 deepseek-v4-flash 和 gemini-3-flash-preview，最接近的参考模型：

| 论文模型 | General Pass^3 | Multi-turn Pass^3 | Multimodal Pass^3 |
|----------|---------------|-------------------|-------------------|
| DeepSeek V3.2 | 42.2% | 31.6% | — (无视觉能力) |
| Gemini 3 Flash | 48.4% | 52.6% | 14.8% |
| **我们 deepseek-v4-flash** | **65.8%** | **15.8%** | — |
| **我们 gemini-3-flash-preview** | — | — | **7.9%** |

### Gemini 3 Flash 论文多模态数据 (Table 4)

| 指标 | 论文 Gemini 3 Flash | 我们 gemini-3-flash-preview |
|------|-------------------|---------------------------|
| Avg Score | 50.4 | 43.0 |
| Pass@3 (Pass^1) | 37.6% | 32.7% |
| Pass^3 | 14.8% | 7.9% |

---

## 二、deepseek-v4-flash General (Pass^3=65.8%)

**Trace 目录**: `traces/deepseek-v4-flash_26-05-23-20-39/`  
**配置**: 纯文本  
**耗时**: 57824s (16.1h), 48M tokens

### 已识别问题

#### a) 纯文本模型遇到图片 task 导致 12 trial 报错 (7 task)

General 分组中部分 task 天然包含图片内容（logo 识别 T057、菜单 T072、office 文档截图 T076-T081）。Agent 通过 Read 工具读取图片后，框架将 base64 图片数据发给纯文本 deepseek-v4-flash，被 ppapi.ai 拒绝 (`"This model does not support image inputs"`)。

| Task | 得分 (3 trials) | 报错 trial 数 |
|------|----------------|:---:|
| T057 deepseek_logo_identification | [0, 0.20, 0.20] | 1 |
| T072 restaurant_menu_contact | [0, 0, 0.72] | 2 |
| T076 officeqa_defense_spending | [0, 0.20, 0.88] | 1 |
| T077 officeqa_highest_dept_spending | [0.20, 0, 0] | 2 |
| T078 officeqa_max_yield_spread | [0, 0.20, 0] | 2 |
| T080 officeqa_bond_yield_change | [0, 0, 0] | 3 |
| T081 officeqa_cagr_trust_fund | [0.38, 0.21, 0] | 1 |

**根因**: 框架层未处理"纯文本模型 + 图片 tool result"的情况。应在发送前过滤不支持的模态，或将图片转为文本描述。

#### b) 6 个 task 全部 3 trials 得 0 分

| Task | 原因 |
|------|------|
| T025zh / T026 ambiguous_contact_email | 模型能力不足 |
| T027zh / T028 api_config_audit | 模型能力不足 |
| T080 officeqa_bond_yield_change | 图片不支持 (3/3 报错) |
| T160 vip_ticket_escalation | 模型能力不足 |

---

## 三、deepseek-v4-flash Multi-turn (Pass^3=15.8%)

**Trace 目录**: `traces/deepseek-v4-flash_26-05-24-03-57/`  
**配置**: 纯文本  
**耗时**: 38 tasks, 112 graded trials

### 已识别问题

Multi-turn Pass^3 (15.8%) 远低于 General (65.8%)，且低于论文中上一代 V3.2 的 Multi-turn (31.6%)。pass^1=63.2% 说明模型有能力通过部分 trial，但 trial 间一致性极差——38 个 task 中 24 个至少过 1 次，但仅 6 个三次全过。

---

## 四、gemini-3-flash-preview Multimodal (Pass^3=7.9%)

**Trace 目录**: `traces/gemini-3-flash-preview_26-05-24-00-41/`  
**耗时**: 46252s (12.8h), 303 trials, 56M tokens

### 已识别问题

#### a) 整体能力不足

- 70/101 tasks 平均得分 < 0.50
- M028 badminton_score_chart、M044 bugatti_identification 等视频任务中，模型反复调用 ReadMedia 查看帧但从不产出回答
- 33/101 tasks 至少 1 trial pass (Pass^1=32.7%)，但仅 8/101 tasks 全 3 trial pass (Pass^3=7.9%)，trial 间一致性差

#### b) 与论文差距分析

| 因素 | 说明 |
|------|------|
| 模型版本 | 我们: gemini-3-flash-**preview** (预览版)，论文: gemini-3-flash (正式版) |
| 代理层 | ppapi.ai 中转 vs 论文直连 Google API |
| 多模态支持 | ppapi 对该模型的多模态支持可能不完整 |

#### c) ppapi 上游过载 (1 trial)

M091 trial 3 报错 `当前分组上游负载已饱和，请稍后再试`，被 openai_compat.py 错误包装为 "Model endpoint rejected multimodal input"。

---

## 五、代码问题

### openai_compat.py 错误分类 Bug

**位置**: `src/claw_eval/runner/providers/openai_compat.py:324`

当 `has_multimodal_input=True` 时，所有非重试错误被统一包装为 "Model endpoint rejected multimodal input"，掩盖真实原因。M091 的 ppapi 过载和 T057-T081 的"模型不支持图片"都被同样误报，而两者根因完全不同。
