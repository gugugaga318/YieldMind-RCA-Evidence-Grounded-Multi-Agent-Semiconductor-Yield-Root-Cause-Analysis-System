# YieldMind RCA｜基于多智能体与 Evidence-Grounded Reasoning 的半导体良率根因分析系统

YieldMind RCA 面向 Yield Engineer、Process Engineer 和设备制程团队。系统从产品良率异常或已知异常 Lot 出发，组织多智能体调查 MES、FDC/SPC、Defect、WAT 和历史 RCA 数据，并基于可追溯证据给出影响范围、根因判断、处置建议和 RCA 报告。

**版本：** `0.1.0`

**项目状态：** 可部署平台

License: 待定

## 目录

- [项目定位](#项目定位)
- [适用场景](#适用场景)
- [核心方法](#核心方法)
- [主要功能](#主要功能)
- [分析结果](#分析结果)
- [分析流程](#分析流程)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [运行模式与配置](#运行模式与配置)
- [API 概览](#api-概览)
- [数据集](#数据集)
- [评测与验证](#评测与验证)
- [本地开发](#本地开发)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [设计边界](#设计边界)
- [文档导航](#文档导航)
- [常见问题](#常见问题)
- [联系方式](#联系方式)
- [待确认项](#待确认项)

## 项目定位

半导体良率异常排查需要跨越多类数据：Lot 和 Wafer genealogy、工艺步骤、设备与腔体、Recipe、FDC/SPC、Defect、WAT 以及历史 RCA case。人工调查往往需要在多个系统之间反复核对，不仅耗时，也容易遗漏证据或无法还原结论的形成过程。

YieldMind RCA 将这项工作组织成一套可部署、可追踪、可复核的分析流程。工程师提供调查入口，系统负责确定范围、调取相关数据、组织专业分析、评估根因假设，并生成完整 RCA 报告。

项目名称表达了系统的两个核心设计：

- **YieldMind**：围绕半导体良率工程建立多智能体分工，让不同 Agent 分别处理调查规划、制造数据、过程异常、缺陷电性、历史知识和改进建议。
- **RCA**：以 Root Cause Analysis 为目标，输出的不只是模型回答，而是影响范围、根因假设、支持与冲突证据、置信度和处置建议。

当前版本使用离线合成 Fab 数据进行开发、演示和评测，尚未接入真实 Fab 实时数据。

## 适用场景

- 某个产品在指定时间范围内出现良率下降，需要查找共同设备、腔体、Recipe 或工艺步骤。
- 已知一个异常 `lot_id`，需要追溯其 genealogy、OOC 窗口和其他受影响 Lot。
- 需要联合检查 FDC/SPC、Defect、WAT 与制程履历是否指向同一原因。
- 需要将根因结论与原始记录关联，供 Yield、Process 和设备团队共同复核。
- 需要把已确认 RCA 经过审批后沉淀为后续调查可检索的历史知识。

## 核心方法

### 多智能体协作

YieldMind RCA 按工程职责拆分调查任务，各 Agent 通过 Tool Layer 获取数据，不直接访问数据库。

| 分工 | 主要职责 |
| --- | --- |
| Planner / Supervisor | 理解调查目标、拆分任务、安排执行顺序并汇总进度 |
| MES Agent | 调查 Lot、Wafer、route、operation、equipment、chamber、Recipe 和 Hold 履历 |
| FDC Agent | 检查 feature drift、OOC event、控制界限和 SPC 规则异常 |
| Defect/WAT Agent | 分析缺陷与电性症状、Wafer 分布及其共同性 |
| Knowledge Agent | 检索历史 RCA case 和工程知识文档 |
| RCA Reasoning Agent | 形成并比较根因假设，检查支持证据、冲突与缺口 |
| Improvement Agent | 根据受支持结论生成 containment、corrective 和 preventive 建议 |
| Report Generator | 将调查范围、证据、结论和建议整理为 RCA 报告 |

系统支持固定工作流、Controlled ReAct 和 Qwen 驱动的 `llm_react`。无论采用哪种编排模式，Agent 都不能绕过 Tool Layer 和证据检查直接生成正式结论。

### Evidence-Grounded Reasoning

Evidence-Grounded Reasoning 的含义是：根因结论必须建立在系统实际取得并记录的 Evidence 上，而不是只依赖 LLM 的文字判断。

- Tool 查询结果会转换为带来源标识的 Evidence。
- 每个根因假设必须引用对应的 Evidence ID。
- 支持证据、反向证据、数据缺口和冲突会分别记录。
- SPC 计算、Evidence 校验、冲突处理和最终支持阈值由确定性代码执行。
- 证据不足或相互冲突时，系统输出无法定论，而不是强行选择一个根因。
- 只有证据支持的结论才能生成 memory candidate，并在双工程师审批后成为高权重历史知识。

## 主要功能

- 支持按产品与时间范围调查良率异常。
- 支持从已知异常 `lot_id` 追溯相关 Lot 和 Wafer。
- 关联 MES、FDC/SPC、Defect、WAT 和历史 RCA 记录。
- 比较工艺步骤、设备、腔体、Recipe 和异常时间窗口的共同性。
- 识别可能受影响的 Lot，并说明它们为什么被纳入范围。
- 生成根因假设、支持证据、冲突信息和置信度。
- 给出 containment、corrective、Recipe、preventive 和 Fab-level 建议。
- 生成可查看、下载和复核的 Markdown RCA 报告。
- 记录 Agent 决策、Tool 调用和 Evidence 引用；符合条件的结论可经过双工程师审批后写入知识库。

## 分析结果

| 输出 | 内容 |
| --- | --- |
| 影响范围 | 异常产品、时间窗口、来源 Lot 和其他受影响 Lot |
| 根因判断 | 排序后的根因假设、置信度，以及无法定论时的明确说明 |
| Evidence Chain | 根因引用的 MES、FDC/SPC、Defect、WAT 和历史案例记录 |
| 处置建议 | 事件控制、纠正措施、Recipe、预防措施和 Fab-level 建议 |
| RCA 报告 | 调查范围、分析过程、结论和建议组成的 Markdown 报告 |
| Agent Trace | 调查计划、Agent 决策、Tool 调用、观察结果和状态变化 |

在仓库提供的 `golden_case` 中，YieldMind RCA 可以从 `40N_SOC` 产品异常定位 20 个暴露 Lot；从 `LOT_A_001` 开始调查时，可以定位另外 19 个影响 Lot，并将根因指向 `CMP_CU03_CH02 slurry delivery degradation`。这是固定合成数据上的演示结果，不代表真实 Fab 的分析准确率。

## 分析流程

1. 工程师输入产品和时间范围，或一个异常 `lot_id`。
2. Planner 确定调查范围，将问题拆分给对应的专业 Agent。
3. 各 Agent 通过 Tool Layer 查询制造、量测和历史知识，并生成可追溯 Evidence。
4. RCA Reasoning Agent 比较根因假设，确定性规则检查证据充分性与冲突。
5. Improvement Agent 和 Report Generator 输出建议与完整 RCA 报告。

Qwen 可用于理解意图、规划下一步和解释工程语义；Qwen 不可用时，YieldMind RCA 可以使用确定性模式继续运行。

## 快速开始

Docker Compose 是运行完整 YieldMind RCA 的推荐方式。它会启动 PostgreSQL、FastAPI、Queue Worker 和 React/Nginx 前端。

### 环境要求

- Git
- Docker Engine 与 Docker Compose v2；Windows 可使用 Docker Desktop
- 建议至少为容器和本地模型预留足够的磁盘空间

只有本地开发才需要另外安装：

- Python `3.11+`
- Node.js
- pnpm `11.9.0`

### 1. 获取代码

Windows PowerShell、Linux 和 macOS 使用相同命令：

```bash
git clone https://github.com/gugugaga318/autonomous-qwen-react.git
cd autonomous-qwen-react
```

### 2. 创建本地配置

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

打开 `.env`，至少修改本地 PostgreSQL 密码：

```dotenv
POSTGRES_PASSWORD=replace_with_a_local_password
```

默认配置使用 `multi_case` 合成数据、确定性 Agent 和关键词检索，不需要 DashScope API key。

### 3. 初始化数据库

```bash
docker compose up -d db
docker compose --profile tools run --rm --build seed
```

`seed` 会执行 migration 并重置目标数据库。只应对本地或 Demo 数据库使用；不要对需要保留 RCA 与审批历史的数据库执行 reset。

### 4. 启动平台

```bash
docker compose up --build -d backend worker frontend
docker compose ps
```

启动后访问：

| 服务 | 地址 |
| --- | --- |
| Dashboard | <http://127.0.0.1:5173> |
| OpenAPI | <http://127.0.0.1:8000/docs> |
| Health | <http://127.0.0.1:8000/health> |
| Readiness | <http://127.0.0.1:8000/ready> |

### 5. 停止平台

```bash
docker compose down
```

该命令不会删除 PostgreSQL volume。完整的初始化、运行和维护说明见 [Docker Compose Deployment](docs/deployment/docker-compose.md)。

### Windows Demo 启停脚本

仓库还提供了用于录制和本地验证的 PowerShell 启动器。先配置 `YIELD_RCA_DATABASE_URL` 或 `TEST_DATABASE_URL`，再运行：

```powershell
.\scripts\start_demo.ps1
```

如果本机 execution policy 阻止仓库脚本，可以只对当前命令绕过策略：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

选择其他数据集：

```powershell
.\scripts\start_demo.ps1 -Dataset multi_case
.\scripts\start_demo.ps1 -Dataset spc_case
```

停止脚本记录的 Demo 进程：

```powershell
.\scripts\stop_demo.ps1
```

Linux/macOS 使用前述 Docker Compose 命令启动相同服务，不依赖这些 PowerShell 脚本。

## 使用方式

### 通过 YieldMind RCA Dashboard 调查

1. 打开 <http://127.0.0.1:5173>。
2. 选择 `Product` 或 `Lot ID`。
3. 使用默认问题或输入调查条件。
4. 点击 `Run RCA`。
5. 查看排队、执行、重试或完成状态。
6. 在 Investigation 与 Report 视图检查证据、根因、建议和报告。

完整演示流程见 [Demo Runbook](docs/demo-runbook.md)。

### 通过 API 创建 Product Window 调查

Windows PowerShell：

```powershell
$body = @{
  user_query = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
} | ConvertTo-Json

$job = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/rca/jobs" `
  -Headers @{ "Idempotency-Key" = "demo-job-001" } `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000$($job.state_url)"
```

Linux/macOS：

```bash
curl --request POST 'http://127.0.0.1:8000/rca/jobs' \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: demo-job-001' \
  --data '{
    "user_query": "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
  }'
```

### 审批 RCA Memory Candidate

只有满足证据门槛的 RCA 才会生成 candidate。以下 PowerShell 示例使用两名不同工程师完成审批：

```powershell
$memoryCandidate = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/rca/jobs/$($job.job_id)/memory-candidate"
$candidateId = $memoryCandidate.candidate.candidate_id

$firstApproval = @{
  engineer_id = "YE001"
  engineer_role = "yield_engineer"
  decision = "approve"
  comment = "RCA evidence reviewed."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/memory/candidates/$candidateId/approvals" `
  -ContentType "application/json" `
  -Body $firstApproval

$secondApproval = @{
  engineer_id = "PE001"
  engineer_role = "process_engineer"
  decision = "approve"
  comment = "Recipe DOE gate reviewed; no direct production change."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/memory/candidates/$candidateId/approvals" `
  -ContentType "application/json" `
  -Body $secondApproval
```

Linux/macOS 可使用 candidate ID 调用同一接口：

```bash
candidate_id='<candidate-id>'

curl --request POST \
  "http://127.0.0.1:8000/memory/candidates/${candidate_id}/approvals" \
  --header 'Content-Type: application/json' \
  --data '{
    "engineer_id": "YE001",
    "engineer_role": "yield_engineer",
    "decision": "approve",
    "comment": "RCA evidence reviewed."
  }'

curl --request POST \
  "http://127.0.0.1:8000/memory/candidates/${candidate_id}/approvals" \
  --header 'Content-Type: application/json' \
  --data '{
    "engineer_id": "PE001",
    "engineer_role": "process_engineer",
    "decision": "approve",
    "comment": "Recipe DOE gate reviewed; no direct production change."
  }'
```

接口返回 `state_url`、`events_url`、`report_url` 和 `cancel_url`。相同的 `Idempotency-Key` 与相同请求会复用 Job；同一个 key 用于不同输入时返回 `409 idempotency_conflict`。

### 通过 API 创建 Lot-Driven 调查

Windows PowerShell：

```powershell
$body = @{
  investigation_mode = "lot"
  lot_id = "LOT_A_001"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/rca/jobs" `
  -ContentType "application/json" `
  -Body $body
```

Linux/macOS：

```bash
curl --request POST 'http://127.0.0.1:8000/rca/jobs' \
  --header 'Content-Type: application/json' \
  --data '{
    "investigation_mode": "lot",
    "lot_id": "LOT_A_001"
  }'
```

### 运行纯 Python Golden Workflow

该方式直接读取现有 `data/seeds/golden_case`，不启动 API，也不会生成或修改合成数据。

Windows PowerShell：

```powershell
python scripts\run_golden_rca.py --no-print-report
```

Linux/macOS：

```bash
python scripts/run_golden_rca.py --no-print-report
```

输出文件：

```text
outputs/golden_rca_run/rca_state.json
outputs/golden_rca_run/rca_report.md
```

如果需要连接已初始化的 PostgreSQL，可以传入 `--database-url`。

## 运行模式与配置

### Agent 模式

| `YIELD_RCA_AGENT_MODE` | 行为 | 是否需要 API key |
| --- | --- | --- |
| `deterministic` | 使用确定性实现，适合默认运行和回归验证 | 否 |
| `fake` | 运行完整 LLM 路径，但不发起付费网络调用 | 否 |
| `llm` | 通过 DashScope 调用真实 Qwen | 是 |

### 编排模式

| `YIELD_RCA_ORCHESTRATION_MODE` | 行为 |
| --- | --- |
| `fixed` | 固定工作流兼容路径 |
| `controlled_react` | 对符合条件的 Lot 调查运行受控 ReAct，其他请求回退到 `fixed` |
| `llm_react` | 由 LLM 执行意图规划、下一步规划和 Specialist interpretation，确定性代码负责工具与结论门禁 |

无需付费即可验证完整 LLM 路径：

```dotenv
YIELD_RCA_AGENT_MODE=fake
YIELD_RCA_ORCHESTRATION_MODE=llm_react
```

使用真实 Qwen：

```dotenv
YIELD_RCA_AGENT_MODE=llm
YIELD_RCA_ORCHESTRATION_MODE=llm_react
YIELD_RCA_LLM_PROVIDER=dashscope
YIELD_RCA_LLM_MODEL=qwen-plus
DASHSCOPE_API_KEY=<local-secret>
```

修改 `.env` 后重新初始化数据并启动容器：

```bash
docker compose --profile tools run --rm --build seed
docker compose up --build -d backend worker frontend
```

### 主要环境变量

#### 数据与服务

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YIELD_RCA_DATABASE_URL` | 空 | 本地手动运行时的 PostgreSQL 连接；空值时核心工作流可读取 seed 文件 |
| `YIELD_RCA_SEED_DIR` | `data/seeds/golden_case` | CSV 工作流使用的数据目录 |
| `YIELD_RCA_DATASET` | `multi_case`（`.env.example`） | Compose 使用的数据集：`golden_case`、`multi_case` 或 `spc_case` |
| `BACKEND_PORT` | `8000` | FastAPI 映射端口 |
| `FRONTEND_PORT` | `5173` | Dashboard 映射端口 |

#### LLM

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YIELD_RCA_LLM_PROVIDER` | `dashscope` | LLM provider |
| `YIELD_RCA_LLM_MODEL` | `qwen-plus` | 模型名称 |
| `YIELD_RCA_LLM_BASE_URL` | DashScope compatible-mode URL | API 地址 |
| `YIELD_RCA_LLM_TIMEOUT_SECONDS` | `60` | 请求超时 |
| `YIELD_RCA_LLM_MAX_RETRIES` | `1` | HTTP 重试上限 |
| `DASHSCOPE_API_KEY` | 空 | 真实 Qwen 所需密钥；不得提交到 Git |

#### Queue Worker

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YIELD_RCA_WORKER_LEASE_SECONDS` | `180` | Job lease 时长 |
| `YIELD_RCA_WORKER_HEARTBEAT_SECONDS` | `30` | Worker heartbeat 间隔，必须小于 lease |
| `YIELD_RCA_WORKER_POLL_SECONDS` | `1` | 队列轮询间隔 |
| `YIELD_RCA_WORKER_RECOVERY_SECONDS` | `30` | stale lease 恢复间隔 |
| `YIELD_RCA_WORKER_RETRY_BASE_SECONDS` | `5` | transient failure 重试退避基数 |

每个 Job 的 `max_attempts=3`；只有可识别的 provider 或 transport transient failure 会进行有限重试。

#### Knowledge Retrieval

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE` | `keyword` | 安全默认值；`hybrid` 需要 embedding index |
| `YIELD_RCA_KNOWLEDGE_EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model |
| `YIELD_RCA_KNOWLEDGE_EMBEDDING_DEVICE` | `auto` | 推理设备 |
| `YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED` | `0` | Reranker feature flag |
| `YIELD_RCA_CAUSAL_SCOPE_ENABLED` | `1` | 启用 causal-wide retrieval scope |
| `YIELD_RCA_CAUSAL_SCOPE_CANDIDATE_BUDGET` | `20` | 候选总预算 |

完整变量、模型 revision 和 Reranker 参数见 [.env.example](.env.example)。

## API 概览

### RCA Job

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/rca/jobs` | 创建异步 RCA Job |
| `GET` | `/rca/jobs/{job_id}` | 查询状态、结果与队列元数据 |
| `POST` | `/rca/jobs/{job_id}/cancel` | 请求取消 Job |
| `GET` | `/rca/jobs/{job_id}/events` | 通过 SSE 读取有序 Job Events |
| `GET` | `/rca/jobs/{job_id}/report` | 获取已完成 Job 的报告 |
| `GET` | `/rca/jobs/{job_id}/memory-candidate` | 获取 Job 生成的 memory candidate |

Job 未完成时请求报告会返回结构化 `409 job_not_completed`。

### Memory 与 Knowledge

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/memory/candidates/{candidate_id}` | 查询 RCA memory candidate |
| `POST` | `/memory/candidates/{candidate_id}/approvals` | 提交工程师审批 |
| `POST` | `/knowledge/lookups` | 查询受治理的知识库 |
| `POST` | `/knowledge/ingestions` | 上传并创建 knowledge ingestion candidate |
| `GET` | `/knowledge/ingestions` | 列出 ingestion candidates |
| `GET` | `/knowledge/ingestions/{candidate_id}` | 查询 ingestion candidate |
| `POST` | `/knowledge/ingestions/{candidate_id}/approvals` | 审批 ingestion candidate |

### 运维

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 进程健康检查 |
| `GET` | `/ready` | 数据库、知识库和运行配置 readiness |
| `GET` | `/metrics` | Prometheus metrics |

请求和响应模型以 <http://127.0.0.1:8000/docs> 中的 OpenAPI 为准。异步队列细节见 [Async Job Queue](docs/async-job-queue.md)。

## 数据集

数据生成是显式离线流程。FastAPI、Worker 和 Dashboard 启动时不会生成或重置 Fab 数据。

| 数据集 | 主要用途 |
| --- | --- |
| `golden_case` | 固定 golden regression；包含 `40N_SOC` 与 `LOT_A_001` 调查 |
| `multi_case` | 多场景可靠性验证：Cu CMP equipment-window excursion、single-Wafer scratch、ILD odd/even Wafer split |
| `spc_case` | 高级 SPC：I-MR、Xbar-S、Xbar-R、p-chart、Nelson Rules、capability 与 hold 关系 |

切换数据集：

```dotenv
YIELD_RCA_DATASET=golden_case
```

然后重新执行 seed：

```bash
docker compose --profile tools run --rm --build seed
```

数据生成器和输出位置：

```text
scripts/generate_synthetic_fab_data.py        -> data/seeds/golden_case/
scripts/generate_synthetic_multi_case_data.py -> data/seeds/multi_case/
scripts/generate_synthetic_spc_data.py        -> data/seeds/spc_case/
```

场景与数据合同见 [Multi-Case Validation](docs/multi-case-validation.md) 和 [Advanced SPC](docs/advanced-spc.md)。

## 评测与验证

### 免费、可重复的评测

基础离线评测：

```bash
python scripts/run_evaluation.py
```

Autonomous Fake-Qwen、Controlled ReAct、Fixed Workflow 与语义负例：

```bash
python scripts/run_autonomous_qwen_evaluation.py
```

通过时预期报告：Autonomous Fake `10/10`、Controlled ReAct `PASS`、Fixed Workflow `10/10`，并通过 material-trace negative case。产物写入：

```text
outputs/autonomous_qwen_react_evaluation/results.json
outputs/autonomous_qwen_react_evaluation/report.md
```

Evaluation V2 的 Retrieval、RCA 与四类 release gate：

```bash
python scripts/run_evaluation_v2_retrieval.py \
  --embedding-backend sentence-transformers \
  --device auto
python scripts/run_evaluation_v2_rca.py
python scripts/run_evaluation_v2_release.py
```

最终报告分别判断 Data Quality、Governance、Retrieval Quality 和 RCA Quality，不将四项折叠成一个整体 `PASS`。没有明确执行真实 Qwen 评测时，RCA gate 记为 `BLOCKED`，不会用 Fake-LLM 结果替代。

### 真实 Qwen：安全入口与调用上限

真实 Qwen 命令可能产生 DashScope 费用。不要把 API key 写入命令历史、日志、输出文件或 Git；通过交互式安全输入设置当前进程的 `DASHSCOPE_API_KEY`。

Windows PowerShell：

```powershell
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
  $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password

  # 单次 smoke test：最多 12 次 LLM 调用
  $env:RUN_REAL_QWEN_TEST = "1"
  & .\.venv\Scripts\python.exe tests\integration\test_qwen_optional.py -v

  # Planner 诊断：每次最多 2 次，3 runs 最多 6 次
  & .\.venv\Scripts\python.exe scripts\run_qwen_intent_diagnosis.py `
    --confirm-paid-qwen `
    --runs 3

  # Planner reliability：每个 run 最多 20 次
  & .\.venv\Scripts\python.exe scripts\run_qwen_reliability_evaluation.py `
    --confirm-paid-qwen `
    --runs 3 `
    --max-llm-calls-per-run 20

  # Evaluation V2：7 个场景，每个场景最多 16 次
  & .\.venv\Scripts\python.exe scripts\run_evaluation_v2_rca.py `
    --run-real-qwen `
    --confirm-paid-qwen `
    --max-qwen-calls-per-scenario 16
  & .\.venv\Scripts\python.exe scripts\run_evaluation_v2_release.py
} finally {
  Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:RUN_REAL_QWEN_TEST -ErrorAction SilentlyContinue
  Remove-Variable qwenSecret -ErrorAction SilentlyContinue
}
```

Linux/macOS：

```bash
read -rsp 'DashScope API key: ' DASHSCOPE_API_KEY
echo
export DASHSCOPE_API_KEY

# 单次 smoke test：最多 12 次 LLM 调用
RUN_REAL_QWEN_TEST=1 python tests/integration/test_qwen_optional.py -v

# Planner 诊断：每次最多 2 次，3 runs 最多 6 次
python scripts/run_qwen_intent_diagnosis.py \
  --confirm-paid-qwen \
  --runs 3

# Planner reliability：每个 run 最多 20 次
python scripts/run_qwen_reliability_evaluation.py \
  --confirm-paid-qwen \
  --runs 3 \
  --max-llm-calls-per-run 20

# Evaluation V2：7 个场景，每个场景最多 16 次
python scripts/run_evaluation_v2_rca.py \
  --run-real-qwen \
  --confirm-paid-qwen \
  --max-qwen-calls-per-scenario 16
python scripts/run_evaluation_v2_release.py

unset DASHSCOPE_API_KEY
```

真实 Qwen smoke test 在没有 API key 或 `RUN_REAL_QWEN_TEST=1` 时会显示 skipped。其他付费 runner 缺少 `--confirm-paid-qwen` 时会拒绝启动。

评测设计、指标和边界见：

- [Evaluation](docs/evaluation.md)
- [Evaluation V2 Causal Scope Specification](docs/evaluation-v2-causal-scope-spec.md)
- [Retrieval Evaluation](docs/retrieval-evaluation.md)

## 本地开发

### 安装 Python 环境

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

需要本地 embedding runtime 时另外安装：

```bash
python -m pip install -e '.[retrieval]'
```

### 手动启动后端与 Worker

默认 HTTP 路径只负责 enqueue。要让 Job 从 `queued` 进入执行状态，需要设置 `YIELD_RCA_DATABASE_URL`，并同时运行独立 Worker。

Windows PowerShell：

```powershell
$env:YIELD_RCA_DATABASE_URL = "postgresql://user:password@127.0.0.1:5432/yield_rca"
.\.venv\Scripts\python.exe -m uvicorn yield_rca_api.app:app `
  --app-dir backend --host 127.0.0.1 --port 8000
```

在第二个 PowerShell 窗口运行：

```powershell
$env:YIELD_RCA_DATABASE_URL = "postgresql://user:password@127.0.0.1:5432/yield_rca"
.\.venv\Scripts\python.exe scripts\run_rca_worker.py
```

Linux/macOS：

```bash
export YIELD_RCA_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/yield_rca'
.venv/bin/python -m uvicorn yield_rca_api.app:app \
  --app-dir backend --host 127.0.0.1 --port 8000
```

在第二个终端运行：

```bash
export YIELD_RCA_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/yield_rca'
.venv/bin/python scripts/run_rca_worker.py
```

### 启动前端

Windows PowerShell：

```powershell
Set-Location frontend
pnpm install
pnpm dev
```

Linux/macOS：

```bash
cd frontend
pnpm install
pnpm dev
```

Vite 开发服务器位于 <http://127.0.0.1:5173>，并将 `/api` 代理到 <http://127.0.0.1:8000>。

### 质量检查

Python：

```bash
python tools/quality.py lint
python tools/quality.py type-check
python tools/quality.py unit
python tools/quality.py contract
python tools/quality.py integration
python tools/quality.py performance
python tools/quality.py benchmark
python tools/quality.py test-all
```

也可以直接运行已安装的开发工具：

```bash
python -m ruff check .
python -m mypy
python -m pytest
```

前端：

```bash
cd frontend
pnpm run check
pnpm run build
pnpm preview
```

更多开发说明见 [Core Development Guide](docs/development.md)。

## 技术栈

YieldMind RCA 将多智能体调查、Evidence-Grounded Reasoning、异步执行和工程工作台组合为一套可部署架构。

| 层级 | 技术 |
| --- | --- |
| Core / Backend | Python 3.11+、FastAPI、Uvicorn、Pydantic、Psycopg |
| Agent / LLM | deterministic workflow、Controlled ReAct、Qwen、DashScope |
| Data / Retrieval | PostgreSQL 16、pgvector、`BAAI/bge-m3`、可选 `bge-reranker-v2-m3` |
| Frontend | React 19、TypeScript 5.8、Vite 7、ECharts 6、React Markdown |
| Queue / Streaming | PostgreSQL leased queue、独立 Worker、SSE |
| Observability | structured logging、audit event、Prometheus metrics |
| Test / Quality | pytest、Ruff、mypy、Vitest |
| Deployment | Docker Compose、Nginx |

## 项目结构

```text
.
├── backend/yield_rca_api/       # FastAPI、异步 Job API、Store、Worker 与审计
├── core/yield_rca_core/         # Domain model、Tool、Agent、RCA、SPC、Retrieval
├── data/
│   ├── seeds/                   # golden_case、multi_case、spc_case
│   ├── knowledge/               # 合成知识语料
│   └── evaluation/              # 评测数据与人工复核记录
├── db/migrations/               # PostgreSQL schema migrations
├── docker/                      # Backend、Worker、Frontend 构建文件
├── docs/                        # 架构、部署、评测与专题规范
├── frontend/                    # React Dashboard
├── outputs/                     # RCA 与评测产物
├── scripts/                     # 数据生成、seed、Worker、运行与评测入口
├── tests/                       # Unit、contract、integration、performance tests
├── tools/                       # 统一质量检查入口
├── compose.yaml
├── pyproject.toml
└── README.md
```

## 设计边界

以下边界保证多智能体调查始终受到数据来源与 Evidence 约束。

### 必须遵守的边界

1. 合成 Fab 数据只能通过离线脚本生成，不能在 API 请求或服务启动时生成。
2. Agent 不能直接访问数据库或 Repository，必须通过 Tool Layer 获取工程数据。
3. RCA 结论必须引用可追溯 Evidence。
4. React 只负责提交请求和展示后端结果，不计算 SPC 或 RCA。
5. FastAPI 是核心工作流的 HTTP adapter，不在 HTTP 请求内执行默认异步任务。
6. 只有工程师确认并发布的知识才能成为高权重历史证据。
7. LLM 负责受约束的规划和解释；Tool 执行、Evidence 校验、冲突门禁与最终支持阈值保持确定性。

### 当前未覆盖

- Raw FDC sensor stream processing。
- Vision Agent 与缺陷图片分析。
- 真实 Fab 的实时数据接入。
- 完整生产级 SPC 平台。
- 大规模工业性能目标和真实 Fab 准确率承诺。
- 生产级身份认证、权限隔离和密钥托管。

YieldMind RCA 当前使用 `hypothesis_v1` 作为正式 RCA reasoning engine。历史 snapshot DTO 仍可读取，但 Legacy reasoning engine 已不再执行，也不可配置。

## 文档导航

### 设计与架构

- [完整设计文档](DESIGN_DOC_FULL.md)
- [系统架构](docs/architecture/system-overview.md)
- [Agent 架构](docs/architecture/agent-architecture.md)
- [数据架构](docs/architecture/data-architecture.md)
- [Autonomous Qwen ReAct 规范](docs/autonomous-qwen-react-spec.md)
- [项目架构与代码讲解](docs/project-architecture-code-guide.md)

### 运行与运维

- [Docker Compose Deployment](docs/deployment/docker-compose.md)
- [Demo Runbook](docs/demo-runbook.md)
- [Async Job Queue](docs/async-job-queue.md)
- [Observability](docs/observability.md)

### RCA、SPC 与 Knowledge

- [Minimal SPC](docs/minimal-spc.md)
- [Advanced SPC](docs/advanced-spc.md)
- [Improvement Agent](docs/improvement-agent.md)
- [Memory Approval](docs/memory-approval.md)
- [Knowledge Ingestion and Lookup](docs/knowledge-ingestion-lookup.md)
- [Hybrid Retrieval](docs/hybrid-retrieval.md)

### 评测

- [Evaluation](docs/evaluation.md)
- [Evaluation V2 Causal Scope Specification](docs/evaluation-v2-causal-scope-spec.md)
- [Retrieval Evaluation](docs/retrieval-evaluation.md)

这些文档与 `docs/adr/` 下的 ADR 是架构与实现约束的正式来源；设计变更应通过 ADR 记录。

## 常见问题

### Job 为什么一直停留在 `queued`？

默认 API 只将 Job 写入队列。确认 PostgreSQL、`backend` 和独立 `worker` 均在运行：

```bash
docker compose ps
docker compose logs worker
```

### 为什么启动服务后没有自动生成数据？

这是明确的设计边界。数据生成与数据库 seed 都是显式离线操作。首次运行或切换数据集后执行：

```bash
docker compose --profile tools run --rm --build seed
```

### 不配置 DashScope API key 能运行吗？

可以。默认 `deterministic` 模式不需要 API key；`fake + llm_react` 可以在不产生费用的情况下验证完整 LLM 路径。

### 为什么报告接口返回 `409 job_not_completed`？

Job 仍处于 `queued`、`running`、`retry_wait` 或其他非完成状态。先查询 `/rca/jobs/{job_id}`，或通过 `/rca/jobs/{job_id}/events` 等待终态。

### 可以把合成数据结果当作真实 Fab 性能吗？

不可以。仓库中的结论、置信度和评测指标只适用于固定的合成数据与评测协议。

## 联系方式

如有问题或建议，请发送邮件至 [1084786731@qq.com](mailto:1084786731@qq.com)。

## 待确认项

- <!-- TODO: 待确定并添加正式 LICENSE 文件。 -->
