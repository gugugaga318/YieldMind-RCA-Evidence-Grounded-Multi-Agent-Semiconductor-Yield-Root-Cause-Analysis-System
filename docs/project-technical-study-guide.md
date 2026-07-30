# 半导体良率 RCA Multi-Agent 系统：技术学习与求职讲解

> 适用场景：项目复盘、面试自我介绍、系统设计讨论。本文以当前仓库实现为准，并明确区分已交付 MVP 与生产化演进项。

## 1. 一句话介绍

这是一个面向半导体良率异常根因分析（RCA）的**证据约束型 Multi-Agent 系统**。用户输入产品时间窗或异常 Lot，系统通过 MES、FDC/SPC、Defect/WAT 和历史知识四类数据，输出可追溯的根因假设、支持/反驳证据、处置建议和可审批的知识沉淀候选。

它要解决的不是“让大模型猜一个原因”，而是把 Yield Engineer 的排查过程结构化：**工具取数 → Typed Evidence → 专家发现 → 假设生成与验证 → 结论门控 → 报告/改进/审批**。

## 2. 业务问题与范围

晶圆厂良率下降的排查往往需要跨 MES、设备 FDC、缺陷检测、WAT 电测和历史案例。人工分析的主要难点是数据源分散、影响范围不清、分析结论不可复核，以及相似历史经验可能被误用。

本项目支持两种入口：

1. **产品 + 时间窗**：定位某产品在时间窗内的异常和共同暴露。
2. **Lot 驱动**：从一个已知异常 Lot 反查产品、工艺路径、OOC 时间窗和受影响 Lot。

当前 MVP 有意不做原始高频 FDC 流处理、实时接入、视觉分析、完整 SPC 平台和大规模在线性能优化。这是重要的边界：项目证明了 RCA 决策链，而不是宣称已经替代 Fab 生产系统。

## 3. 总体架构

```text
React Dashboard
       │ HTTP
       ▼
FastAPI API ── Job/Report/Memory DTO
       │
       ▼
PurePythonRCAWorkflow
       │
 Planner → Supervisor → MES / FDC / Defect-WAT / Knowledge Agents
                           │
                      Tool Layer
                           │
                 CSV Seed Repository / PostgreSQL Repository
                           │
       RCAReasoningAgent → HypothesisEngine → ReportGenerator
                           │
                    ImprovementAgent → Memory Candidate → Engineer Approval
```

核心分层原则：

- **Frontend 只展示**，不计算 SPC 或 RCA。
- **FastAPI 只做接口和组装**，不生成合成数据，也不承载推理规则。
- **Agent 不能直接访问数据库**，必须通过 Tool Layer 取得受控、可追溯的结果。
- **结论必须引用 Evidence ID**；LLM 只能参与受约束的解释，不能越过确定性门控。

实现入口位于 `core/yield_rca_core/workflow.py`：它把 Repository、Tools、Agents、Supervisor 和 Planner 组装为无需 HTTP 也可运行的纯 Python 工作流。这使核心逻辑易于单测、可被 API 和脚本复用。

## 4. 数据与领域建模

### 4.1 数据源

数据层同时支持离线 CSV 种子和 PostgreSQL：

- MES：Lot genealogy、process history、recipe change、hold history；
- FDC：参数特征摘要、OOC event、SPC baseline；
- 质量：defect summary、WAT / metrology result；
- 知识：历史 RCA case 及其状态；
- 记忆：当前 RCA 产生的候选、审批和索引状态。

合成数据生成与数据库 seed 是离线步骤；API 启动不会重置或生成 Fab 数据。这样避免演示数据逻辑污染运行时，也符合生产系统的职责边界。

### 4.2 Typed Evidence

`evidence_models.py` 定义了机器可校验的证据模型。证据不是一段自由文本，而是有 ID、类型、来源、实体、观察值、置信度和 provenance 的结构化对象。Agent 输出 `AgentFinding`，其中至少包含：

- `agent`：MES、FDC、Defect/WAT、Knowledge、RCA Reasoning 或 Improvement；
- `finding_kind`：区分同一 Agent 的不同阶段结果；
- `evidence_ids`：结论的可追溯依据；
- `details`：结构化明细，而非不可验证的长文本；
- `warnings`：数据缺失、冲突或不应升级结论的原因。

