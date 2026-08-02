"""Qwen-backed user-intent planning for autonomous RCA investigations.

This module stops at Goal and Question creation. It does not choose Agents,
dispatch Tools, inspect evidence, infer a root cause, or enable llm_react.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from yield_rca_core.investigation_models import (
    MAX_CROSS_DOMAIN_ACTIONS,
    IntentPlan,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    InvestigationValidationError,
    OrchestrationMode,
    QuestionKind,
)
from yield_rca_core.llm_gateway import LLMClient, LLMOutputValidationError, LLMRequest
from yield_rca_core.models import AgentKind, ModelValidationError
from yield_rca_core.planner_agent import PlannerAgent
from yield_rca_core.question_capability import requested_capability_notices

_OUTPUT_ATTEMPTS = 2
_FORBIDDEN_KNOWN_FACT_KEYS = frozenset(
    {
        "affected_lots",
        "cause",
        "conclusion",
        "hypothesis",
        "impact_lots",
        "root_cause",
    }
)
_REQUIRED_EVIDENCE_BY_INTENT: dict[str, list[str]] = {
    InvestigationIntent.IMPACT_SCOPE.value: [
        "shared_exposure",
        "impact_scope",
    ],
    InvestigationIntent.SPC_CHECK.value: [
        "process_context",
        "spc_signal",
    ],
    InvestigationIntent.ROOT_CAUSE.value: [
        "defect_signature",
        "process_mechanism",
        "product_outcome",
    ],
    InvestigationIntent.HISTORICAL_LOOKUP.value: [
        "incident_signature",
        "approved_historical_match",
    ],
    InvestigationIntent.FULL_RCA.value: [
        "defect_signature",
        "shared_exposure",
        "impact_scope",
        "process_mechanism",
        "product_outcome",
    ],
}


class QwenIntentPlannerError(LLMOutputValidationError):
    """Raised after both Qwen structured-output attempts fail validation."""

    fallback_mode = OrchestrationMode.CONTROLLED_REACT.value

    def __init__(self, validation_errors: list[str]) -> None:
        self.attempts = len(validation_errors)
        self.validation_errors = tuple(validation_errors)
        super().__init__(
            "Qwen Intent Planner returned invalid output twice; "
            f"fallback to {self.fallback_mode} is required"
        )


def _normalize_query(user_query: str) -> str:
    if not isinstance(user_query, str) or not user_query.strip():
        raise ModelValidationError("user_query must be a non-empty string")
    return " ".join(user_query.split())


def _intent_from_query(query: str, fallback_intent: str) -> str:
    """Produce a deterministic Fake Client baseline without replacing Qwen."""

    lowered = query.casefold()
    has_root_cause = any(
        token in lowered for token in ("root cause", "rca", "原因", "根因")
    )
    has_impact = any(
        token in lowered
        for token in ("impact", "affected lot", "影响批次", "受影响批次")
    )
    has_spc = any(token in lowered for token in ("spc", "控制图", "管制图"))
    has_history = any(
        token in lowered
        for token in ("historical", "similar case", "历史案例", "相似案例")
    )
    asks_full = any(
        token in lowered for token in ("full rca", "完整rca", "完整 rca", "全面分析")
    )
    if asks_full or (has_root_cause and has_impact):
        return InvestigationIntent.FULL_RCA.value
    if has_impact and not has_root_cause:
        return InvestigationIntent.IMPACT_SCOPE.value
    if has_spc and not has_root_cause:
        return InvestigationIntent.SPC_CHECK.value
    if has_history and not has_root_cause:
        return InvestigationIntent.HISTORICAL_LOOKUP.value
    return fallback_intent


def _question(
    goal: InvestigationGoal,
    suffix: str,
    text: str,
    rationale: str,
    question_kind: QuestionKind,
) -> InvestigationQuestion:
    return InvestigationQuestion(
        question_id=f"{goal.goal_id}:q:{suffix}",
        goal_id=goal.goal_id,
        question=text,
        rationale=rationale,
        question_kind=question_kind.value,
        scope=dict(goal.known_facts),
    )


def _baseline_questions(goal: InvestigationGoal) -> list[InvestigationQuestion]:
    if goal.intent == InvestigationIntent.IMPACT_SCOPE.value:
        return [
            _question(
                goal,
                "impact_scope",
                "Which Lots share the relevant process exposure with the source scope?",
                "The requested outcome is the bounded impact-Lot population.",
                QuestionKind.IMPACT_SCOPE,
            )
        ]
    if goal.intent == InvestigationIntent.SPC_CHECK.value:
        return [
            _question(
                goal,
                "spc_signal",
                "Which process parameter and SPC rule show the reported excursion?",
                "The requested outcome is an SPC assessment, not a root-cause conclusion.",
                QuestionKind.SPC_SIGNAL,
            )
        ]
    if goal.intent == InvestigationIntent.HISTORICAL_LOOKUP.value:
        return [
            _question(
                goal,
                "historical_match",
                "Which approved historical cases match the reported incident signature?",
                "Only confirmed knowledge can support a historical comparison.",
                QuestionKind.HISTORICAL_MATCH,
            )
        ]
    if goal.intent == InvestigationIntent.FULL_RCA.value:
        return [
            _question(
                goal,
                "defect_signature",
                "What is the defect or product-outcome signature of the source Lot?",
                "The observed symptom must be characterized before mechanism analysis.",
                QuestionKind.DEFECT_SIGNATURE,
            ),
            _question(
                goal,
                "impact_scope",
                "Which Lots share the relevant process exposure with the source Lot?",
                "The user requested impact scope as part of the same investigation.",
                QuestionKind.IMPACT_SCOPE,
            ),
            _question(
                goal,
                "process_mechanism",
                "Which process or equipment mechanism explains the reported symptom?",
                "A supported RCA needs process evidence linked to the product outcome.",
                QuestionKind.PROCESS_MECHANISM,
            ),
        ]
    return [
        _question(
            goal,
            "defect_signature",
            "What is the defect or product-outcome signature of the source scope?",
            "The reported symptom must be characterized before mechanism analysis.",
            QuestionKind.DEFECT_SIGNATURE,
        ),
        _question(
            goal,
            "process_mechanism",
            "Which process or equipment mechanism explains the reported symptom?",
            "The user requested an evidence-backed root cause.",
            QuestionKind.PROCESS_MECHANISM,
        ),
    ]


@dataclass(frozen=True)
class QwenIntentPlanner:
    """Convert one user request into a bounded Goal and initial Questions."""

    llm_client: LLMClient
    fallback_planner: PlannerAgent = field(default_factory=PlannerAgent)
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if self.llm_client is None:
            raise ModelValidationError("Qwen Intent Planner requires an LLM client")
        if not isinstance(self.fallback_planner, PlannerAgent):
            raise ModelValidationError("fallback_planner must be a PlannerAgent")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ModelValidationError("prompt_version must be a non-empty string")

    def plan(
        self,
        user_query: str,
        *,
        lot_id: str | None = None,
    ) -> IntentPlan:
        query = _normalize_query(user_query)
        if lot_id is not None and not isinstance(lot_id, str):
            raise ModelValidationError("lot_id must be a string or null")
        normalized_lot_id = lot_id.strip().upper() if lot_id is not None else None
        if lot_id is not None and not normalized_lot_id:
            raise ModelValidationError("lot_id must be a non-empty string")
        baseline = self._baseline_plan(query, lot_id=normalized_lot_id)
        protected_lot_id = normalized_lot_id
        if protected_lot_id is None and baseline.goal.known_facts.get("lot_id"):
            protected_lot_id = str(baseline.goal.known_facts["lot_id"])
        validation_errors: list[str] = []

        for attempt in range(1, _OUTPUT_ATTEMPTS + 1):
            request = LLMRequest(
                agent=AgentKind.PLANNER.value,
                prompt_name="intent_planner",
                prompt_version=self.prompt_version,
                payload={
                    "user_query": query,
                    "explicit_lot_id": protected_lot_id,
                    "requested_goal_id": baseline.goal.goal_id,
                    "allowed_intents": [intent.value for intent in InvestigationIntent],
                    "fixed_max_steps": baseline.goal.max_steps,
                    "fixed_max_tool_calls": baseline.goal.max_tool_calls,
                    "deterministic_intent_plan": baseline.to_dict(),
                    "output_attempt": attempt,
                    "previous_validation_error": (
                        validation_errors[-1] if validation_errors else None
                    ),
                },
                temperature=0.0,
            )
            try:
                response = self.llm_client.complete_json(request)
                candidate = IntentPlan.from_dict(response.data)
                self._validate_candidate(
                    candidate,
                    baseline=baseline,
                    explicit_lot_id=protected_lot_id,
                )
                baseline_material_questions = [
                    question
                    for question in baseline.questions
                    if question.question_kind == QuestionKind.MATERIAL_TRACE.value
                ]
                candidate_material_questions = [
                    question
                    for question in candidate.questions
                    if question.question_kind == QuestionKind.MATERIAL_TRACE.value
                ]
                if baseline_material_questions:
                    if len(baseline.questions) == len(baseline_material_questions):
                        # A pure unsupported request cannot be converted into a
                        # supported investigation by a model response.
                        candidate = replace(
                            candidate,
                            questions=list(baseline_material_questions),
                        )
                    elif not candidate_material_questions:
                        # Preserve the unsupported mixed-request component even
                        # when Qwen focuses its supported questions first.
                        candidate = replace(
                            candidate,
                            questions=[
                                *candidate.questions,
                                baseline_material_questions[0],
                            ],
                        )
                # Capability notices are Python-owned and cannot be omitted or
                # rewritten by Qwen's structured response.
                return replace(
                    candidate,
                    capability_notices=list(baseline.capability_notices),
                )
            except (
                InvestigationValidationError,
                LLMOutputValidationError,
                KeyError,
                TypeError,
            ) as exc:
                message = str(exc).strip() or type(exc).__name__
                validation_errors.append(message)

        raise QwenIntentPlannerError(validation_errors)

    def _baseline_plan(self, query: str, *, lot_id: str | None) -> IntentPlan:
        base_goal = self.fallback_planner.plan_investigation_goal(query, lot_id=lot_id)
        intent = _intent_from_query(query, base_goal.intent)
        known_facts = dict(base_goal.known_facts)
        lowered = query.casefold()
        if any(token in lowered for token in ("scratch", "划伤", "刮伤")):
            known_facts.setdefault("defect", "scratch")
        if ("cu" in lowered and "cmp" in lowered) or any(
            token in lowered for token in ("铜cmp", "铜 cmp")
        ):
            known_facts.setdefault("module", "CU_CMP")
        goal = replace(
            base_goal,
            intent=intent,
            known_facts=known_facts,
            required_evidence=list(_REQUIRED_EVIDENCE_BY_INTENT[intent]),
            max_steps=MAX_CROSS_DOMAIN_ACTIONS,
        )
        notices = requested_capability_notices(query)
        questions = _baseline_questions(goal)
        if notices:
            supported_tokens = (
                "root cause",
                "rca",
                "impact",
                "spc",
                "historical",
                "defect",
                "scratch",
                "yield",
                "良率",
                "原因",
            )
            if not any(token in lowered for token in supported_tokens):
                questions = [
                    _question(
                        goal,
                        "material_trace",
                        (
                            "Which material, supplier, or consumable batch is linked "
                            "to the source scope?"
                        ),
                        (
                            "The user explicitly requested material genealogy, which "
                            "is not configured."
                        ),
                        QuestionKind.MATERIAL_TRACE,
                    )
                ]
            elif len(questions) < 5:
                questions.append(
                    _question(
                        goal,
                        "material_trace",
                        (
                            "Which material, supplier, or consumable batch is linked "
                            "to the source scope?"
                        ),
                        (
                            "The user explicitly requested material genealogy in "
                            "addition to supported RCA work."
                        ),
                        QuestionKind.MATERIAL_TRACE,
                    )
                )
        return IntentPlan(
            goal=goal,
            questions=questions,
            capability_notices=notices,
        )

    @staticmethod
    def _validate_candidate(
        candidate: IntentPlan,
        *,
        baseline: IntentPlan,
        explicit_lot_id: str | None,
    ) -> None:
        if candidate.goal.goal_id != baseline.goal.goal_id:
            raise InvestigationValidationError("Qwen changed the requested goal_id")
        if candidate.goal.max_steps != baseline.goal.max_steps:
            raise InvestigationValidationError("Qwen changed the fixed max_steps budget")
        if candidate.goal.max_tool_calls != baseline.goal.max_tool_calls:
            raise InvestigationValidationError("Qwen changed the fixed max_tool_calls budget")
        for key, expected_value in baseline.goal.known_facts.items():
            if candidate.goal.known_facts.get(key) != expected_value:
                raise InvestigationValidationError(
                    f"Qwen changed or removed explicit known fact: {key}"
                )
        forbidden_keys = _FORBIDDEN_KNOWN_FACT_KEYS & set(candidate.goal.known_facts)
        if forbidden_keys:
            raise InvestigationValidationError(
                "Intent Planner cannot assert conclusions as known facts: "
                f"{sorted(forbidden_keys)}"
            )
        baseline_notice_kinds = {
            notice.capability for notice in baseline.capability_notices
        }
        for question in candidate.questions:
            if question.question_kind == QuestionKind.UNSUPPORTED.value:
                raise InvestigationValidationError(
                    "unsupported_question_kind: Qwen created an unrecognized Question kind"
                )
            if (
                question.question_kind == QuestionKind.MATERIAL_TRACE.value
                and QuestionKind.MATERIAL_TRACE.value not in baseline_notice_kinds
            ):
                raise InvestigationValidationError(
                    "unsupported_question_kind: material_trace was not requested by the user"
                )
        if explicit_lot_id is not None:
            if candidate.goal.known_facts.get("lot_id") != explicit_lot_id:
                raise InvestigationValidationError(
                    "Qwen changed or removed the explicit lot_id"
                )
            for question in candidate.questions:
                scoped_lot_id = question.scope.get("lot_id")
                if scoped_lot_id is not None and scoped_lot_id != explicit_lot_id:
                    raise InvestigationValidationError(
                        "an initial question cannot expand to another Lot scope"
                    )
