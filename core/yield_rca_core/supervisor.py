"""Supervisor orchestration for the pure Python Yield RCA workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from yield_rca_core.causal_scope import explicit_module_limit_requested
from yield_rca_core.evidence_collection import EvidenceCollection
from yield_rca_core.improvement_agent import ImprovementAgent
from yield_rca_core.investigation_models import (
    ActionRecord,
    ConclusionLevel,
    DecisionType,
    EvidenceGapStatus,
    GoalStatus,
    IntentPlan,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    PlannerDecisionOutcome,
    StopReason,
)
from yield_rca_core.investigation_policy import ACTION_REGISTRY, InvestigationPolicy
from yield_rca_core.llm_gateway import (
    LLMCallError,
    LLMClient,
    LLMOutputValidationError,
    LLMRequest,
)
from yield_rca_core.models import (
    AgentFinding,
    AgentKind,
    AgentMode,
    AgentTask,
    FindingKind,
    Hypothesis,
    HypothesisStatus,
    InvestigationMode,
    LotDrivenRCAError,
    ModelValidationError,
    RCAJob,
    RCAState,
    TaskPlan,
    TaskStatus,
    Warning,
)
from yield_rca_core.next_action_planner import (
    LLM_REACT_EXECUTABLE_ACTION_KINDS,
    QwenNextActionPlanner,
    QwenNextActionPlannerError,
)
from yield_rca_core.question_evidence import QuestionEvidenceResolver
from yield_rca_core.rca_reasoning_agent import RCAReasoningAgent
from yield_rca_core.report_generator import ReportGenerator
from yield_rca_core.specialist_agents import DefectWATAgent, FDCAgent, KnowledgeAgent, MESAgent
from yield_rca_core.specialist_v2 import SpecialistV2Error, SpecialistV2Executor
from yield_rca_core.workflow_events import emit_workflow_event

SUPERVISOR_EXECUTABLE_AGENTS = frozenset(
    {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
        AgentKind.KNOWLEDGE.value,
        AgentKind.RCA_REASONING.value,
        AgentKind.IMPROVEMENT.value,
    }
)

SPECIALIST_TOOL_ALLOWLISTS = {
    AgentKind.MES.value: [
        "find_affected_lots",
        "get_lot_context",
        "find_impact_lots",
        "analyze_lot_genealogy",
    ],
    AgentKind.FDC.value: [
        "analyze_parameter_shift",
        "find_ooc_events",
        "perform_basic_spc_analysis",
        "analyze_spc_evidence",
    ],
    AgentKind.DEFECT_WAT.value: ["summarize_defect_wat"],
    AgentKind.KNOWLEDGE.value: ["retrieve_similar_case"],
}


def _llm_call_fallback_diagnostics(error: LLMCallError) -> dict[str, Any]:
    """Expose only bounded, credential-free provider failure facts."""

    diagnostics: dict[str, Any] = {
        "orchestration_fallback_failure_category": error.failure_category,
        "orchestration_fallback_call_attempt_count": error.call_attempt_count,
    }
    optional_values = {
        "orchestration_fallback_status_code": error.status_code,
        "orchestration_fallback_provider_code": error.provider_code,
        "orchestration_fallback_provider_message": error.provider_message,
        "orchestration_fallback_request_id": error.request_id,
    }
    diagnostics.update(
        {key: value for key, value in optional_values.items() if value is not None}
    )
    return diagnostics


def _open_question_gaps(questions: list[InvestigationQuestion]) -> list[str]:
    return [
        question.question_id
        for question in questions
        if question.status == EvidenceGapStatus.OPEN.value
    ]


def _conclusion_cap(state: RCAState, goal: InvestigationGoal) -> str:
    statuses = {hypothesis.status for hypothesis in state.hypotheses}
    if HypothesisStatus.CONFLICTED.value in statuses:
        return ConclusionLevel.CONFLICTED.value
    if HypothesisStatus.SUPPORTED.value in statuses:
        return ConclusionLevel.SUPPORTED.value
    if HypothesisStatus.CANDIDATE.value in statuses:
        return ConclusionLevel.CANDIDATE.value
    if statuses & {
        HypothesisStatus.INCONCLUSIVE.value,
        HypothesisStatus.REJECTED.value,
    }:
        return ConclusionLevel.INCONCLUSIVE.value
    if not state.evidence:
        return ConclusionLevel.INCONCLUSIVE.value

    finding_agents = {finding.agent for finding in state.findings}
    if (
        goal.intent == InvestigationIntent.HISTORICAL_LOOKUP.value
        and AgentKind.KNOWLEDGE.value in finding_agents
    ):
        return ConclusionLevel.CANDIDATE.value
    if goal.intent in {
        InvestigationIntent.ROOT_CAUSE.value,
        InvestigationIntent.FULL_RCA.value,
    } and {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
    } <= finding_agents:
        return ConclusionLevel.CANDIDATE.value
    return ConclusionLevel.SIGNAL.value


def _gate_conclusion_level(
    proposed_level: str,
    *,
    state: RCAState,
    goal: InvestigationGoal,
) -> str:
    """Bound Qwen's proposed level by the existing Evidence/Hypothesis gate."""

    cap = _conclusion_cap(state, goal)
    if cap == ConclusionLevel.CONFLICTED.value:
        return cap
    if proposed_level == ConclusionLevel.CONFLICTED.value:
        return ConclusionLevel.INCONCLUSIVE.value
    if proposed_level == ConclusionLevel.INCONCLUSIVE.value:
        return proposed_level
    if cap == ConclusionLevel.INCONCLUSIVE.value:
        return cap

    ordered = [
        ConclusionLevel.SIGNAL.value,
        ConclusionLevel.CANDIDATE.value,
        ConclusionLevel.SUPPORTED.value,
    ]
    return ordered[min(ordered.index(proposed_level), ordered.index(cap))]


def _reconcile_satisfied_questions(state: RCAState) -> list[InvestigationQuestion]:
    if not state.investigation_questions or not state.evidence:
        return list(state.investigation_questions)
    evidence_ids = sorted(item.evidence_id for item in state.evidence)
    answer = " ".join(
        finding.summary.strip()
        for finding in state.findings
        if finding.summary.strip()
    )
    if not answer:
        answer = (
            "The controlled fallback completed the investigation with "
            f"Evidence IDs {', '.join(evidence_ids)}."
        )
    return [
        (
            replace(
                question,
                status=EvidenceGapStatus.CLOSED.value,
                answer=answer,
                evidence_ids=evidence_ids,
                unavailable_reason=None,
            )
            if question.status == EvidenceGapStatus.OPEN.value
            else question
        )
        for question in state.investigation_questions
    ]


