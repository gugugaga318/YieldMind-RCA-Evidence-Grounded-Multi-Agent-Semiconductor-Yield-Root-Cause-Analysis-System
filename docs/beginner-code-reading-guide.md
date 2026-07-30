# 从零读懂这个项目：代码学习教程

这不是一份要你背下来的文档。目标是：你能顺着一次 RCA 请求，把“用户输入”跟到“有证据的结论”，并能解释每一层为什么存在。

建议每次只学 30-45 分钟：先读本节，再打开对应源码，最后做一个小练习。不要一开始通读所有文件。

## 开始前：先建立一张地图

一次请求的主干是：

```text
用户请求
  -> Planner 生成任务计划
  -> Supervisor 调度专家 Agent
  -> Agent 调用 Tool 查询数据
  -> Agent 生成 Finding（带 Evidence）
  -> Hypothesis Engine 生成/验证/排序根因
  -> Report + Improvement + Memory Approval
```

你会经常看到这几个词：

| 名称 | 小白解释 | 类比 |
| --- | --- | --- |
| Repository | 读取 CSV 或 PostgreSQL 的数据适配器 | 图书馆书库 |
| Tool | 将一次业务查询包装成可复用调用 | 图书管理员 |
| Agent | 用多个 Tool 得出一个专业结论 | 某一科专家 |
| Evidence | 可引用、可验证的事实 | 带编号的证据卡 |
| Finding | Agent 写出的阶段性结论 | 专家意见 |
| Hypothesis | 一个待确认的根因候选 | 嫌疑人 |
| RCAState | 整个案件的统一档案 | 案卷 |

## 1. 先读 Workflow：谁把所有零件装起来？

打开 [workflow.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\workflow.py)。这个文件是“应用组装器”，只负责连接对象，不应写具体 RCA 规则。

先看 `build_workflow()` 的核心：

```python
mes_agent = MESAgent(
    find_affected_lots_tool=FindAffectedLotsTool(repository),
    analyze_lot_genealogy_tool=AnalyzeLotGenealogyTool(repository),
    get_lot_context_tool=GetLotContextTool(repository),
    find_impact_lots_tool=FindImpactLotsTool(repository),
)

fdc_agent = FDCAgent(
    analyze_parameter_shift_tool=AnalyzeParameterShiftTool(repository),
    find_ooc_events_tool=FindOocEventsTool(repository),
    perform_basic_spc_analysis_tool=PerformBasicSpcAnalysisTool(repository),
    analyze_spc_evidence_tool=advanced_spc_tool,
)
```

逐行理解：

- `repository` 是数据来源；它可以是 CSV，也可以是 PostgreSQL。
- `FindAffectedLotsTool(repository)` 表示 Tool 依赖数据，但 Agent 不依赖数据库细节。
- `MESAgent(...)` 通过构造函数拿到自己被允许使用的 Tool；这叫**依赖注入**。
- 优点：测试时可以换成假的 Repository；未来从 CSV 换 PostgreSQL 时，Agent 基本不动。

随后，所有 Agent 被交给 Supervisor：

```python
supervisor = Supervisor(
    mes_agent=mes_agent,
    fdc_agent=fdc_agent,
    defect_wat_agent=defect_wat_agent,
    knowledge_agent=knowledge_agent,
    rca_reasoning_agent=RCAReasoningAgent(...),
    improvement_agent=ImprovementAgent(...),
    report_generator=ReportGenerator(),
)
```

`run()` 的关键流程如下：

```python
task_plan = self.planner.plan(user_query, plan_id=plan_id, lot_id=lot_id)
job = RCAJob(job_id=job_id, user_query=user_query, ...)
state = self.supervisor.execute(job, task_plan)
return RCAState.from_dict({
    **state.to_dict(),
    "execution_metadata": metadata,
})
```

这里先“计划”再“执行”。`metadata` 记录 LLM token、LLM/Tool 耗时和调用次数，这叫**可观测性**：出了问题时，知道慢在哪里、是否真的调用了模型。

### 小练习 1

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_golden_rca.py --no-print-report
```

然后打开 `outputs/golden_rca_run/rca_state.json`，找到 `execution_metadata`、`findings` 和 `hypotheses`。试着回答：本次运行用了哪些 Agent？每个 Agent 返回了什么？

## 2. 再读 Models 与 Evidence：系统怎样防止“数据乱传”？

先打开 [evidence_models.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\evidence_models.py)，再打开 [models.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\models.py)。

### 2.1 为什么不用到处传 `dict`？

Python 的字典灵活，但也危险：`{"confidence": 1.5}`、漏掉 evidence ID、拼错字段名，都可能悄悄混进报告。项目使用 `@dataclass(frozen=True)` 建立数据合同。

下面是 `AgentFinding` 的简化版本：

```python
@dataclass(frozen=True)
class AgentFinding:
    finding_id: str
    agent: str
    summary: str
    confidence: float
    evidence_ids: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[Warning] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    task_id: str | None = None
    finding_kind: str = ""

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)
        _validate_string_list(self.evidence_ids, "evidence_ids", allow_empty=False)