这层设计是项目最重要的工程基础：报告、假设、记忆快照和 UI 都复用同一证据合同，避免“展示的证据”和“推理使用的证据”不一致。

## 5. Agent 工作流如何实现

### 5.1 Planner 与 Supervisor

Planner 将用户请求转化为任务计划，识别调查模式、产品、Lot 和时间窗。Supervisor 按依赖关系调度专家 Agent，并将多个 Finding 汇总进 `RCAState`。`finding_kind` 和 `task_id` 使一个 Agent 可以有多个 Finding，而不会因为以 Agent 名称作唯一键而相互覆盖。

### 5.2 四类专家 Agent

| Agent | 主要工具与工作 | 输出价值 |
| --- | --- | --- |
| MES Agent | 查询 affected/impact Lots、工艺路径、共同设备/腔体、Recipe 变化 | 确定异常暴露范围与共同性 |
| FDC Agent | 参数漂移、OOC、Basic/Advanced SPC | 找出设备或参数异常信号 |
| Defect/WAT Agent | 缺陷、电测、量测症状汇总 | 验证物理后果是否与过程异常一致 |
| Knowledge Agent | 关键词检索候选、再用当前证据验证 | 提供历史先例，但不能单独确认根因 |

FDC 有明确的优先级：如果 Advanced SPC 已配置且能得出可分析参数，就使用它；若没有可分析参数，才回退 Basic SPC，并且只将**最终选中的 SPC evidence**交给 Finding，避免两个版本的控制图证据相互干扰。

### 5.3 SPC 实现边界

Basic SPC 使用基线、样本标准差、3-sigma 控制限和点/趋势规则。Advanced SPC 支持 I-MR、Xbar-S、Xbar-R、p-chart、Nelson Rules 1-8、能力指数和版本化基线。其输出都被包装为 `EV_SPC_*` 证据，而不是只生成一张图或一句“异常”。

这里的设计重点不是做一个全功能 SPC 产品，而是把“控制图发现”变为 RCA 可消费、可引用、可测试的事实。

## 6. 两阶段 RAG 与知识使用原则

知识检索采用两阶段方式：

1. **Discovery**：`KeywordRetriever` 基于症状、模块、设备、参数等字段检索历史候选；
2. **Validation**：使用当前 MES/FDC/Defect-WAT 的证据逐条验证候选是否支持、反驳或数据不足。

因此，历史案例的定位作用被保留，但不会取代当前事件证据。一个相似案例不能仅凭文本相似度，把当前不确定的事件提升为 `supported`。当前实现为关键词检索；Embedding、pgvector、Hybrid Search 与 reranker 是刻意延期的演进项。

## 7. Hypothesis Engine：从“Agent 回答”到“受门控决策”

正式 RCA 引擎是 `hypothesis_v1`，代码在 `hypothesis_engine.py`。它是确定性的，不依赖数据库、Tool、LLM 或报告模块，便于独立验证。

主要步骤：

1. 从 MES/FDC 提取设备签名，例如共同腔体与 `slurry_flow` 明显下降组合成 slurry delivery degradation 候选；
2. 从 Recipe 变更和知识检索补充候选；
3. 汇总 MES 暴露共同性、FDC 参数/OOC 强度、Defect/WAT 物理症状；
4. 将知识验证的 supporting、contradicting、neutral evidence 分开保存；
5. 对显式正常/排除证据和物理冲突施加惩罚或拒绝；
6. 排序后按阈值决定 `supported`、`candidate`、`conflicted` 或 `inconclusive`。

最大置信度被限制在 0.95；支持阈值为 0.75。这种“有上限、有反证、有拒答”的设计，比让模型输出 100% 把握更接近工程 RCA 的风险控制要求。

Batch 15 先以 Shadow Mode 让新引擎与旧逻辑并行比较；Batch 16 通过 feature flag 切换；Batch 19 移除了旧 RCA 推理运行路径。旧记录的反序列化和页面展示兼容仍保留，因此升级不会让历史结果不可读。

## 8. LLM 的位置与安全边界

系统提供 `deterministic`、`fake`、`llm` 三种模式。可选 LLM 为 DashScope `qwen-plus`，可用于 Planner、专家解释、排序摘要和 Improvement summary。