class SupervisorExecutionError(RuntimeError):
    """Raised when a TaskPlan cannot be executed to completion."""

    def __init__(
        self,
        message: str,
        *,
        state: RCAState | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.error_code = error_code


def _replace_task_status(plan: TaskPlan, task_id: str, status: str) -> TaskPlan:
    tasks = [
        replace(task, status=status) if task.task_id == task_id else task for task in plan.tasks
    ]
    return TaskPlan(
        plan_id=plan.plan_id,
        objective=plan.objective,
        tasks=tasks,
        schema_version=plan.schema_version,
    )


def _finding_for_task(
    state: RCAState,
    task_id: str,
    *,
    expected_agent: str | None = None,
) -> AgentFinding:
    finding = state.finding_for_task(task_id)
    if finding is None:
        raise SupervisorExecutionError(
            f"expected a finding for completed task {task_id!r}",
            state=state,
        )
    if expected_agent is not None and finding.agent != expected_agent:
        raise SupervisorExecutionError(
            f"task {task_id!r} produced {finding.agent}, expected {expected_agent}",
            state=state,
        )
    return finding


def _input_findings(state: RCAState, task: AgentTask) -> list[AgentFinding]:
    raw_task_ids = task.inputs.get("finding_task_ids")
    if raw_task_ids is None:
        task_ids = list(task.depends_on)
    elif isinstance(raw_task_ids, list) and all(
        isinstance(item, str) and item.strip() for item in raw_task_ids
    ):
        task_ids = list(raw_task_ids)
    else:
        raise SupervisorExecutionError(
            f"task {task.task_id!r} has invalid finding_task_ids",
            state=state,
        )
    if len(task_ids) != len(set(task_ids)):
        raise SupervisorExecutionError(
            f"task {task.task_id!r} has duplicate finding_task_ids",
            state=state,
        )
    return [_finding_for_task(state, task_id) for task_id in task_ids]


def _finding_for_agent(
    findings: list[AgentFinding],
    agent: str,
    *,
    state: RCAState,
) -> AgentFinding:
    matches = [finding for finding in findings if finding.agent == agent]
    if len(matches) != 1:
        raise SupervisorExecutionError(
            f"expected exactly one selected {agent} finding, found {len(matches)}",
            state=state,
        )
    return matches[0]


def _latest_finding_for_agent(
    findings: list[AgentFinding],
    agent: str,
    *,
    state: RCAState,
) -> AgentFinding:
    matches = [finding for finding in findings if finding.agent == agent]
    if not matches:
        raise SupervisorExecutionError(
            f"expected at least one selected {agent} finding",
            state=state,
        )
    return matches[-1]


def _merge_warnings(existing: list[Warning], incoming: list[Warning]) -> list[Warning]:
    warnings_by_id = {item.warning_id: item for item in existing}
    for item in incoming:
        warnings_by_id[item.warning_id] = item
    return list(warnings_by_id.values())


def _time_window(inputs: dict[str, Any], job: RCAJob) -> tuple[str | None, str | None]:
    raw_window = inputs.get("time_window", job.time_window)
    if not isinstance(raw_window, dict):
        return None, None
    start = raw_window.get("start") or raw_window.get("start_date")
    end = raw_window.get("end") or raw_window.get("end_date")
    return (str(start) if start else None, str(end) if end else None)


def _knowledge_query(
    state: RCAState,
    findings: list[AgentFinding],
) -> tuple[str, str, str]:
    mes_finding = _finding_for_agent(findings, AgentKind.MES.value, state=state)
    fdc_finding = _finding_for_agent(findings, AgentKind.FDC.value, state=state)
    defect_finding = _finding_for_agent(
        findings,
        AgentKind.DEFECT_WAT.value,
        state=state,
    )

    target_operation = str(mes_finding.details.get("target_operation_no", ""))
    raw_operation_rows = mes_finding.details.get("operation_commonality", [])
    operation_rows = (
        [item for item in raw_operation_rows if isinstance(item, dict)]
        if isinstance(raw_operation_rows, list)
        else []
    )
    operation: dict[str, Any] = next(
        (item for item in operation_rows if str(item.get("operation_no", "")) == target_operation),
        {},
    )
    module = str(operation.get("module", "")).strip()
    raw_commonality = mes_finding.details.get("target_commonality", {})
    commonality = raw_commonality if isinstance(raw_commonality, dict) else {}
    equipment_id = str(commonality.get("equipment_id", ""))
    equipment_type = equipment_id.split("_", maxsplit=1)[0] if equipment_id else ""

    terms: list[str] = [module]
    terms.extend(
        str(item.get("parameter_name", "")).replace("_", " ")
        for item in fdc_finding.details.get("parameter_summary", [])
    )
    terms.extend(
        str(item).replace("_", " ") for item in defect_finding.details.get("defect_counts", {})
    )
    terms.extend(
        str(item).replace("_", " ") for item in defect_finding.details.get("wat_fail_modes", {})
    )
    terms.extend(
        str(item.get("metric_name", "")).replace("_", " ")
        for item in defect_finding.details.get("metrology_summaries", [])
    )
    terms.extend(
        (
            f"{item.get('source_recipe_id', '')} "
            f"{item.get('source_recipe_version', '')} recipe change"
        )
        for item in mes_finding.details.get("recipe_changes", [])
    )
    query = " ".join(item for item in terms if item).strip() or state.job.user_query
    return query, module, equipment_type


def _knowledge_observation_context(
    state: RCAState,
    findings: list[AgentFinding],
) -> dict[str, Any]:
    """Project trusted operational findings into non-causal observation facts."""

    mes_finding = _finding_for_agent(findings, AgentKind.MES.value, state=state)
    defect_finding = _finding_for_agent(
        findings,
        AgentKind.DEFECT_WAT.value,
        state=state,
    )
    raw_commonality = mes_finding.details.get("target_commonality", {})
    commonality = raw_commonality if isinstance(raw_commonality, dict) else {}
    defect_counts = defect_finding.details.get("defect_counts", {})
    symptom_types = (
        tuple(str(item) for item in defect_counts)
        if isinstance(defect_counts, dict)
        else ()
    )
    detected_at = max(
        (
            item.timestamp
            for item in defect_finding.evidence
            if item.timestamp is not None
        ),
        default="",
    )
    return {
        "source_lot_id": state.job.source_lot_id or "",
        "product_id": state.job.product_id or "",
        "detected_operation": str(
            mes_finding.details.get("target_operation_no", "")
        ),
        "detected_equipment_id": str(commonality.get("equipment_id", "")),
        "detected_at": detected_at,
        "symptom_types": symptom_types,
    }


def _knowledge_explicit_module_limit(
    state: RCAState,
    module: str,
    action_inputs: dict[str, Any] | None = None,
) -> bool:
    """Validate, rather than trust, a Planner-proposed Module restriction."""

    if action_inputs is not None and not isinstance(
        action_inputs.get("explicit_module_limit", False),
        bool,
    ):
        return False
    return explicit_module_limit_requested(
        state.job.user_query,
        module,
    )


def _legacy_preliminary_candidates(
    findings: list[AgentFinding],
    *,
    state: RCAState,
) -> list[dict[str, Any]]:
    mes_finding = _finding_for_agent(findings, AgentKind.MES.value, state=state)
    fdc_finding = _finding_for_agent(findings, AgentKind.FDC.value, state=state)
    defect_finding = _finding_for_agent(findings, AgentKind.DEFECT_WAT.value, state=state)
    candidates: dict[str, dict[str, Any]] = {}

    def add(root_cause: str, basis: str, evidence_ids: list[str]) -> None:
        normalized = root_cause.strip()
        if not normalized:
            return
        candidates.setdefault(
            normalized,
            {
                "root_cause": normalized,
                "basis": basis,
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            },
        )

    raw_commonality = mes_finding.details.get("target_commonality", {})
    commonality = raw_commonality if isinstance(raw_commonality, dict) else {}
    chamber_id = str(commonality.get("chamber_id", "")).strip()
    parameter_summary = {
        str(item.get("parameter_name", "")): item
        for item in fdc_finding.details.get("parameter_summary", [])
        if isinstance(item, dict)
    }
    signature_rules = (
        ("slurry_flow", "slurry delivery degradation"),
        ("carrier_pressure", "carrier pressure instability"),
        ("wf6_flow", "WF6 delivery degradation"),
        ("deposition_rate", "deposition rate excursion"),
    )
    for parameter_name, failure_mode in signature_rules:
        delta = float(parameter_summary.get(parameter_name, {}).get("avg_delta_percent", 0.0))
        if chamber_id and delta <= -5.0:
            add(
                f"{chamber_id} {failure_mode}",
                "legacy_fdc_signature",
                list(mes_finding.evidence_ids) + list(fdc_finding.evidence_ids),
            )
            break

    recipe_changes = [
        item for item in mes_finding.details.get("recipe_changes", []) if isinstance(item, dict)
    ]
    if recipe_changes:
        change = recipe_changes[0]
        recipe_id = str(change.get("source_recipe_id", "")).strip()
        recipe_version = str(change.get("source_recipe_version", "")).strip()
        if recipe_id and recipe_version:
            add(
                f"{recipe_id} {recipe_version} recipe version change",
                "legacy_recipe_change",
                list(mes_finding.evidence_ids) + list(defect_finding.evidence_ids),
            )

    return list(candidates.values())[:3]


def _review_specialist_finding(
    finding: AgentFinding,
    *,
    llm_client: LLMClient | None,
    agent_mode: str,
    prompt_version: str,
) -> AgentFinding:
    if agent_mode == AgentMode.DETERMINISTIC.value:
        return finding
    if llm_client is None:
        raise SupervisorExecutionError("LLM Specialist review requires an LLM client")
    try:
        response = llm_client.complete_json(
            LLMRequest(
                agent=finding.agent,
                prompt_name="specialist",
                prompt_version=prompt_version,
                payload={
                    "agent": finding.agent,
                    "allowed_tools": SPECIALIST_TOOL_ALLOWLISTS[finding.agent],
                    "deterministic_finding": finding.to_dict(),
                },
            )
        )
    except LLMCallError as exc:
        if exc.failure_category in {
            "call_limit",
            "formal_blind_call_cap",
        }:
            raise
        fallback_details: dict[str, Any] = {
            "source": "deterministic_fallback",
            "fallback_reason": "llm_call_failed",
            "failure_category": exc.failure_category,
        }
        if exc.status_code is not None:
            fallback_details["status_code"] = exc.status_code
        if exc.provider_code is not None:
            fallback_details["provider_code"] = exc.provider_code
        if exc.request_id is not None:
            fallback_details["provider_request_id"] = exc.request_id
        warning = Warning(
            warning_id=(
                f"WARN_{finding.agent.upper()}_LLM_REVIEW_FALLBACK"
            ),
            message=(
                f"The optional {finding.agent} LLM review was unavailable; "
                "the deterministic Tool-derived Finding was preserved."
            ),
            evidence_ids=list(finding.evidence_ids),
        )
        return replace(
            finding,
            details={
                **finding.details,
                "agent_mode": agent_mode,
                "llm_prompt_version": prompt_version,
                "engineering_interpretation": finding.summary,
                "specialist_review": fallback_details,
            },
            warnings=[*finding.warnings, warning],
        )
    try:
        summary = str(response.data["summary"]).strip()
        confidence = float(response.data["confidence"])
        evidence_ids = [str(item) for item in response.data["evidence_ids"]]
        interpretation = str(response.data["engineering_interpretation"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMOutputValidationError(
            "Specialist returned an invalid AgentFinding review"
        ) from exc
    if not summary or not interpretation:
        raise LLMOutputValidationError("Specialist summary and interpretation must not be empty")
    if set(evidence_ids) != set(finding.evidence_ids):
        raise LLMOutputValidationError(
            "Specialist response must preserve exactly the Tool evidence_ids"
        )
    return AgentFinding(
        finding_id=finding.finding_id,
        task_id=finding.task_id,
        agent=finding.agent,
        finding_kind=finding.finding_kind,
        summary=summary,
        confidence=confidence,
        evidence_ids=list(finding.evidence_ids),
        evidence=list(finding.evidence),
        details={
            **finding.details,
            "agent_mode": agent_mode,
            "llm_prompt_version": prompt_version,
            "engineering_interpretation": interpretation,
        },
        warnings=list(finding.warnings),
    )


@dataclass(frozen=True)
class Supervisor:
    """Execute an existing TaskPlan and maintain an immutable RCAState."""

    mes_agent: MESAgent
    fdc_agent: FDCAgent
    defect_wat_agent: DefectWATAgent
    knowledge_agent: KnowledgeAgent
    rca_reasoning_agent: RCAReasoningAgent
    improvement_agent: ImprovementAgent
    report_generator: ReportGenerator
    llm_client: LLMClient | None = None
    agent_mode: str = AgentMode.DETERMINISTIC.value
    specialist_prompt_version: str = "v1"
    specialist_v2_executor: SpecialistV2Executor | None = None

    def execute_controlled(
        self,
        job: RCAJob,
        goal: InvestigationGoal,
        *,
        policy: InvestigationPolicy | None = None,
        tool_latencies: list[dict[str, str | float]] | None = None,
    ) -> RCAState:
        """Run a bounded observation-action loop without changing fixed-plan execution."""
        state = RCAState(
            job=replace(job, status=TaskStatus.RUNNING.value),
            investigation_goal=goal,
        )
        return self._continue_controlled(
            state,
            goal,
            policy=policy,
            tool_latencies=tool_latencies,
        )

    def _continue_controlled(
        self,
        state: RCAState,
        goal: InvestigationGoal,
        *,
        policy: InvestigationPolicy | None = None,
        tool_latencies: list[dict[str, str | float]] | None = None,
    ) -> RCAState:
        """Resume controlled ReAct from the supplied state, including after LLM fallback."""

        active_policy = policy or InvestigationPolicy()
        observed_tool_latencies = tool_latencies if tool_latencies is not None else []
        while True:
            decision = active_policy.next_action(
                goal=goal,
                findings=state.findings,
                action_records=state.action_history,
                tool_call_count=len(observed_tool_latencies),
            )
            if decision.next_action is not None:
                remaining_tool_calls = max(
                    0,
                    goal.max_tool_calls - len(observed_tool_latencies),
                )
                required_tool_calls = self._controlled_action_tool_cost(
                    decision.next_action,
                    state,
                )
                if required_tool_calls > remaining_tool_calls:
                    # Ask the policy for its normal budget terminal so fallback
                    # execution cannot cross the global Tool-call boundary.
                    decision = active_policy.next_action(
                        goal=goal,
                        findings=state.findings,
                        action_records=state.action_history,
                        tool_call_count=goal.max_tool_calls,
                    )
            if decision.next_action is None:
                questions = (
                    _reconcile_satisfied_questions(state)
                    if decision.goal_status == GoalStatus.SATISFIED.value
                    else list(state.investigation_questions)
                )
                evidence_gaps = list(
                    dict.fromkeys(
                        [
                            *decision.evidence_gaps,
                            *_open_question_gaps(questions),
                        ]
                    )
                )
                terminal = replace(
                    state,
                    job=replace(state.job, status=TaskStatus.COMPLETED.value),
                    investigation_questions=questions,
                    goal_status=decision.goal_status,
                    conclusion_level=decision.conclusion_level,
                    evidence_gaps=evidence_gaps,
                    stop_reason=decision.stop_reason,
                )
                emit_workflow_event(
                    "investigation_stopped",
                    {
                        "mode": "controlled_react",
                        "goal_status": decision.goal_status,
                        "conclusion_level": decision.conclusion_level,
                        "stop_reason": decision.stop_reason,
                        "evidence_gaps": evidence_gaps,
                    },
                )
                report = self.report_generator.generate(terminal)
                return replace(terminal, report=report)

            emit_workflow_event(
                "action_started",
                {
                    "action_id": decision.next_action.action_id,
                    "action_kind": decision.next_action.kind,
                    "agent": decision.next_action.agent,
                    "reason": decision.next_action.reason,
                },
            )
            finding = self._dispatch_controlled(decision.next_action, state)
            state = self._record_controlled_finding(state, decision.next_action, finding)
            emit_workflow_event(
                "action_completed",
                {
                    "action_id": decision.next_action.action_id,
                    "action_kind": decision.next_action.kind,
                    "agent": finding.agent,
                    "finding_id": finding.finding_id,
                    "summary": finding.summary,
                    "evidence_ids": list(finding.evidence_ids),
                    "confidence": finding.confidence,
                },
            )

    def _controlled_action_tool_cost(
        self,
        action: InvestigationAction,
        state: RCAState,
    ) -> int:
        """Return the conservative V1 Tool cost used for budget preflight."""

        if action.kind in {
            "inspect_defect_pattern",
            "validate_shared_defect_pattern",
            "validate_historical_case",
        }:
            return 1
        if action.kind == "find_shared_exposure":
            lot_id = str(
                action.inputs.get("lot_id") or state.job.source_lot_id or ""
            ).strip()
            return 3 if lot_id else 2
        if action.kind == "inspect_fdc_spc":
            return 4 if self.fdc_agent.analyze_spc_evidence_tool is not None else 3
        return 0

    def execute_llm_react(
        self,
        job: RCAJob,
        intent_plan: IntentPlan,
        planner: QwenNextActionPlanner,
        *,
        fallback_policy: InvestigationPolicy | None = None,
        tool_latencies: list[dict[str, str | float]] | None = None,
    ) -> RCAState:
        """Let Qwen choose one registered Agent action after every observation."""

        state = RCAState(
            job=replace(job, status=TaskStatus.RUNNING.value),
            investigation_goal=intent_plan.goal,
            capability_notices=list(intent_plan.capability_notices),
            investigation_questions=list(intent_plan.questions),
            execution_metadata={
                "orchestration_requested_mode": "llm_react",
                "orchestration_mode": "llm_react",
            },
        )
        unsupported_kinds = {
            notice.capability
            for notice in intent_plan.capability_notices
            if not notice.supported
        }
        if unsupported_kinds:
            questions = [
                replace(
                    question,
                    status=EvidenceGapStatus.UNAVAILABLE.value,
                    answer=None,
                    evidence_ids=[],
                    unavailable_reason=next(
                        (
                            notice.reason
                            for notice in intent_plan.capability_notices
                            if notice.capability == question.question_kind
                        ),
                        "The requested capability is not configured.",
                    ),
                )
                if question.question_kind in unsupported_kinds
                and question.status == EvidenceGapStatus.OPEN.value
                else question
                for question in state.investigation_questions
            ]
            state = replace(state, investigation_questions=questions)
            if not any(
                question.status == EvidenceGapStatus.OPEN.value
                for question in questions
            ):
                terminal = replace(
                    state,
                    job=replace(state.job, status=TaskStatus.COMPLETED.value),
                    goal_status=GoalStatus.BLOCKED.value,
                    conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
                    evidence_gaps=[],
                    stop_reason=StopReason.DATA_UNAVAILABLE.value,
                )
                # No investigation Evidence exists for a pure unsupported
                # request, so a traceable RCA report would be misleading.
                return terminal
        observed_tool_latencies = tool_latencies if tool_latencies is not None else []
        while True:
            try:
                outcome = planner.decide_with_review(
                    goal=intent_plan.goal,
                    questions=state.investigation_questions,
                    findings=state.findings,
                    action_records=state.action_history,
                    tool_call_count=len(observed_tool_latencies),
                    evidence=state.evidence,
                    evidence_ids=[item.evidence_id for item in state.evidence],
                    question_evidence_links=state.question_evidence_links,
                    capability_notices=state.capability_notices,
                    hypotheses=state.hypotheses,
                    prior_decisions=state.planner_decisions,
                )
                decision = outcome.decision
            except (QwenNextActionPlannerError, LLMCallError) as exc:
                reason = (
                    "qwen_next_action_output_invalid"
                    if isinstance(exc, QwenNextActionPlannerError)
                    else "qwen_next_action_call_failed"
                )
                validation_diagnostics = (
                    {
                        "orchestration_fallback_failure_category": (
                            "planner_output_invalid"
                        ),
                        "orchestration_fallback_attempt_count": exc.attempts,
                        "orchestration_fallback_validation_errors": list(
                            exc.validation_errors
                        ),
                        "orchestration_fallback_validation_error_categories": list(
                            exc.validation_error_categories
                        ),
                        "orchestration_fallback_output_parse_error_count": (
                            exc.output_parse_error_count
                        ),
                        "orchestration_fallback_core_validation_error_count": (
                            exc.core_validation_error_count
                        ),
                    }
                    if isinstance(exc, QwenNextActionPlannerError)
                    else _llm_call_fallback_diagnostics(exc)
                )
                fallback_state = replace(
                    state,
                    execution_metadata={
                        **state.execution_metadata,
                        "orchestration_requested_mode": "llm_react",
                        "orchestration_mode": "controlled_react",
                        "orchestration_fallback_reason": reason,
                        "orchestration_fallback_stage": "next_action_planning",
                        "orchestration_fallback_after_action_count": len(
                            state.action_history
                        ),
                        **validation_diagnostics,
                    },
                )
                return self._continue_controlled(
                    fallback_state,
                    intent_plan.goal,
                    policy=fallback_policy,
                    tool_latencies=observed_tool_latencies,
                )

            if decision.decision_type == DecisionType.STOP.value:
                state = self._record_planner_outcome(state, outcome)
                state = replace(
                    state,
                    execution_metadata={
                        **state.execution_metadata,
                        "planner_stop_proposed_by": outcome.decision_proposed_by,
                        "terminal_question_updates_source": (
                            outcome.question_updates_source
                        ),
                        "terminal_question_updates_validated_by": (
                            "python_evidence_gate"
                            if decision.question_updates
                            else None
                        ),
                    },
                )
                conclusion_level = _gate_conclusion_level(
                    decision.proposed_conclusion_level,
                    state=state,
                    goal=intent_plan.goal,
                )
                terminal = replace(
                    state,
                    job=replace(state.job, status=TaskStatus.COMPLETED.value),
                    goal_status=decision.goal_status,
                    conclusion_level=conclusion_level,
                    evidence_gaps=_open_question_gaps(
                        state.investigation_questions
                    ),
                    stop_reason=decision.stop_reason,
                )
                emit_workflow_event(
                    "planner_stopped",
                    {
                        "decision_id": decision.decision_id,
                        "reason": decision.reason,
                        "goal_status": decision.goal_status,
                        "conclusion_level": conclusion_level,
                        "stop_reason": decision.stop_reason,
                        "evidence_gaps": list(terminal.evidence_gaps),
                    },
                )
                if not terminal.evidence:
                    return terminal
                report = self.report_generator.generate(terminal)
                return replace(terminal, report=report)

            action = decision.next_action
            if action is None:
                raise SupervisorExecutionError(
                    "LLM act decision lost its next_action",
                    state=state,
                )
            emit_workflow_event(
                "planner_decision",
                {
                    "decision_id": decision.decision_id,
                    "decision_type": decision.decision_type,
                    "reason": decision.reason,
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "agent": action.agent,
                    "target_question_ids": list(decision.target_question_ids),
                },
            )
            emit_workflow_event(
                "action_started",
                {
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "agent": action.agent,
                    "reason": action.reason,
                },
            )
            remaining_tool_calls = max(
                0,
                intent_plan.goal.max_tool_calls - len(observed_tool_latencies),
            )
            try:
                finding = self._dispatch_llm_react(
                    action,
                    state,
                    remaining_tool_calls=remaining_tool_calls,
                )
            except SpecialistV2Error as exc:
                raise SupervisorExecutionError(str(exc), state=state) from exc

            # Build both immutable state projections before advancing the loop. A
            # Specialist or Finding validation failure therefore exposes the
            # pre-action state without a dangling Decision or Review.
            state_with_finding = self._record_controlled_finding(
                state,
                action,
                finding,
            )
            state = self._record_planner_outcome(state_with_finding, outcome)
            emit_workflow_event(
                "action_completed",
                {
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "agent": finding.agent,
                    "finding_id": finding.finding_id,
                    "summary": finding.summary,
                    "evidence_ids": list(finding.evidence_ids),
                    "confidence": finding.confidence,
                },
            )

    @staticmethod
    def _record_planner_outcome(
        state: RCAState,
        outcome: PlannerDecisionOutcome,
    ) -> RCAState:
        decision = outcome.decision
        questions_by_id = {
            question.question_id: question
            for question in state.investigation_questions
        }
        for question in getattr(decision, "question_updates", []):
            current = questions_by_id.get(question.question_id)
            if current is None:
                raise SupervisorExecutionError(
                    "Planner question update references an unknown question",
                    state=state,
                )
            if current.status != EvidenceGapStatus.OPEN.value:
                raise SupervisorExecutionError(
                    "Planner question update references a terminal question",
                    state=state,
                )
            questions_by_id[question.question_id] = replace(
                current,
                status=question.status,
                answer=question.answer,
                evidence_ids=list(question.evidence_ids),
                unavailable_reason=question.unavailable_reason,
            )
        for question in decision.new_questions:
            questions_by_id[question.question_id] = question
        return replace(
            state,
            investigation_questions=list(questions_by_id.values()),
            planner_decisions=[*state.planner_decisions, decision],
            question_update_reviews=[
                *state.question_update_reviews,
                *outcome.question_update_reviews,
            ],
        )

    def _dispatch_llm_react(
        self,
        action: InvestigationAction,
        state: RCAState,
        *,
        remaining_tool_calls: int,
    ) -> AgentFinding:
        """Dispatch one Qwen action through Specialist V2 or the RCA evidence gate."""

        definition = ACTION_REGISTRY.get(action.kind)
        if (
            definition is None
            or action.kind not in LLM_REACT_EXECUTABLE_ACTION_KINDS
            or action.agent != definition.agent
        ):
            raise SupervisorExecutionError(
                f"LLM action {action.kind!r} is not executable by {action.agent!r}",
                state=state,
            )
        if action.kind == "run_rca_reasoning":
            return self._dispatch_controlled(action, state)
        if self.specialist_v2_executor is None:
            raise SupervisorExecutionError(
                "llm_react Specialist V2 executor is not configured",
                state=state,
            )
        if remaining_tool_calls <= 0:
            raise SupervisorExecutionError(
                "the global Tool budget is exhausted before Specialist execution",
                state=state,
            )

        facts = action.inputs
        context: dict[str, Any] = {
            "investigation_intent": (
                state.investigation_goal.intent
                if state.investigation_goal is not None
                else ""
            ),
            "source_lot_id": state.job.source_lot_id,
            "user_query": state.job.user_query,
        }
        if action.kind in {
            "inspect_defect_pattern",
            "validate_shared_defect_pattern",
        }:
            source_lot_id = str(
                facts.get("lot_id") or state.job.source_lot_id or ""
            ).strip()
            lot_ids = [source_lot_id] if source_lot_id else []
            if action.kind == "validate_shared_defect_pattern" or not lot_ids:
                mes = _latest_finding_for_agent(
                    state.findings,
                    AgentKind.MES.value,
                    state=state,
                )
                raw_lot_ids = mes.details.get("affected_lots", [])
                authorized_lot_ids = (
                    raw_lot_ids if isinstance(raw_lot_ids, list) else []
                )
                selected = [
                    str(item).strip()
                    for item in authorized_lot_ids
                    if str(item).strip()
                ]
                lot_ids = list(
                    dict.fromkeys(
                        [
                            *([source_lot_id] if source_lot_id else []),
                            *selected,
                        ]
                    )
                )
            if not lot_ids:
                raise SupervisorExecutionError(
                    "defect-pattern action requires a source or MES-selected Lot scope",
                    state=state,
                )
            context.update(
                {
                    "lot_ids": lot_ids,
                    "evidence_scope": (
                        "shared_exposure_comparison"
                        if action.kind == "validate_shared_defect_pattern"
                        else "selected_lots"
                    ),
                }
            )
        elif action.kind == "find_shared_exposure":
            start_date, end_date = _time_window(facts, state.job)
            context.update(
                {
                    "lot_id": str(
                        facts.get("lot_id") or state.job.source_lot_id or ""
                    ).strip()
                    or None,
                    "product_id": str(
                        facts.get("product_id") or state.job.product_id or ""
                    ).strip()
                    or None,
                    "start_date": start_date,
                    "end_date": end_date,
                    "target_operation_no": (
                        str(facts.get("target_operation_no")).strip()
                        if facts.get("target_operation_no")
                        else None
                    ),
                }
            )
        elif action.kind == "inspect_fdc_spc":
            mes = _latest_finding_for_agent(
                state.findings,
                AgentKind.MES.value,
                state=state,
            )
            raw_commonality = mes.details.get("target_commonality", {})
            commonality = (
                raw_commonality if isinstance(raw_commonality, dict) else {}
            )
            raw_lot_ids = mes.details.get("affected_lots", [])
            lot_ids = (
                [str(item) for item in raw_lot_ids if str(item).strip()]
                if isinstance(raw_lot_ids, list)
                else []
            )
            context.update(
                {
                    "lot_ids": lot_ids,
                    "equipment_id": str(commonality.get("equipment_id", "")),
                    "chamber_id": str(commonality.get("chamber_id", "")),
                    "operation_no": str(
                        mes.details.get("target_operation_no", "")
                    ),
                }
            )
        elif action.kind == "validate_historical_case":
            selected_findings = [
                _latest_finding_for_agent(
                    state.findings,
                    agent,
                    state=state,
                )
                for agent in (
                    AgentKind.MES.value,
                    AgentKind.FDC.value,
                    AgentKind.DEFECT_WAT.value,
                )
            ]
            query, module, equipment_type = _knowledge_query(
                state,
                selected_findings,
            )
            observation = _knowledge_observation_context(state, selected_findings)
            context.update(
                {
                    "query": query,
                    "module": module,
                    "equipment_type": equipment_type,
                    **observation,
                    "explicit_module_limit": _knowledge_explicit_module_limit(
                        state,
                        module,
                        action.inputs,
                    ),
                }
            )
        else:
            raise SupervisorExecutionError(
                f"LLM Specialist action {action.kind!r} has no V2 dispatcher",
                state=state,
            )

        return self.specialist_v2_executor.execute(
            action,
            request_id=f"{state.job.job_id}:{action.action_id}",
            context=context,
            max_tool_calls=min(2, remaining_tool_calls),
        )

    def _dispatch_controlled(
        self,
        action: InvestigationAction,
        state: RCAState,
    ) -> AgentFinding:
        definition = ACTION_REGISTRY.get(action.kind)
        if (
            definition is None
            or action.kind not in LLM_REACT_EXECUTABLE_ACTION_KINDS
            or action.agent != definition.agent
        ):
            raise SupervisorExecutionError(
                f"controlled action {action.kind!r} is not executable by {action.agent!r}",
                state=state,
            )
        request_id = f"{state.job.job_id}:{action.action_id}"
        facts = action.inputs
        if action.kind in {"inspect_defect_pattern", "validate_shared_defect_pattern"}:
            lot_id = str(facts.get("lot_id") or state.job.source_lot_id or "")
            lot_ids = [lot_id] if lot_id else []
            if action.kind == "validate_shared_defect_pattern" or not lot_ids:
                mes = _latest_finding_for_agent(
                    state.findings,
                    AgentKind.MES.value,
                    state=state,
                )
                raw_mes_lot_ids = mes.details.get("affected_lots", [])
                mes_lot_ids = [
                    str(item)
                    for item in (
                        raw_mes_lot_ids
                        if isinstance(raw_mes_lot_ids, list)
                        else []
                    )
                    if str(item).strip()
                ]
                if action.kind == "validate_shared_defect_pattern" or not lot_ids:
                    lot_ids = mes_lot_ids or lot_ids
            if not lot_ids:
                raise SupervisorExecutionError(
                    "defect-pattern action requires a source or MES-selected Lot scope",
                    state=state,
                )
            finding = self.defect_wat_agent.analyze(
                request_id=request_id,
                lot_ids=lot_ids,
                evidence_scope=(
                    "shared_exposure_comparison"
                    if action.kind == "validate_shared_defect_pattern"
                    else "selected_lots"
                ),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )
        if action.kind == "find_shared_exposure":
            lot_id = str(facts.get("lot_id") or state.job.source_lot_id or "")
            if lot_id:
                finding = self.mes_agent.analyze_lot(request_id=request_id, lot_id=lot_id)
            else:
                product_id = str(facts.get("product_id") or state.job.product_id or "")
                if not product_id:
                    raise SupervisorExecutionError(
                        "shared-exposure action requires a lot_id or product_id", state=state
                    )
                finding = self.mes_agent.analyze(
                    request_id=request_id,
                    product_id=product_id,
                    start_date=str(facts.get("start_date") or "") or None,
                    end_date=str(facts.get("end_date") or "") or None,
                )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )
        if action.kind == "inspect_fdc_spc":
            mes = _latest_finding_for_agent(
                state.findings,
                AgentKind.MES.value,
                state=state,
            )
            commonality = mes.details["target_commonality"]
            finding = self.fdc_agent.analyze(
                request_id=request_id,
                lot_ids=list(mes.details["affected_lots"]),
                equipment_id=str(commonality["equipment_id"]),
                chamber_id=str(commonality["chamber_id"]),
                operation_no=str(mes.details["target_operation_no"]),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )
        if action.kind == "validate_historical_case":
            selected_findings = [
                _latest_finding_for_agent(
                    state.findings,
                    agent,
                    state=state,
                )
                for agent in (
                    AgentKind.MES.value,
                    AgentKind.FDC.value,
                    AgentKind.DEFECT_WAT.value,
                )
            ]
            query, module, equipment_type = _knowledge_query(
                state,
                selected_findings,
            )
            observation = _knowledge_observation_context(state, selected_findings)
            finding = self.knowledge_agent.analyze(
                request_id=request_id,
                query=query,
                module=module,
                equipment_type=equipment_type,
                source_lot_id=str(observation["source_lot_id"]),
                product_id=str(observation["product_id"]),
                detected_operation=str(observation["detected_operation"]),
                detected_equipment_id=str(observation["detected_equipment_id"]),
                detected_at=str(observation["detected_at"]),
                symptom_types=tuple(observation["symptom_types"]),
                explicit_module_limit=_knowledge_explicit_module_limit(
                    state,
                    module,
                    action.inputs,
                ),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )
        if action.kind == "run_rca_reasoning":
            specialists = [
                finding
                for finding in state.findings
                if finding.agent
                in {
                    AgentKind.MES.value,
                    AgentKind.FDC.value,
                    AgentKind.DEFECT_WAT.value,
                    AgentKind.KNOWLEDGE.value,
                }
            ]
            rca_finding: AgentFinding = self.rca_reasoning_agent.analyze(
                request_id=request_id,
                findings=specialists,
            )
            return rca_finding
        raise SupervisorExecutionError(
            f"controlled action {action.kind!r} has no dispatcher", state=state
        )

    def _record_controlled_finding(
        self,
        state: RCAState,
        action: InvestigationAction,
        finding: AgentFinding,
    ) -> RCAState:
        if finding.agent != action.agent:
            raise SupervisorExecutionError(
                f"action {action.action_id} expected {action.agent} finding, "
                f"got {finding.agent}",
                state=state,
            )
        available_before_action = {item.evidence_id for item in state.evidence}
        missing_required = set(action.required_evidence_ids) - available_before_action
        if missing_required:
            raise SupervisorExecutionError(
                f"action requires unavailable evidence: {sorted(missing_required)}",
                state=state,
            )
        if any(item.finding_id == finding.finding_id for item in state.findings):
            raise SupervisorExecutionError(
                f"finding_id is already recorded: {finding.finding_id}",
                state=state,
            )
        try:
            evidence = EvidenceCollection(state.evidence).merge(finding.evidence).to_list()
        except ModelValidationError as exc:
            raise SupervisorExecutionError(str(exc), state=state) from exc
        known_evidence_ids = {item.evidence_id for item in evidence}
        missing = set(finding.evidence_ids) - known_evidence_ids
        if missing:
            raise SupervisorExecutionError(
                f"controlled finding references evidence without payload: {sorted(missing)}",
                state=state,
            )
        affected_lots = list(state.affected_lots)
        impact_lots = list(state.impact_lots)
        affected_wafers = list(state.affected_wafers)
        impact_wafers = list(state.impact_wafers)
        scope_level = state.scope_level
        impact_criteria = dict(state.impact_criteria)
        updated_job = state.job
        if finding.agent == AgentKind.MES.value:
            affected_lots = list(finding.details.get("affected_lots", []))
            impact_lots = list(finding.details.get("impact_lots", []))
            affected_wafers = list(finding.details.get("affected_wafers", []))
            impact_wafers = list(finding.details.get("impact_wafers", []))
            scope_level = str(finding.details.get("scope_level", scope_level))
            impact_criteria = dict(finding.details.get("impact_criteria", {}))
            resolved_product = finding.details.get("product_id")
            if resolved_product and not updated_job.product_id:
                updated_job = replace(updated_job, product_id=str(resolved_product))
        hypotheses = list(state.hypotheses)
        if finding.agent == AgentKind.RCA_REASONING.value:
            payload = finding.details.get("hypothesis")
            if not isinstance(payload, dict):
                raise SupervisorExecutionError("RCA finding must include a hypothesis", state=state)
            hypotheses.append(Hypothesis.from_dict(payload))
        record = ActionRecord(
            action=action,
            status="completed",
            produced_finding_ids=[finding.finding_id],
            produced_evidence_ids=list(finding.evidence_ids),
            decision_summary=finding.summary,
        )
        links = QuestionEvidenceResolver().resolve(
            questions=state.investigation_questions,
            action_record=record,
            evidence=evidence,
        )
        return replace(
            state,
            job=updated_job,
            evidence=evidence,
            findings=[*state.findings, finding],
            hypotheses=hypotheses,
            affected_lots=affected_lots,
            impact_lots=impact_lots,
            affected_wafers=affected_wafers,
            impact_wafers=impact_wafers,
            scope_level=scope_level,
            impact_criteria=impact_criteria,
            action_history=[*state.action_history, record],
            question_evidence_links=[
                *state.question_evidence_links,
                *links,
            ],
            warnings=_merge_warnings(state.warnings, finding.warnings),
        )

    def execute(self, job: RCAJob, task_plan: TaskPlan) -> RCAState:
        plan_agents = {task.agent for task in task_plan.tasks}
        unsupported = plan_agents - SUPERVISOR_EXECUTABLE_AGENTS
        if unsupported:
            raise SupervisorExecutionError(
                f"TaskPlan references Agents not registered in Supervisor: {sorted(unsupported)}"
            )

        running_job = replace(job, status=TaskStatus.RUNNING.value)
        state = RCAState(job=running_job, task_plan=task_plan)
        remaining = {task.task_id for task in task_plan.tasks}

        while remaining:
            ready = [
                task
                for task in task_plan.tasks
                if task.task_id in remaining
                and set(task.depends_on) <= set(state.completed_task_ids)
            ]
            if not ready:
                raise SupervisorExecutionError(
                    "TaskPlan has no executable task; dependencies cannot be satisfied",
                    state=state,
                )

            for task in ready:
                emit_workflow_event(
                    "agent_started",
                    {
                        "task_id": task.task_id,
                        "agent": task.agent,
                        "objective": task.objective,
                    },
                )
                state = self._execute_task(state, task)
                finding = state.finding_for_task(task.task_id)
                emit_workflow_event(
                    "agent_completed",
                    {
                        "task_id": task.task_id,
                        "agent": task.agent,
                        "finding_id": finding.finding_id if finding is not None else None,
                        "summary": finding.summary if finding is not None else "",
                        "evidence_ids": (
                            list(finding.evidence_ids) if finding is not None else []
                        ),
                        "confidence": finding.confidence if finding is not None else None,
                    },
                )
                remaining.remove(task.task_id)

        if state.task_plan is None:
            raise SupervisorExecutionError("RCAState lost its TaskPlan", state=state)
        state = replace(state, current_task_id=None)
        report = self.report_generator.generate(state)
        completed_job = replace(state.job, status=TaskStatus.COMPLETED.value)
        emit_workflow_event(
            "investigation_stopped",
            {
                "mode": "fixed",
                "goal_status": "satisfied",
                "conclusion_level": (
                    state.hypotheses[-1].status if state.hypotheses else "inconclusive"
                ),
                "stop_reason": "workflow_completed",
                "evidence_gaps": [],
            },
        )
        return replace(state, job=completed_job, report=report)

    def _execute_task(self, state: RCAState, task: AgentTask) -> RCAState:
        if state.task_plan is None:
            raise SupervisorExecutionError("RCAState requires a TaskPlan", state=state)
        running_plan = _replace_task_status(
            state.task_plan,
            task.task_id,
            TaskStatus.RUNNING.value,
        )
        running_state = replace(
            state,
            task_plan=running_plan,
            current_task_id=task.task_id,
        )
        try:
            finding = self._dispatch(task, running_state)
            return self._record_finding(running_state, task, finding)
        except Exception as exc:
            failed_plan = _replace_task_status(
                running_plan,
                task.task_id,
                TaskStatus.FAILED.value,
            )
            failed_state = replace(
                running_state,
                job=replace(running_state.job, status=TaskStatus.FAILED.value),
                task_plan=failed_plan,
                current_task_id=None,
            )
            if isinstance(exc, SupervisorExecutionError):
                raise SupervisorExecutionError(
                    str(exc),
                    state=failed_state,
                    error_code=exc.error_code,
                ) from exc
            error_code = exc.error_code if isinstance(exc, LotDrivenRCAError) else None
            raise SupervisorExecutionError(
                f"task {task.task_id} failed: {exc}",
                state=failed_state,
                error_code=error_code,
            ) from exc

    def _dispatch(self, task: AgentTask, state: RCAState) -> AgentFinding:
        request_id = f"{state.job.job_id}:{task.task_id}"
        if task.agent == AgentKind.MES.value:
            if state.job.investigation_mode == InvestigationMode.LOT.value:
                lot_id = str(task.inputs.get("lot_id") or state.job.source_lot_id or "")
                if not lot_id:
                    raise SupervisorExecutionError(
                        "Lot-driven MES task requires lot_id",
                        state=state,
                        error_code="LOT_ID_REQUIRED",
                    )
                requested_operation = task.inputs.get("target_operation_no")
                finding = self.mes_agent.analyze_lot(
                    request_id=request_id,
                    lot_id=lot_id,
                    target_operation_no=(str(requested_operation) if requested_operation else None),
                )
                return _review_specialist_finding(
                    finding,
                    llm_client=self.llm_client,
                    agent_mode=self.agent_mode,
                    prompt_version=self.specialist_prompt_version,
                )
            product_id = str(task.inputs.get("product_id") or state.job.product_id or "")
            if not product_id:
                raise SupervisorExecutionError("MES task requires product_id", state=state)
            start_date, end_date = _time_window(task.inputs, state.job)
            finding = self.mes_agent.analyze(
                request_id=request_id,
                product_id=product_id,
                start_date=start_date,
                end_date=end_date,
                target_operation_no=(
                    str(task.inputs["target_operation_no"])
                    if task.inputs.get("target_operation_no")
                    else None
                ),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )

        if task.agent == AgentKind.FDC.value:
            mes_finding = _finding_for_agent(
                _input_findings(state, task),
                AgentKind.MES.value,
                state=state,
            )
            commonality = mes_finding.details["target_commonality"]
            finding = self.fdc_agent.analyze(
                request_id=request_id,
                lot_ids=list(mes_finding.details["affected_lots"]),
                equipment_id=str(commonality["equipment_id"]),
                chamber_id=str(commonality["chamber_id"]),
                operation_no=str(mes_finding.details["target_operation_no"]),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )

        if task.agent == AgentKind.DEFECT_WAT.value:
            mes_finding = _finding_for_agent(
                _input_findings(state, task),
                AgentKind.MES.value,
                state=state,
            )
            finding = self.defect_wat_agent.analyze(
                request_id=request_id,
                lot_ids=list(mes_finding.details["affected_lots"]),
            )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )

        if task.agent == AgentKind.KNOWLEDGE.value:
            input_findings = _input_findings(state, task)
            query, module, equipment_type = _knowledge_query(state, input_findings)
            observation = _knowledge_observation_context(state, input_findings)
            if task.finding_kind == FindingKind.KNOWLEDGE_VALIDATION.value:
                finding = self.knowledge_agent.validate_preliminary_candidates(
                    request_id=request_id,
                    preliminary_candidates=_legacy_preliminary_candidates(
                        input_findings,
                        state=state,
                    ),
                    module=module,
                    equipment_type=equipment_type,
                    source_lot_id=str(observation["source_lot_id"]),
                    product_id=str(observation["product_id"]),
                    detected_operation=str(observation["detected_operation"]),
                    detected_equipment_id=str(observation["detected_equipment_id"]),
                    detected_at=str(observation["detected_at"]),
                    explicit_module_limit=_knowledge_explicit_module_limit(
                        state,
                        module,
                    ),
                )
            else:
                finding = self.knowledge_agent.analyze(
                    request_id=request_id,
                    query=query,
                    module=module,
                    equipment_type=equipment_type,
                    source_lot_id=str(observation["source_lot_id"]),
                    product_id=str(observation["product_id"]),
                    detected_operation=str(observation["detected_operation"]),
                    detected_equipment_id=str(observation["detected_equipment_id"]),
                    detected_at=str(observation["detected_at"]),
                    symptom_types=tuple(observation["symptom_types"]),
                    explicit_module_limit=_knowledge_explicit_module_limit(
                        state,
                        module,
                    ),
                )
            return _review_specialist_finding(
                finding,
                llm_client=self.llm_client,
                agent_mode=self.agent_mode,
                prompt_version=self.specialist_prompt_version,
            )

        if task.agent == AgentKind.RCA_REASONING.value:
            specialist_findings = _input_findings(state, task)
            rca_finding: AgentFinding = self.rca_reasoning_agent.analyze(
                request_id=request_id,
                findings=specialist_findings,
            )
            return rca_finding

        if task.agent == AgentKind.IMPROVEMENT.value:
            improvement_inputs = _input_findings(state, task)
            improvement_finding: AgentFinding = self.improvement_agent.analyze(
                request_id=request_id,
                findings=improvement_inputs,
            )
            return improvement_finding

        raise SupervisorExecutionError(
            f"no dispatch implementation for Agent {task.agent}",
            state=state,
        )

    def _record_finding(
        self,
        state: RCAState,
        task: AgentTask,
        finding: AgentFinding,
    ) -> RCAState:
        if finding.agent != task.agent:
            raise SupervisorExecutionError(
                f"task {task.task_id} expected {task.agent} finding, got {finding.agent}",
                state=state,
            )
        if finding.task_id is not None and finding.task_id != task.task_id:
            raise SupervisorExecutionError(
                f"task {task.task_id} received finding for task {finding.task_id}",
                state=state,
            )
        if state.finding_for_task(task.task_id) is not None:
            raise SupervisorExecutionError(
                f"task {task.task_id} already has a recorded finding",
                state=state,
            )
        finding = replace(
            finding,
            task_id=task.task_id,
            finding_kind=task.finding_kind,
        )
        try:
            evidence = EvidenceCollection(state.evidence).merge(finding.evidence).to_list()
        except ModelValidationError as exc:
            raise SupervisorExecutionError(str(exc), state=state) from exc
        known_evidence_ids = {item.evidence_id for item in evidence}
        missing_evidence = set(finding.evidence_ids) - known_evidence_ids
        if missing_evidence:
            raise SupervisorExecutionError(
                f"finding references evidence without payload: {sorted(missing_evidence)}",
                state=state,
            )

        affected_lots = list(state.affected_lots)
        impact_lots = list(state.impact_lots)
        affected_wafers = list(state.affected_wafers)
        impact_wafers = list(state.impact_wafers)
        scope_level = state.scope_level
        impact_criteria = dict(state.impact_criteria)
        updated_job = state.job
        if finding.agent == AgentKind.MES.value:
            affected_lots = list(finding.details.get("affected_lots", []))
            impact_lots = list(finding.details.get("impact_lots", []))
            affected_wafers = list(finding.details.get("affected_wafers", []))
            impact_wafers = list(finding.details.get("impact_wafers", []))
            scope_level = str(finding.details.get("scope_level", scope_level))
            impact_criteria = dict(finding.details.get("impact_criteria", {}))
            resolved_product = finding.details.get("product_id")
            if resolved_product and not updated_job.product_id:
                updated_job = replace(updated_job, product_id=str(resolved_product))

        hypotheses = list(state.hypotheses)
        if finding.agent == AgentKind.RCA_REASONING.value:
            hypothesis_payload = finding.details.get("hypothesis")
            if not isinstance(hypothesis_payload, dict):
                raise SupervisorExecutionError(
                    "RCA Reasoning finding must include a hypothesis",
                    state=state,
                )
            hypotheses.append(Hypothesis.from_dict(hypothesis_payload))

        if state.task_plan is None:
            raise SupervisorExecutionError("RCAState requires a TaskPlan", state=state)
        completed_plan = _replace_task_status(
            state.task_plan,
            task.task_id,
            TaskStatus.COMPLETED.value,
        )
        return replace(
            state,
            job=updated_job,
            task_plan=completed_plan,
            current_task_id=None,
            completed_task_ids=[*state.completed_task_ids, task.task_id],
            affected_lots=affected_lots,
            impact_lots=impact_lots,
            affected_wafers=affected_wafers,
            impact_wafers=impact_wafers,
            scope_level=scope_level,
            impact_criteria=impact_criteria,
            evidence=evidence,
            findings=[*state.findings, finding],
            hypotheses=hypotheses,
            warnings=_merge_warnings(state.warnings, finding.warnings),
        )
