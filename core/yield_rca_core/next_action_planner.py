"""Qwen-backed next-action planning for bounded autonomous RCA investigations.

The model chooses one registered Agent action or an explicit stop after every
observation.  This module validates runtime safety, but deliberately does not
replace a legal model choice with the deterministic controlled-ReAct policy.
The deterministic policy is supplied only as the Fake Client's no-cost output
and as an explicit fallback hint for the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from yield_rca_core.evidence_models import Evidence
from yield_rca_core.investigation_models import (
    MAX_CROSS_DOMAIN_ACTIONS,
    MAX_INITIAL_QUESTIONS,
    ActionKind,
    ActionRecord,
    ConclusionLevel,
    DecisionType,
    EvidenceGapStatus,
    GoalStatus,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    InvestigationValidationError,
    OrchestrationMode,
    PlannerDecision,
    PlannerDecisionOutcome,
    QuestionUpdate,
    QuestionUpdateDisposition,
    QuestionUpdateReasonCode,
    QuestionUpdateReview,
    StopReason,
)
from yield_rca_core.investigation_policy import (
    ACTION_REGISTRY,
    ActionDefinition,
    InvestigationPolicy,
)
from yield_rca_core.llm_gateway import (
    LLMCallError,
    LLMClient,
    LLMOutputValidationError,
    LLMRequest,
)
from yield_rca_core.models import (
    AgentFinding,
    AgentKind,
    Hypothesis,
    ModelValidationError,
)
from yield_rca_core.question_capability import (
    QUESTION_CAPABILITY_REGISTRY,
    capability_for_question,
    validate_action_for_questions,
)
from yield_rca_core.question_update_review import review_qwen_planner_output

_OUTPUT_ATTEMPTS = 2
_CALL_RETRIES = 1
_OUTPUT_PARSE_ERROR = "output_parse"
_CORE_DECISION_VALIDATION_ERROR = "core_decision_validation"


def _is_retryable_call_error(error: LLMCallError) -> bool:
    """Retry only transient failures; configuration and call caps fail fast."""

    if error.failure_category == "call_limit":
        return False
    if error.failure_category == "transport_error":
        return True
    if error.status_code in {408, 429}:
        return True
    if error.status_code is not None and error.status_code >= 500:
        return True
    return error.failure_category == "llm_call_error" and error.status_code is None

# Only actions with a Supervisor dispatcher are visible to Qwen. Specialist
# Tool selection for these actions is bounded separately by Specialist V2.
LLM_REACT_EXECUTABLE_ACTION_KINDS = frozenset(
    {
        ActionKind.INSPECT_DEFECT_PATTERN.value,
        ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value,
        ActionKind.FIND_SHARED_EXPOSURE.value,
        ActionKind.INSPECT_FDC_SPC.value,
        ActionKind.VALIDATE_HISTORICAL_CASE.value,
        ActionKind.RUN_RCA_REASONING.value,
    }
)
LLM_REACT_ACTION_REGISTRY: Mapping[str, ActionDefinition] = MappingProxyType(
    {
        kind: ACTION_REGISTRY[kind]
        for kind in sorted(LLM_REACT_EXECUTABLE_ACTION_KINDS)
    }
)


class QwenNextActionPlannerError(LLMOutputValidationError):
    """Raised after two invalid next-action outputs require controlled fallback."""

    fallback_mode = OrchestrationMode.CONTROLLED_REACT.value

    def __init__(
        self,
        validation_errors: list[str],
        validation_error_categories: list[str],
        *,
        goal_id: str,
        completed_steps: int,
        tool_call_count: int,
    ) -> None:
        self.attempts = len(validation_errors)
        self.validation_errors = tuple(validation_errors)
        self.validation_error_categories = tuple(validation_error_categories)
        self.output_parse_error_count = self.validation_error_categories.count(
            _OUTPUT_PARSE_ERROR
        )
        self.core_validation_error_count = self.validation_error_categories.count(
            _CORE_DECISION_VALIDATION_ERROR
        )
        self.goal_id = goal_id
        self.completed_steps = completed_steps
        self.tool_call_count = tool_call_count
        super().__init__(
            "Qwen Next-action Planner returned invalid output twice; preserve the "
            f"current investigation state and fallback to {self.fallback_mode}"
        )


def _validate_string_list(values: list[str], name: str) -> None:
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ModelValidationError(f"{name} must be a list of non-empty strings")
    if len(values) != len(set(values)):
        raise ModelValidationError(f"{name} must not contain duplicates")


def _normalized_lot_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def _assert_source_lot_boundary(
    payload: dict[str, Any],
    *,
    source_lot_id: str | None,
    label: str,
) -> None:
    """Prevent an impact Lot from silently becoming a new RCA objective."""

    if source_lot_id is None:
        return
    for key, value in payload.items():
        normalized_key = key.casefold()
        if normalized_key == "lot_ids":
            if not isinstance(value, list) or any(
                _normalized_lot_id(item) is None for item in value
            ):
                raise InvestigationValidationError(
                    f"{label}.lot_ids must be a list of non-empty Lot IDs"
                )
            normalized_values = {
                normalized
                for item in value
                if (normalized := _normalized_lot_id(item)) is not None
            }
            if source_lot_id not in normalized_values:
                raise InvestigationValidationError(
                    f"{label}.lot_ids must retain the source Lot {source_lot_id}"
                )
        elif normalized_key == "lot_id" or normalized_key.endswith("_lot_id"):
            normalized_value = _normalized_lot_id(value)
            if normalized_value != source_lot_id:
                raise InvestigationValidationError(
                    f"{label}.{key} cannot replace source Lot {source_lot_id}"
                )


def _compact_finding(finding: AgentFinding) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "agent": finding.agent,
        "finding_kind": finding.finding_kind,
        "summary": finding.summary,
        "confidence": finding.confidence,
        "evidence_ids": list(finding.evidence_ids),
        "details": dict(finding.details),
    }


def _compact_evidence(evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "source_type": evidence.source_type,
        "summary": evidence.summary,
        "evidence_type": evidence.evidence_type,
        "source_agent": evidence.source_agent,
        "observation": evidence.observation,
        "confidence": evidence.confidence,
        "entities": [entity.to_dict() for entity in evidence.entities],
    }


def _strict_outcome(decision: PlannerDecision) -> PlannerDecisionOutcome:
    """Project the legacy strict path into the new outcome contract."""

    reviews = [
        QuestionUpdateReview(
            decision_id=decision.decision_id,
            disposition=QuestionUpdateDisposition.ACCEPTED.value,
            reason_code=QuestionUpdateReasonCode.ACCEPTED.value,
            reason=(
                f"QuestionUpdate {update.question_id} passed the strict "
                "PlannerDecision contract."
            ),
            update_index=index,
            question_id=update.question_id,
            claimed_status=update.status,
        )
        for index, update in enumerate(decision.question_updates)
    ]
    return PlannerDecisionOutcome(
        decision=decision,
        question_update_reviews=reviews,
        raw_question_update_count=len(decision.question_updates),
    )


def _validate_reviewed_stop_boundary(
    outcome: PlannerDecisionOutcome,
    *,
    questions: list[InvestigationQuestion],
) -> None:
    decision = outcome.decision
    if decision.decision_type != DecisionType.STOP.value:
        return
    projected_status = {
        question.question_id: question.status for question in questions
    }
    for update in decision.question_updates:
        projected_status[update.question_id] = update.status
    open_question_ids = sorted(
        question_id
        for question_id, status in projected_status.items()
        if status == EvidenceGapStatus.OPEN.value
    )
    if not open_question_ids:
        return
    if decision.stop_reason == StopReason.GOAL_SATISFIED.value:
        raise InvestigationValidationError(
            "a goal_satisfied stop cannot leave open investigation questions: "
            f"{open_question_ids}"
        )
    if decision.stop_reason == StopReason.DATA_UNAVAILABLE.value:
        raise InvestigationValidationError(
            "a data_unavailable stop must terminally mark every unavailable "
            f"investigation question: {open_question_ids}"
        )


@dataclass(frozen=True)
class QwenNextActionPlanner:
    """Select one legal next Agent action or stop after the latest observation."""

    llm_client: LLMClient
    fallback_policy: InvestigationPolicy = field(default_factory=InvestigationPolicy)
    registry: Mapping[str, ActionDefinition] = field(
        default_factory=lambda: dict(LLM_REACT_ACTION_REGISTRY)
    )
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if self.llm_client is None:
            raise ModelValidationError("Qwen Next-action Planner requires an LLM client")
        if not isinstance(self.fallback_policy, InvestigationPolicy):
            raise ModelValidationError("fallback_policy must be an InvestigationPolicy")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ModelValidationError("prompt_version must be a non-empty string")
        if set(self.registry) != LLM_REACT_EXECUTABLE_ACTION_KINDS:
            raise ModelValidationError(
                "Qwen Next-action Planner registry must contain exactly the executable "
                "Batch 20.9.3 actions"
            )
        for kind, definition in self.registry.items():
            expected = LLM_REACT_ACTION_REGISTRY[kind]
            if not isinstance(definition, ActionDefinition) or definition != expected:
                raise ModelValidationError(
                    f"Qwen Next-action Planner registry definition is invalid: {kind}"
                )
        object.__setattr__(
            self,
            "registry",
            MappingProxyType(dict(self.registry)),
        )

    def decide(
        self,
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        evidence: list[Evidence] | None = None,
        evidence_ids: list[str] | None = None,
        hypotheses: list[Hypothesis] | None = None,
        prior_decisions: list[PlannerDecision] | None = None,
        critical_contradictions: list[str] | None = None,
    ) -> PlannerDecision:
        """Preserve the strict compatibility path until Supervisor integration."""

        return self._decide(
            goal=goal,
            questions=questions,
            findings=findings,
            action_records=action_records,
            tool_call_count=tool_call_count,
            evidence=evidence,
            evidence_ids=evidence_ids,
            hypotheses=hypotheses,
            prior_decisions=prior_decisions,
            critical_contradictions=critical_contradictions,
            review_question_updates=False,
        ).decision

    def decide_with_review(
        self,
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        evidence: list[Evidence] | None = None,
        evidence_ids: list[str] | None = None,
        hypotheses: list[Hypothesis] | None = None,
        prior_decisions: list[PlannerDecision] | None = None,
        critical_contradictions: list[str] | None = None,
    ) -> PlannerDecisionOutcome:
        """Return a core decision with independently reviewed update claims."""

        return self._decide(
            goal=goal,
            questions=questions,
            findings=findings,
            action_records=action_records,
            tool_call_count=tool_call_count,
            evidence=evidence,
            evidence_ids=evidence_ids,
            hypotheses=hypotheses,
            prior_decisions=prior_decisions,
            critical_contradictions=critical_contradictions,
            review_question_updates=True,
        )

    def _decide(
        self,
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        evidence: list[Evidence] | None,
        evidence_ids: list[str] | None,
        hypotheses: list[Hypothesis] | None,
        prior_decisions: list[PlannerDecision] | None,
        critical_contradictions: list[str] | None,
        review_question_updates: bool,
    ) -> PlannerDecisionOutcome:
        """Ask Qwen for one core decision, retrying only invalid core output."""

        normalized_evidence = list(evidence or [])
        explicit_evidence_ids = list(evidence_ids or [])
        normalized_hypotheses = list(hypotheses or [])
        normalized_prior_decisions = list(prior_decisions or [])
        contradictions = list(critical_contradictions or [])
        self._validate_runtime_inputs(
            goal=goal,
            questions=questions,
            findings=findings,
            action_records=action_records,
            tool_call_count=tool_call_count,
            evidence=normalized_evidence,
            evidence_ids=explicit_evidence_ids,
            hypotheses=normalized_hypotheses,
            prior_decisions=normalized_prior_decisions,
            critical_contradictions=contradictions,
        )
        available_evidence_ids = self._available_evidence_ids(
            evidence=normalized_evidence,
            explicit_evidence_ids=explicit_evidence_ids,
            findings=findings,
            action_records=action_records,
        )
        if (
            len(action_records) >= min(goal.max_steps, MAX_CROSS_DOMAIN_ACTIONS)
            or tool_call_count >= goal.max_tool_calls
        ):
            open_questions = [
                question
                for question in questions
                if question.status == EvidenceGapStatus.OPEN.value
            ]
            return _strict_outcome(
                PlannerDecision(
                    decision_id=self._next_baseline_decision_id(
                        goal=goal,
                        prior_decisions=normalized_prior_decisions,
                    ),
                    goal_id=goal.goal_id,
                    decision_type=DecisionType.STOP.value,
                    reason=(
                        "The Python runtime budget boundary was reached before "
                        "another Qwen decision could be requested."
                    ),
                    goal_status=GoalStatus.BUDGET_EXHAUSTED.value,
                    proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
                    stop_reason=StopReason.BUDGET_EXHAUSTED.value,
                    question_updates=self._terminal_question_updates(
                        open_questions=open_questions,
                        findings=findings,
                        available_evidence_ids=available_evidence_ids,
                    ),
                )
            )
        baseline = self._baseline_decision(
            goal=goal,
            questions=questions,
            findings=findings,
            action_records=action_records,
            tool_call_count=tool_call_count,
            available_evidence_ids=available_evidence_ids,
            prior_decisions=normalized_prior_decisions,
            critical_contradictions=contradictions,
        )
        open_questions = [
            question
            for question in questions
            if question.status == EvidenceGapStatus.OPEN.value
        ]
        advertised_actions = self._advertised_actions(open_questions)
        validation_errors: list[str] = []
        validation_error_categories: list[str] = []
        call_retry_count = 0
        failed_provider_call_attempt_count = 0

        for attempt in range(1, _OUTPUT_ATTEMPTS + 1):
            request = LLMRequest(
                agent=AgentKind.PLANNER.value,
                prompt_name="next_action_planner",
                prompt_version=self.prompt_version,
                payload={
                    "goal": goal.to_dict(),
                    "questions": [question.to_dict() for question in questions],
                    "findings": [_compact_finding(finding) for finding in findings],
                    "evidence": [
                        _compact_evidence(item) for item in normalized_evidence
                    ],
                    "available_evidence_ids": sorted(available_evidence_ids),
                    "hypotheses": [
                        hypothesis.to_dict() for hypothesis in normalized_hypotheses
                    ],
                    "action_history": [
                        record.to_dict() for record in action_records
                    ],
                    "prior_decision_ids": [
                        decision.decision_id
                        for decision in normalized_prior_decisions
                    ],
                    "critical_contradictions": contradictions,
                    "budget": {
                        "completed_steps": len(action_records),
                        "max_steps": min(
                            goal.max_steps,
                            MAX_CROSS_DOMAIN_ACTIONS,
                        ),
                        "tool_call_count": tool_call_count,
                        "max_tool_calls": goal.max_tool_calls,
                    },
                    "allowed_actions": [
                        {
                            "kind": definition.kind,
                            "agent": definition.agent,
                            "required_finding_agents": list(
                                definition.required_finding_agents
                            ),
                        }
                        for definition in self.registry.values()
                        if definition.kind in advertised_actions
                    ],
                    "question_action_capabilities": {
                        question.question_id: sorted(
                            capability_for_question(question).allowed_actions
                        )
                        for question in open_questions
                    },
                    "deterministic_planner_decision": baseline.to_dict(),
                    "output_attempt": attempt,
                    "previous_validation_error": (
                        validation_errors[-1] if validation_errors else None
                    ),
                },
                temperature=0.0,
            )
            response = None
            while True:
                try:
                    response = self.llm_client.complete_json(request)
                except LLMCallError as exc:
                    failed_provider_call_attempt_count += exc.call_attempt_count
                    if (
                        call_retry_count < _CALL_RETRIES
                        and _is_retryable_call_error(exc)
                    ):
                        call_retry_count += 1
                        continue
                    raise LLMCallError(
                        "Qwen Next-action Planner call failed after its bounded retry",
                        status_code=exc.status_code,
                        provider_code=exc.provider_code,
                        provider_message=exc.provider_message,
                        request_id=exc.request_id,
                        failure_category=exc.failure_category,
                        call_attempt_count=failed_provider_call_attempt_count,
                    ) from exc
                except LLMOutputValidationError as exc:
                    message = str(exc).strip() or type(exc).__name__
                    validation_errors.append(message)
                    validation_error_categories.append(_OUTPUT_PARSE_ERROR)
                break
            if response is None:
                continue
            try:
                outcome = (
                    review_qwen_planner_output(
                        response.data,
                        questions=questions,
                        available_evidence_ids=available_evidence_ids,
                    )
                    if review_question_updates
                    else _strict_outcome(
                        PlannerDecision.from_dict(
                            response.data,
                            allow_legacy_question_updates=False,
                        )
                    )
                )
                candidate = outcome.decision
                self._validate_candidate(
                    candidate,
                    goal=goal,
                    questions=questions,
                    findings=findings,
                    action_records=action_records,
                    tool_call_count=tool_call_count,
                    available_evidence_ids=available_evidence_ids,
                    prior_decisions=normalized_prior_decisions,
                )
                if review_question_updates:
                    _validate_reviewed_stop_boundary(
                        outcome,
                        questions=questions,
                    )
                return outcome
            except (
                InvestigationValidationError,
                LLMOutputValidationError,
                KeyError,
                TypeError,
            ) as exc:
                message = str(exc).strip() or type(exc).__name__
                validation_errors.append(message)
                validation_error_categories.append(
                    _OUTPUT_PARSE_ERROR
                    if isinstance(exc, LLMOutputValidationError)
                    else _CORE_DECISION_VALIDATION_ERROR
                )

        raise QwenNextActionPlannerError(
            validation_errors,
            validation_error_categories,
            goal_id=goal.goal_id,
            completed_steps=len(action_records),
            tool_call_count=tool_call_count,
        )

    def _baseline_decision(
        self,
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        available_evidence_ids: set[str],
        prior_decisions: list[PlannerDecision],
        critical_contradictions: list[str],
    ) -> PlannerDecision:
        policy_decision = self.fallback_policy.next_action(
            goal=goal,
            findings=findings,
            action_records=action_records,
            tool_call_count=tool_call_count,
            critical_contradictions=critical_contradictions,
        )
        decision_id = self._next_baseline_decision_id(
            goal=goal,
            prior_decisions=prior_decisions,
        )
        open_questions = [
            question
            for question in questions
            if question.status == EvidenceGapStatus.OPEN.value
        ]
        action = policy_decision.next_action
        if (
            action is not None
            and action.kind in self.registry
            and open_questions
        ):
            scope = dict(action.scope or goal.known_facts or {"goal_id": goal.goal_id})
            bounded_action = replace(action, scope=scope)
            target = next(
                (
                    question
                    for question in open_questions
                    if action.kind
                    in capability_for_question(question).allowed_actions
                    and capability_for_question(question).supported
                ),
                None,
            )
            if target is None:
                return PlannerDecision(
                    decision_id=decision_id,
                    goal_id=goal.goal_id,
                    decision_type=DecisionType.STOP.value,
                    reason=(
                        "No registered Action can target the remaining typed "
                        "Questions."
                    ),
                    goal_status=GoalStatus.BLOCKED.value,
                    proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
                    stop_reason=StopReason.NO_ALLOWED_ACTION.value,
                    question_updates=self._terminal_question_updates(
                        open_questions=open_questions,
                        findings=findings,
                        available_evidence_ids=available_evidence_ids,
                    ),
                )
            return PlannerDecision(
                decision_id=decision_id,
                goal_id=goal.goal_id,
                decision_type=DecisionType.ACT.value,
                reason=action.reason,
                goal_status=GoalStatus.IN_PROGRESS.value,
                proposed_conclusion_level=policy_decision.conclusion_level,
                next_action=bounded_action,
                target_question_ids=[target.question_id],
            )

        if action is not None:
            goal_status = GoalStatus.BLOCKED.value
            stop_reason = StopReason.NO_ALLOWED_ACTION.value
        else:
            goal_status = policy_decision.goal_status
            stop_reason = (
                policy_decision.stop_reason
                or StopReason.NO_ALLOWED_ACTION.value
            )
        if not open_questions and stop_reason != StopReason.BUDGET_EXHAUSTED.value:
            goal_status = GoalStatus.SATISFIED.value
            stop_reason = StopReason.GOAL_SATISFIED.value
        question_updates = self._terminal_question_updates(
            open_questions=open_questions,
            findings=findings,
            available_evidence_ids=available_evidence_ids,
        )
        return PlannerDecision(
            decision_id=decision_id,
            goal_id=goal.goal_id,
            decision_type=DecisionType.STOP.value,
            reason=(
                "The deterministic Fake Client baseline reached an explicit "
                f"{stop_reason} boundary."
            ),
            goal_status=goal_status,
            proposed_conclusion_level=policy_decision.conclusion_level,
            stop_reason=stop_reason,
            question_updates=question_updates,
        )

    @staticmethod
    def _terminal_question_updates(
        *,
        open_questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        available_evidence_ids: set[str],
    ) -> list[QuestionUpdate]:
        if available_evidence_ids:
            answer = " ".join(
                finding.summary.strip()
                for finding in findings
                if finding.summary.strip()
            )
            if not answer:
                answer = (
                    "The available observations are recorded by Evidence IDs "
                    f"{', '.join(sorted(available_evidence_ids))}."
                )
            return [
                QuestionUpdate(
                    question_id=question.question_id,
                    status=EvidenceGapStatus.CLOSED.value,
                    answer=answer,
                    evidence_ids=sorted(available_evidence_ids),
                    unavailable_reason=None,
                )
                for question in open_questions
            ]
        return [
            QuestionUpdate(
                question_id=question.question_id,
                status=EvidenceGapStatus.UNAVAILABLE.value,
                answer=None,
                evidence_ids=[],
                unavailable_reason=(
                    "The deterministic investigation reached its stop boundary "
                    "without any Evidence that can answer this question."
                ),
            )
            for question in open_questions
        ]

    @staticmethod
    def _next_baseline_decision_id(
        *,
        goal: InvestigationGoal,
        prior_decisions: list[PlannerDecision],
    ) -> str:
        used_ids = {decision.decision_id for decision in prior_decisions}
        index = len(prior_decisions) + 1
        while True:
            candidate = f"{goal.goal_id}:decision:{index}"
            if candidate not in used_ids:
                return candidate
            index += 1

    @staticmethod
    def _available_evidence_ids(
        *,
        evidence: list[Evidence],
        explicit_evidence_ids: list[str],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
    ) -> set[str]:
        return {
            *explicit_evidence_ids,
            *(item.evidence_id for item in evidence),
            *(
                evidence_id
                for finding in findings
                for evidence_id in finding.evidence_ids
            ),
            *(
                evidence_id
                for record in action_records
                for evidence_id in record.produced_evidence_ids
            ),
        }

    @staticmethod
    def _validate_runtime_inputs(
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        evidence: list[Evidence],
        evidence_ids: list[str],
        hypotheses: list[Hypothesis],
        prior_decisions: list[PlannerDecision],
        critical_contradictions: list[str],
    ) -> None:
        if not isinstance(goal, InvestigationGoal):
            raise ModelValidationError("goal must be an InvestigationGoal")
        if not isinstance(questions, list) or not questions:
            raise ModelValidationError("questions must be a non-empty list")
        question_ids: list[str] = []
        for question in questions:
            if not isinstance(question, InvestigationQuestion):
                raise ModelValidationError(
                    "questions must contain InvestigationQuestion instances"
                )
            if question.goal_id != goal.goal_id:
                raise ModelValidationError("questions must reference the current goal")
            question_ids.append(question.question_id)
        if len(question_ids) != len(set(question_ids)):
            raise ModelValidationError("questions must not contain duplicate ids")
        if len(questions) > MAX_INITIAL_QUESTIONS:
            raise ModelValidationError(
                f"questions must not exceed {MAX_INITIAL_QUESTIONS} total items"
            )
        if not isinstance(findings, list) or any(
            not isinstance(finding, AgentFinding) for finding in findings
        ):
            raise ModelValidationError("findings must contain AgentFinding instances")
        if not isinstance(action_records, list) or any(
            not isinstance(record, ActionRecord) for record in action_records
        ):
            raise ModelValidationError("action_records must contain ActionRecord instances")
        if type(tool_call_count) is not int or tool_call_count < 0:
            raise ModelValidationError("tool_call_count must be a non-negative integer")
        if not isinstance(evidence, list) or any(
            not isinstance(item, Evidence) for item in evidence
        ):
            raise ModelValidationError("evidence must contain Evidence instances")
        _validate_string_list(evidence_ids, "evidence_ids")
        if not isinstance(hypotheses, list) or any(
            not isinstance(hypothesis, Hypothesis) for hypothesis in hypotheses
        ):
            raise ModelValidationError("hypotheses must contain Hypothesis instances")
        if not isinstance(prior_decisions, list) or any(
            not isinstance(decision, PlannerDecision) for decision in prior_decisions
        ):
            raise ModelValidationError(
                "prior_decisions must contain PlannerDecision instances"
            )
        decision_ids = [decision.decision_id for decision in prior_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ModelValidationError("prior_decisions must not contain duplicate ids")
        _validate_string_list(
            critical_contradictions,
            "critical_contradictions",
        )

    def _validate_candidate(
        self,
        candidate: PlannerDecision,
        *,
        goal: InvestigationGoal,
        questions: list[InvestigationQuestion],
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        available_evidence_ids: set[str],
        prior_decisions: list[PlannerDecision],
    ) -> None:
        if candidate.goal_id != goal.goal_id:
            raise InvestigationValidationError("Qwen changed the active goal_id")
        if candidate.decision_id in {
            decision.decision_id for decision in prior_decisions
        }:
            raise InvestigationValidationError("Qwen reused an earlier decision_id")

        existing_questions = {
            question.question_id: question for question in questions
        }
        new_question_ids = {question.question_id for question in candidate.new_questions}
        if new_question_ids & set(existing_questions):
            raise InvestigationValidationError(
                "new_questions cannot reuse an existing question_id"
            )
        if len(existing_questions) + len(candidate.new_questions) > MAX_INITIAL_QUESTIONS:
            raise InvestigationValidationError(
                f"the investigation cannot exceed {MAX_INITIAL_QUESTIONS} questions"
            )
        source_lot_id = _normalized_lot_id(goal.known_facts.get("lot_id"))
        for question in candidate.new_questions:
            capability = QUESTION_CAPABILITY_REGISTRY.get(question.question_kind)
            if capability is None or not capability.supported:
                raise InvestigationValidationError(
                    "unsupported_question_kind: Qwen cannot create an unsupported "
                    f"Question kind {question.question_kind!r}"
                )
            _assert_source_lot_boundary(
                question.scope,
                source_lot_id=source_lot_id,
                label=f"new_questions[{question.question_id}].scope",
            )

        for update in candidate.question_updates:
            current = existing_questions.get(update.question_id)
            if current is None:
                raise InvestigationValidationError(
                    "question_updates can only update an existing question"
                )
            if current.status != EvidenceGapStatus.OPEN.value:
                raise InvestigationValidationError(
                    "question_updates cannot rewrite a terminal question"
                )
            if (
                update.status == EvidenceGapStatus.CLOSED.value
                and not set(update.evidence_ids) <= available_evidence_ids
            ):
                raise InvestigationValidationError(
                    "a closed question references unknown Evidence IDs"
                )
        resulting_questions = {
            **existing_questions,
            **{
                question.question_id: question
                for question in candidate.new_questions
            },
            **{
                update.question_id: replace(
                    existing_questions[update.question_id],
                    status=update.status,
                    answer=update.answer,
                    evidence_ids=list(update.evidence_ids),
                    unavailable_reason=update.unavailable_reason,
                )
                for update in candidate.question_updates
            },
        }
        for target_id in candidate.target_question_ids:
            target = resulting_questions.get(target_id)
            if target is None:
                raise InvestigationValidationError(
                    "target_question_ids must reference a current investigation question"
                )
            if target.status != EvidenceGapStatus.OPEN.value:
                raise InvestigationValidationError(
                    "an action can target only an open investigation question"
                )

        budget_exhausted = (
            len(action_records) >= min(goal.max_steps, MAX_CROSS_DOMAIN_ACTIONS)
            or tool_call_count >= goal.max_tool_calls
        )
        if candidate.decision_type == DecisionType.STOP.value:
            if candidate.target_question_ids:
                raise InvestigationValidationError(
                    "a stop decision cannot target an open question"
                )
            if budget_exhausted and (
                candidate.goal_status != GoalStatus.BUDGET_EXHAUSTED.value
                or candidate.stop_reason != StopReason.BUDGET_EXHAUSTED.value
            ):
                raise InvestigationValidationError(
                    "an exhausted runtime budget requires an explicit budget_exhausted stop"
                )
            if (
                not budget_exhausted
                and candidate.stop_reason == StopReason.BUDGET_EXHAUSTED.value
            ):
                raise InvestigationValidationError(
                    "Qwen cannot claim budget_exhausted before the runtime limit"
                )
            return

        if budget_exhausted:
            raise InvestigationValidationError(
                "Qwen cannot select an action after the runtime budget is exhausted"
            )
        action = candidate.next_action
        if action is None:
            raise InvestigationValidationError("an act decision requires next_action")
        targeted_questions = [
            resulting_questions[question_id]
            for question_id in candidate.target_question_ids
            if question_id in resulting_questions
        ]
        # This is deliberately atomic: one incompatible target rejects the
        # complete Decision instead of silently dropping that target.
        validate_action_for_questions(action, targeted_questions)
        definition = self.registry.get(action.kind)
        if definition is None:
            raise InvestigationValidationError(
                f"action is not in the executable allowlist: {action.kind}"
            )
        if action.agent != definition.agent:
            raise InvestigationValidationError(
                f"action {action.kind} must be executed by Agent {definition.agent}"
            )
        if action.max_attempts != 1:
            raise InvestigationValidationError(
                "a Qwen-selected action must use max_attempts=1"
            )
        finding_agents = {finding.agent for finding in findings}
        missing_agents = set(definition.required_finding_agents) - finding_agents
        if (
            action.kind == ActionKind.INSPECT_DEFECT_PATTERN.value
            and source_lot_id is None
            and AgentKind.MES.value not in finding_agents
        ):
            missing_agents.add(AgentKind.MES.value)
        if missing_agents:
            raise InvestigationValidationError(
                f"action {action.kind} is missing prerequisite Findings from "
                f"{sorted(missing_agents)}"
            )
        if (
            action.kind == ActionKind.RUN_RCA_REASONING.value
            and goal.intent
            in {
                InvestigationIntent.ROOT_CAUSE.value,
                InvestigationIntent.FULL_RCA.value,
            }
            and not any(
                record.action.kind
                == ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value
                and record.status == "completed"
                for record in action_records
            )
        ):
            raise InvestigationValidationError(
                "run_rca_reasoning requires a completed "
                "validate_shared_defect_pattern action for root-cause goals"
            )
        if not action.scope:
            raise InvestigationValidationError(
                "a Qwen-selected action requires a non-empty stable scope"
            )
        _assert_source_lot_boundary(
            action.inputs,
            source_lot_id=source_lot_id,
            label="next_action.inputs",
        )
        _assert_source_lot_boundary(
            action.scope,
            source_lot_id=source_lot_id,
            label="next_action.scope",
        )
        if not set(action.required_evidence_ids) <= available_evidence_ids:
            raise InvestigationValidationError(
                "next_action.required_evidence_ids references unknown Evidence"
            )
        prior_action_ids = {record.action.action_id for record in action_records}
        if action.action_id in prior_action_ids:
            raise InvestigationValidationError("Qwen reused an earlier action_id")
        if action.deduplication_key in {
            record.action.deduplication_key for record in action_records
        }:
            raise InvestigationValidationError(
                "Qwen repeated an already attempted Action + Scope"
            )
        if (
            action.kind == ActionKind.FIND_SHARED_EXPOSURE.value
            and any(
                record.action.kind == ActionKind.FIND_SHARED_EXPOSURE.value
                for record in action_records
            )
        ):
            raise InvestigationValidationError(
                "find_shared_exposure is single-use within one bounded investigation"
            )

    def _advertised_actions(
        self,
        questions: list[InvestigationQuestion],
    ) -> frozenset[str]:
        """Advertise only Actions that can target at least one open Question."""

        advertised: set[str] = set()
        for question in questions:
            capability = QUESTION_CAPABILITY_REGISTRY.get(question.question_kind)
            if capability is not None and capability.supported:
                advertised.update(
                    action_kind
                    for action_kind in capability.allowed_actions
                    if action_kind in self.registry
                )
        return frozenset(advertised)


__all__ = [
    "LLM_REACT_ACTION_REGISTRY",
    "LLM_REACT_EXECUTABLE_ACTION_KINDS",
    "QwenNextActionPlanner",
    "QwenNextActionPlannerError",
]