但 Tool 查询、Evidence 校验、冲突门控、最终 supported 阈值与正式 RCA 决策仍然是确定性的。LLM 返回 JSON 后还要校验字段、ID 集合和证据集合；不合格输出会被拒绝。执行元数据记录模型、prompt 版本、token、LLM 延迟、Tool 调用次数和耗时，支持可观测性与审计。

面试时可以把它概括为：**LLM 用于受限的语言理解与表达，确定性代码负责事实、权限和最终安全决策。**

## 9. 报告、改进建议与知识记忆

### 9.1 Report 与 Improvement Agent

Report Generator 从 `RCAState` 生成 Markdown 报告，包含调查范围、根因候选、证据链、SPC 和建议。

Improvement Agent 在 RCA 之后运行，输出五类建议：containment、corrective、recipe optimization、preventive、fab-system optimization。每一条建议都必须带 Evidence IDs。若 RCA 不确定，系统只保留 containment 与补充验证，不允许给出根因特异或 Fab 级优化建议。

### 9.2 审批后才可成为知识

对于 `supported` 结论才创建 memory candidate。记忆保存的是被引用 Typed Evidence 的白名单快照（观察、类型、实体、来源、置信度），不复制原始 Fab 表数据。

发布规则：两位不同工程师批准；若包含 Recipe 建议，批准人中至少一位必须是 Process Engineer。只有已发布的 `CONFIRMED` memory 才能作为高权重历史知识进入后续检索。这体现了“模型输出不直接自我训练”的知识治理思路。

## 10. API、前端与部署

FastAPI 提供同步 Job API：创建 RCA、读取状态、读取报告、读取/审批记忆候选，以及健康检查和 metrics。当前 Job 保存在进程内，这是 MVP 的明确取舍；重启会丢失 Job，生产环境应替换为持久化 Job 表和异步 worker。

React + TypeScript + Vite Dashboard 调用 API 并展示报告、证据链、模型/工具可观测性和审批信息。Docker Compose 包含 PostgreSQL、后端、Nginx 前端和显式 `seed` 工具服务；正常启动不会 seed 或 reset 数据库。后端容器采用 read-only 文件系统与 `/tmp` tmpfs，属于不错的基础运行时安全实践。

## 11. 测试与验证策略

测试分层包括：

- 单元测试：Evidence、领域模型、SPC、LLM Gateway；
- Contract 测试：各 Agent 输入输出、Typed Evidence、API、Memory、Hypothesis Engine 合同；
- 集成测试：纯 Python workflow、FastAPI、黄金案例、多案例、SPC；
- 可选集成：PostgreSQL migration、真实 Qwen；
- 离线评估：10 个正例、负例、冲突、缺失数据和正确拒答场景。

最近一次完整本地验证结果：Python `269 passed, 3 skipped`，mypy 通过；前端 TypeScript 与 Vitest `6/6` 通过；离线评估 10/10 通过，Top-1/Top-3 100%，无 hallucinated citations。全量 Ruff 仍有 20 条存量格式问题，集中在合成数据脚本和测试文件，不是功能回归，但应在发布前清理。

## 12. 项目亮点（可直接用于面试）

1. **Evidence-first，而非 Prompt-first**：用 Typed Evidence 和 ID 追踪把 AI 输出落到可复核的工程事实。
2. **工具与 Agent 解耦**：Agent 不能直连数据库，数据访问集中在 Tool Layer，便于权限、缓存、审计和替换数据源。
3. **知识不越权**：两阶段 RAG 保证历史案例只能辅助当前证据，不能凭相似度制造“已确认根因”。
4. **确定性假设门控**：显式支持证据、反驳证据、物理冲突与正确拒答，避免 LLM 把推测包装为结论。
5. **渐进式迁移治理**：Shadow → feature flag cutover → legacy runtime cleanup，同时保留历史数据读取兼容。
6. **改进建议也可追溯**：建议不是附加的泛化文本，每条都绑定 Evidence ID，并随 RCA 状态限制权限。
7. **Human-in-the-loop 知识闭环**：双工程师审批、Recipe 角色门槛、快照化 provenance，降低错误知识回流风险。
8. **可测试、可演示、可部署**：核心纯 Python、API、前端、Docker、合成数据与离线评估都具备，适合展示完整工程能力。