```

- `dataclass`：自动帮你生成构造函数，适合“主要承载数据”的类。
- `frozen=True`：创建后不允许随意改字段，减少共享对象被意外修改。
- `__post_init__`：构造完成后立即校验。例如置信度必须在 0 到 1、证据 ID 不可为空。
- `finding_kind`：同一个 Agent 可以产生不同阶段的 Finding。例如 Knowledge 有 discovery 和 validation 两种结果。

`Hypothesis` 还将证据分成三类：

```python
supporting_evidence_ids: list[str]
contradicting_evidence_ids: list[str]
neutral_evidence_ids: list[str]
status: str  # candidate / supported / conflicted / inconclusive / rejected
```

这比只放一个 `evidence_ids` 更严谨：工程判断不能只收集支持自己的事实，也要保存反证。

### 2.2 Evidence 的价值

可以把一条 Evidence 想成：`EV_FDC_SLURRY_FLOW` 这张证据卡说明“什么实体、在哪个来源、观察到什么”。Finding 引用卡片 ID，Hypothesis 再引用 Finding 的卡片 ID，报告只展示已引用的卡片。

这种设计提供了三个能力：

1. **追溯**：报告中一个结论可以回到原始来源；
2. **校验**：程序能检查“引用的 ID 是否存在”；
3. **复用**：同一证据同时用于 UI、报告、建议和记忆快照。

### 小练习 2

打开 `tests/unit/test_evidence_models.py`。找到故意构造非法 Evidence/AgentFinding 的测试。尝试预测每个测试为什么应报错，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_evidence_models.py -q
```

## 3. Tool Layer 与 Specialist Agents：数据如何变成专家意见？

打开 [tool_layer.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\tool_layer.py) 和 [specialist_agents.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\specialist_agents.py)。

### 3.1 Tool 与 Agent 的职责差异

一个 Tool 应像一个明确的函数：输入条件，查询 Repository，返回结构化事实。它不应该自行判断“根因就是泵坏了”。

```python
# Tool 的思想（伪代码）
class FindOocEventsTool:
    def run(self, *, equipment_id: str, chamber_id: str, time_window: dict) -> ToolResult:
        rows = self.repository.find_ooc_events(equipment_id, chamber_id, time_window)
        return ToolResult(data=rows, evidence=[...])
```

Agent 则负责把多个 Tool Result 合成一个专业 Finding：

```python
# FDC Agent 的思想（伪代码）
parameter_shift = self.analyze_parameter_shift_tool.run(...)
ooc_events = self.find_ooc_events_tool.run(...)
spc = self._select_advanced_or_basic_spc(...)

return AgentFinding(
    agent="fdc",
    summary="Cu CMP chamber shows slurry-flow drift and OOC events.",
    evidence_ids=parameter_shift.ids + ooc_events.ids + spc.ids,
    details={"parameter_summary": ..., "event_count": ...},
)
```

这叫**关注点分离**：

- Tool：如何取数、如何形成事实；
- Agent：事实对本专业意味着什么；
- Hypothesis Engine：跨专业事实是否足以支持根因。

### 3.2 MES Agent 的思路

MES Agent 的重点是“范围”。它寻找 affected Lots、共同操作/设备/腔体和 impact Lots。不要把 `affected lots` 与 `impact lots` 混为一谈：前者是已观测到异常的群体，后者是同一异常暴露窗口下可能需要处置的群体。

### 3.3 FDC 与 SPC 的回退

FDC Agent 先尝试 Advanced SPC；仅当它没有可分析参数才回退到 Basic SPC。最终 Finding 只带最终选中的一组 SPC Evidence。

这样做的原因是“证据一致性”：如果同时放 Advanced 和 Basic 的不同控制限/结果，后续 Hypothesis Engine 无法知道应信任哪个版本。

### 3.4 Defect/WAT Agent 的作用

FDC 说明过程参数异常，不代表一定造成产品问题。Defect/WAT Agent 从缺陷、漏电、量测等症状验证**物理后果是否一致**。因此它是独立的交叉验证，而不是给 FDC 加更多描述。

