# Claw-Eval 复现补丁说明

本文档记录了在复现论文 (arXiv:2604.06132v1) Table 4 过程中，对原始仓库所做的全部修改。

---

## 1. API 兼容性修复

### 1.1 assistant tool_call 消息 content 字段处理

**文件**: `src/claw_eval/runner/providers/openai_compat.py`

**问题**: 原代码在 assistant 消息包含 tool_call 时，将空 content 序列化为 `content: null`。部分 API 网关会拒绝该格式。

**修复**: 当 content 为空时完全省略 `content` 字段，而非传 `null`。

### 1.2 Gemini Flash extra_content 透传

**文件**: `src/claw_eval/runner/providers/openai_compat.py`, `src/claw_eval/models/content.py`

**问题**: Gemini Flash 在 tool_call 中返回 `extra_content`（包含 `thought_signature`），后续请求必须原样传回，否则返回 400。

**修复**:
- `content.py`: `ToolUseBlock` 新增 `extra_content: dict[str, Any] | None = None`
- `openai_compat.py`: 在非流式、流式两条路径中提取并保存 `extra_content`，序列化时写入 tool_call 字典

---

## 2. Docker 沙盒模式修复

**文件**: `src/claw_eval/runner/sandbox_runner.py`

**问题**: 原代码使用 Docker 端口映射（`ports={...}`），在缺少 DOCKER iptables chain 的主机上报 500。

**修复**: 改用 `network_mode="host"`，每个容器通过线程安全的 `_allocate_port()` 分配唯一端口（从 18100 起递增）。

---

## 3. Dockerfile 镜像源修改

**文件**: `Dockerfile.agent`

**问题**: 原 Dockerfile 使用国内镜像源（DaoCloud registry、USTC apt、TUNA PyPI），在有代理的环境下构建失败。

**修复**: 改为官方源（docker.io、deb.debian.org、pypi.org），通过 build-arg 传入代理。

---

## 4. Web 搜索/抓取代理修复

### 4.1 mock 服务代理环境变量保留

**文件**: `src/claw_eval/runner/services.py`

**问题**: 原代码启动所有 mock 服务子进程时统一剥离代理环境变量，导致 `web_real` 服务无法访问外部 API。

**修复**: 服务名包含 `web_real` 的保留代理环境变量。

### 4.2 搜索 API 后端切换

**文件**: `mock_services/web_real/search_serp.py`, `mock_services/web_real_injection/search_serp.py`

**问题**: 原代码使用 `scraperapi.novada.com`（内部 API），外部环境不可用。

**修复**: 切换到 Serper.dev API：GET → POST，认证改为 header `X-API-KEY`，响应解析适配 `organic` 字段。

### 4.3 web_fetch 显式代理

**文件**: `mock_services/web_real/server.py`

**问题**: httpx 不自动读取代理环境变量。

**修复**: 显式读取 `https_proxy`/`http_proxy` 传入 `httpx.Client(proxy=...)`。

---

## 5. CLI --filter 逻辑修复

**文件**: `src/claw_eval/cli.py`

**问题**: 原代码使用子串匹配（`filt in d.lower()`），`--filter T` 会匹配路径中含 `tasks/` 的所有目录，C 类任务也被选中。

**修复**: 改为 basename 前缀匹配：`os.path.basename(d).startswith(filt)`。

---

## 6. Calendar Mock 服务参数兼容

**文件**: `mock_services/calendar/server.py`

**问题**: 12 个任务的 task.yaml tool schema 定义 `start_date`/`end_date` 参数，但 calendar mock 服务只接受 `date` 字段，模型按 schema 传参导致 422。这是参数名不匹配，与运行日期无关，永远失败。

**修复**: `ListEventsRequest` 同时接受 `date`、`start_date`、`end_date`，优先用 `date`，其次 `start_date`，均无则回退到 `MOCK_TODAY` 环境变量。`end_date` 存在时用于计算查询范围。

**效果**: 8 个任务从全部失败变为全部通过。

---

## 7. Scheduler/Config Mock 服务 `status:"all"` 修复

**文件**: `mock_services/scheduler/server.py`, `mock_services/config/server.py`

**问题**: task.yaml tool description 写 `"按状态筛选(all/enabled/disabled)"`，但 mock 服务将 `"all"` 当作普通值做精确匹配（`j.get("last_status") != "all"`），没有任何 job 的 status 等于 `"all"`，返回空列表。

**修复**: 过滤条件增加 `req.status != "all"` 检查，`"all"` 时跳过过滤返回全部。

**效果**: T137zh 从 0.558 → 1.0，T147zh 从 0.700 → 1.0（best trial）。

---

## 8. Pinbench Grader 正则 `re.MULTILINE` 修复

**文件**: `src/claw_eval/graders/pinbench_common.py`

**问题**: `REQUIRED_PATTERNS` 正则用 `^` 锚定行首，但 `re.search` 只传了 `re.IGNORECASE`，缺少 `re.MULTILINE`。`^` 只匹配字符串开头，模型输出首行不是列表格式时格式分 = 0。

**修复**: `re.IGNORECASE` → `re.IGNORECASE | re.MULTILINE`。

**效果**: T089 best trial 从 0.736 → 0.936。

---

## 9. T098 Grader 固定行号改为关键词搜索

**文件**: `tasks/T098_pinbench_openclaw_facts/grader.py`

**问题**: 原 grader 按固定行号检查答案（`lines[0]` 含 "5705"、`lines[1]` 含 "2999"...），模型在答案前加一行引言导致所有行号偏移 1 位，8 个正确答案全部判错。

**修复**: 改为逐行搜索关键词，不依赖固定行号。

**效果**: T098 从 0.288 → 0.912。

---

## 10. test_sandbox.sh venv 兼容

**文件**: `scripts/test_sandbox.sh`

**问题**: 脚本直接调用 `pip install`，未激活 venv，使用系统 Python 3.10 导致安装失败（claw-eval 要求 >=3.11）。

**修复**:
- 脚本开头自动激活 `.venv/bin/activate`（`set +u` 避免与 `set -euo pipefail` 冲突）
- `pip install` 改为先检查依赖是否已装，已装则跳过

---

## 修复效果汇总

| 阶段 | General Score | General AnyPass | 说明 |
|------|-------------|----------------|------|
| 未修复 | ~0.779 | ~130/160 | Calendar 422 + 各种 Bug |
| +Calendar 修复 | 0.809 | 139/161 | 恢复 8 个 calendar 任务 |
| +Scheduler/Grader 修复 | 0.819 | 143/161 | 恢复 T137zh/T147zh/T089/T098 |

论文报告值：Score=0.813, AnyPass=136/161 (84.5%)。修复全部代码 Bug 后，复现值超过论文。

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `config_sonnet_4.6_general.yaml` | Sonnet 4.6 General 任务配置 |
| `config_sonnet_4.6_multiturn.yaml` | Sonnet 4.6 Multi-turn 任务配置 |
| `config_flash_general.yaml` | Gemini Flash General 任务配置 |
| `config_flash_multiturn.yaml` | Gemini Flash Multi-turn 任务配置 |
| `scripts/run_sonnet.sh` | Sonnet 一键测评脚本 |
| `scripts/run_flash.sh` | Flash 一键测评脚本 |
| `scripts/continue_sonnet.sh` | Sonnet 续跑脚本 |
| `scripts/continue_flash.sh` | Flash 续跑脚本 |