## 13. 可以提升的方向

| 优先级 | 改进方向 | 当前状态 | 建议方案 |
| --- | --- | --- | --- |
| P0 | Job 持久化与异步执行 | Job 在进程内，同步处理 | PostgreSQL job 表 + Redis/Celery 或 Temporal；支持重试、取消、状态机与幂等键 |
| P0 | 认证授权与审计 | 有审批规则，缺少完整身份体系 | OIDC/RBAC、审批人身份映射、不可篡改 audit log、数据访问权限 |
| P0 | 真实数据接入与数据质量 | 使用合成 CSV/可选 PostgreSQL | 建 CDC/ETL、数据契约、schema evolution、缺失/延迟/异常数据监控 |
| P1 | 检索质量 | 当前是 KeywordRetriever | 混合检索（BM25 + embedding + metadata filter）+ reranker；离线 recall/nDCG 评估 |
| P1 | 生产可观测性 | 有基本 metrics 和调用元数据 | OpenTelemetry tracing、Prometheus/Grafana、按 case/evidence 的审计检索、成本告警 |
| P1 | 评估规模和统计显著性 | 10 个离线场景 | 扩展为按工艺模块分层的标注集；盲测、置信区间、回归门禁、人工复核一致性 |
| P1 | API 与前端可靠性 | 同步 API、基础 Dashboard | SSE/WebSocket 进度、错误态/重试、证据 drill-down、导出 PDF、可访问性测试 |
| P2 | RCA 规则可配置化 | 规则在 Python 中 | 用版本化 YAML/数据库配置管理 signature、阈值、OCAP；引入变更审批与回放 |
| P2 | 多租户/多 Fab 扩展 | 单一演示数据模型 | tenant/site 隔离、设备字典映射、单位标准化、区域数据合规 |
| P2 | 安全与供应链 | Docker 基础加固已做 | Secret manager、镜像扫描/SBOM、依赖锁定、SAST/DAST、备份与灾备演练 |

## 14. 90 秒面试讲法

“我做的是一个半导体良率根因分析 Multi-Agent MVP。它的核心不是让 LLM 直接给结论，而是先把 MES、FDC/SPC、Defect/WAT 和历史案例统一为带 ID 的 Typed Evidence。Agent 不能直连数据库，只能经由 Tool Layer 取数；Planner 和 Supervisor 编排四类专家 Agent。之后由一个独立、确定性的 Hypothesis Engine 生成和验证根因候选，显式保存支持、反驳和中性证据，并在证据不足或物理冲突时拒绝确认结论。历史案例采用两阶段 RAG，只能增强当前证据，不能单独把猜测变成 confirmed。RCA 结果会生成可追溯报告和分层改进建议，最终只有经过两位工程师审批的证据快照才能进入长期知识库。项目有 FastAPI、React、Docker、离线评估和 269 项后端测试；下一步我会优先补齐异步持久化、真实数据治理和 Hybrid RAG。”

## 15. 建议的学习路径

1. 先读 `core/yield_rca_core/workflow.py`，理解依赖如何组装；
2. 再读 `evidence_models.py` 和 `models.py`，理解数据合同；
3. 阅读 `tool_layer.py` 与 `specialist_agents.py`，理解从数据到专家 Finding；
4. 重点精读 `hypothesis_engine.py`、`rca_reasoning_agent.py`，掌握证据门控；
5. 阅读 `knowledge_retrieval.py`、`memory.py`，理解 RAG 与审批闭环；
6. 最后通过 `tests/contract/` 和 `scripts/run_evaluation.py` 学习如何把架构规则固化为可执行验证。

## 16. 相关项目文档

- `README.md`：运行、接口和演示入口；
- `DESIGN_DOC_FULL.md`：完整设计背景；
- `docs/architecture/`：系统、Agent、数据架构；
- `docs/advanced-spc.md`：Advanced SPC 数据合同；
- `docs/evaluation.md`：离线评估定义；
- `docs/memory-approval.md`：知识审批和发布约束；
- `docs/adr/ADR-005-qwen-hybrid-agent-observability.md`：LLM 混合模式与可观测性决策。