### 小练习 3

读 `tests/contract/test_fdc_spc_typed_evidence_contracts.py`，重点搜索 “fallback”。你会看到回退时证据集合必须满足的规则。然后读 `tests/contract/test_mes_typed_evidence_contracts.py`，比较 MES 和 FDC 输出的结构有哪些共同点。

## 4. Hypothesis Engine：真正决定“能不能确认”的地方

打开 [hypothesis_engine.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\hypothesis_engine.py)，这是最值得精读的文件。

### 4.1 先理解输入

它的输入不是数据库行，也不是用户自然语言，而是已经产出的 `list[AgentFinding]`：

```python
def analyze(
    self,
    *,
    request_id: str,
    findings: list[AgentFinding],
    mode: str = "shadow",
) -> dict[str, Any]:
```

这表示 Hypothesis Engine 不关心“怎么查数据”，只关心“已有证据是否足够”。这让它可以脱离 API 和数据库独立做单测。

### 4.2 由签名生成候选

项目用一个非常可读的规则表定义已知过程签名：

```python
_SIGNATURE_RULES = (
    ("slurry_flow", "slurry delivery degradation"),
    ("carrier_pressure", "carrier pressure instability"),
    ("wf6_flow", "WF6 delivery degradation"),
    ("deposition_rate", "deposition rate excursion"),
)
```

然后 `_signature_candidate()` 检查：MES 是否给出了共同 chamber，FDC 是否显示某参数下降超过阈值。

```python
if chamber and parameters.get(parameter, 0.0) <= -5.0:
    return f"{chamber} {failure_mode}"
```

这不是“AI 猜测”，而是把工程经验编码成显式、可评审规则。未来可把规则移到版本化 YAML 或数据库，避免每次改变阈值都改 Python。

### 4.3 支持、反驳和拒答

`_candidate_payload()` 做了关键工作：

```python
if outcome == "supporting":
    supporting.extend(result_evidence)
    base_score = min(1.0, base_score + 0.05)
elif outcome == "contradicting":
    contradicting.extend(result_evidence)
    base_score = max(0.0, base_score - 0.20)
else:
    neutral.extend(result_evidence)

status = "conflicted" if contradicting else (
    "supported" if confidence >= 0.75 else "candidate"
)
```

要注意：历史知识的验证结果只会小幅加分或减分；它不能替代当前 MES/FDC/Defect-WAT 的证据。这就是为什么项目能避免“找到了相似文本，所以当前根因已确认”的错误。

另外 `_has_conflicting_physics()` 会识别不合理的物理组合，例如 slurry flow 明显降低却估计 removal rate 明显上升。冲突不会被忽略，而会让系统给出 `conflicted` 或 `inconclusive`。

### 4.4 为什么置信度最多 0.95？

```python
supported_threshold: float = 0.75
maximum_confidence: float = 0.95
```

这是产品决策，不是数学定理。它告诉使用者：即使证据很强，系统仍保留工程复核空间。工业 RCA 中的“绝对确定”通常不可信。

### 小练习 4

打开 `tests/contract/test_hypothesis_engine_contracts.py`。选择一个冲突场景，先不运行测试，手画三列：supporting / contradicting / neutral。再运行该文件，检查你的判断是否和代码一致。

## 5. 两阶段 RAG 和审批闭环：历史经验怎样安全地被使用？

打开 [knowledge_retrieval.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\core\yield_rca_core\knowledge_retrieval.py) 和 [memory.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\backend\yield_rca_api\memory.py)。

### 5.1 RAG 不是“把资料塞给模型”

当前的 `KeywordRetriever` 是一个可解释的第一版检索器：根据症状、工艺模块、设备、参数等关键词把历史 case 排序。它输出的是**候选**，不是答案。

流程是：

```text
当前异常 -> KeywordRetriever 找候选 case（discovery）
         -> 使用当前 Typed Evidence 验证每个 case（validation）
         -> Hypothesis Engine 将结果计入支持/反驳/中性证据
```

这叫 two-stage RAG。第 1 阶段追求“不漏掉可能相似的案例”；第 2 阶段追求“不能误把相似当成相同”。

### 5.2 Memory Candidate 不是立刻写入知识库

只有 `supported` RCA 才能生成候选。候选保存的是证据白名单快照，而不是原始 Fab 表：

```text
observed fact + evidence type + entities + source + confidence + provenance
```

这样既能回溯“为什么沉淀这条知识”，又不会把大量敏感原始生产数据复制到知识文档。

### 5.3 双人审批在代码里如何落地？

`MemoryApprovalService` 是业务服务层：

```python
def decide(self, *, candidate_id, engineer_id, engineer_role, decision, ...):
    approval = MemoryApproval(
        approval_id=f"APPROVAL_{uuid4().hex.upper()}",
        candidate_id=candidate_id,
        engineer_id=engineer_id.strip().upper(),
        engineer_role=engineer_role,
        decision=decision,
    )
    return self.store.commit_decision(...)
```

- Service：实施审批业务规则；
- Store：可替换的持久化接口；有 `InMemoryMemoryStore`（测试/CSV）和 `PostgresMemoryStore`（真实数据库）；
- PostgreSQL 提交时使用 `SELECT ... FOR UPDATE`，避免两个审批请求同时修改同一候选导致竞态；
- 每个工程师只能决策一次；两个不同工程师批准才发布；Recipe 建议还需要至少一个 Process Engineer。

发布后才向 `rca_case`、`knowledge_document` 和 `knowledge_index_update` 写入记录，同时创建审计事件。这是典型的 **human-in-the-loop + auditability** 设计。

### 小练习 5

阅读 `tests/contract/test_memory_approval_contracts.py`，列出以下三种情况的预期结果：

1. 同一个工程师重复 approve；
2. 两位不同工程师 approve 普通候选；
3. 包含 Recipe 建议，但两位都不是 Process Engineer。

然后运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_memory_approval_contracts.py -q
```

## 6. 用 Contract Tests 和 Evaluation 学会“把设计变成规则”

打开 `tests/contract/` 与 [run_evaluation.py](C:\Users\ybt\Documents\Codex\semiconductor-yield-rca-v1-upgrade\scripts\run_evaluation.py)。

### 6.1 Contract Test 是什么？

普通测试常问：“函数返回正确结果了吗？”

Contract Test 还问：“模块之间的约定有没有被破坏？”例如：

- FDC 回退时不能同时传两种 SPC Evidence；
- Finding 的 `evidence_ids` 必须和 first-class Evidence 一致；
- Knowledge 不能单凭历史案例确认根因；
- API 返回结构必须与前端 DTO 约定一致；
- 审批前的 candidate 不能被检索器当作 confirmed knowledge。

对 Multi-Agent 系统来说，合同测试尤其重要，因为错误常发生在“每个组件单看都对，但组合起来语义错了”。

### 6.2 Evaluation 是什么？

`run_evaluation.py` 运行 10 个离线场景，关注的不只是“top-1 是否对”，还有：

- `top1` / `top3`：正确根因是否排在前面；
- `inconclusive`：不确定时是否正确拒答；
- `hallucinated_citations`：报告是否引用了不存在的证据；
- `tool_p95` / `e2e_p95`：工具与端到端延迟。

运行命令：

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py --output-dir outputs\evaluation
```

完成后读 `outputs/evaluation/report.md`，不要只看 PASS：逐个看哪些案例测试了缺失数据、冲突和正确拒答。这些往往比黄金正例更能体现系统可靠性。

## 7. 推荐的 7 天学习计划

| 天数 | 任务 | 你应该能回答的问题 |
| --- | --- | --- |
| Day 1 | Workflow + 跑 golden case | 一次请求从哪里进入、由谁调度？ |
| Day 2 | Evidence/Models + 单测 | 为什么所有结论都需要 Evidence ID？ |
| Day 3 | Tool Layer + MES Agent | 为什么 Agent 不应该直接查数据库？ |
| Day 4 | FDC/SPC + Defect/WAT | 参数异常和产品后果如何交叉验证？ |
| Day 5 | Hypothesis Engine | 系统何时支持、冲突或拒答？ |
| Day 6 | RAG + Memory Approval | 历史案例为何不能自动成为新知识？ |
| Day 7 | Contract/Evaluation + 复述 | 如何证明系统不只是“看起来能跑”？ |

## 8. 读代码时的三个实用方法

1. **先找输入和输出**：看函数签名、返回类型、对应测试，再读中间逻辑。
2. **跟一条 Evidence ID**：例如全局搜索 `EV_FDC_SLURRY_FLOW`，观察它从 Tool 到 Finding、Hypothesis、报告如何流动。
3. **先读测试再读实现**：测试用最短的方式写清了“作者不允许发生什么”。这是学工程代码最快的方法。

等你看完这一轮后，下一步最值得做的是挑一个简单的改动：新增一个参数签名规则及其 contract test。这样你会真正练到领域模型、Tool 输出、Hypothesis Engine 和测试如何一起演进。
