"""Qwen-backed user-intent planning for autonomous RCA investigations.

This module stops at Goal and Question creation. It does not choose Agents,
dispatch Tools, inspect evidence, infer a root cause, or enable llm_react.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from yield_rca_core.investigation_models import (
    MAX_CROSS_DOMAIN_ACTIONS,
    IntentPlan,
    IntentPlannerReasonCode,
    IntentPlanOutcome,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    InvestigationValidationError,
    OrchestrationMode,
    PlannerAttemptDiagnostic,
    PlannerAttemptOutcome,
    PlannerAttemptStage,
    PlannerFailureCategory,
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
_SENSITIVE_FIELD_TOKENS = ("api_key", "authorization", "password", "secret", "token")
_SAFE_ENUM_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_QUESTION_KINDS = tuple(
    item.value for item in QuestionKind if item is not QuestionKind.UNSUPPORTED
)


class _IntentCandidateValidationError(InvestigationValidationError):
    """Internal semantic rejection with a stable audit reason and field path."""

    def __init__(self, reason_code: IntentPlannerReasonCode, field_path: str, message: str) -> None:
        self.reason_code = reason_code.value
        self.field_path = field_path
        super().__init__(message)


class QwenIntentPlannerError(LLMOutputValidationError):
    """Raised after both Qwen structured-output attempts fail validation."""

    fallback_mode = OrchestrationMode.CONTROLLED_REACT.value

    def __init__(
        self,
        validation_errors: list[str],
        attempt_diagnostics: list[PlannerAttemptDiagnostic] | None = None,
        *,
        fallback_plan: IntentPlan,
    ) -> None:
        if not isinstance(fallback_plan, IntentPlan):
            raise TypeError("fallback_plan must be an IntentPlan")
        self.attempts = len(validation_errors)
        self.validation_errors = tuple(validation_errors)
        self.attempt_diagnostics = tuple(attempt_diagnostics or ())
        self.fallback_plan = fallback_plan
        super().__init__(
            "Qwen Intent Planner returned invalid output twice; "
            f"fallback to {self.fallback_mode} is required"
        )


def _safe_message(error: Exception) -> str:
    """Bound validation feedback and redact common credential representations."""

    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    message = message or type(error).__name__
    message = re.sub(
        r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+",
        r"\1 [REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)\b(api[_ -]?key|authorization|password|secret)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)\b(api[_ -]?key|authorization|password|secret|access[_ -]?token)\b",
        "[SENSITIVE_FIELD]",
        message,
    )
    return message[:500]


def _safe_field_names(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    names: list[str] = []
    for key in value:
        if not isinstance(key, str) or not key.strip():
            continue
        normalized = key.casefold().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in _SENSITIVE_FIELD_TOKENS):
            continue
        names.append(key[:100])
    return sorted(names)[:30]


def _safe_question_kind_value(value: object) -> str:
    """Retain only bounded enum-like tokens; redact arbitrary model content."""

    if not isinstance(value, str):
        safe_type = type(value).__name__
        if safe_type not in {"NoneType", "bool", "dict", "float", "int", "list"}:
            safe_type = "other"
        return f"[NON_STRING:{safe_type}]"
    normalized = value.strip()
    lowered = normalized.casefold()
    if any(token in lowered for token in (*_SENSITIVE_FIELD_TOKENS, "bearer")):
        return "[REDACTED]"
    if not _SAFE_ENUM_TOKEN.fullmatch(normalized):
        return "[REDACTED]"
    return normalized


def _candidate_summary(candidate: object) -> dict[str, Any]:
    """Summarize shape only; never retain the raw response or fact values."""

    if not isinstance(candidate, dict):
        return {
            "response_type": type(candidate).__name__,
            "response_is_object": False,
        }
    summary: dict[str, Any] = {
        "response_type": "object",
        "response_is_object": True,
        "top_level_fields": _safe_field_names(candidate),
    }
    goal = candidate.get("goal")
    if isinstance(goal, dict):
        summary["goal_fields"] = _safe_field_names(goal)
        intent = goal.get("intent")
        if isinstance(intent, str) and intent in {item.value for item in InvestigationIntent}:
            summary["intent"] = intent
        summary["known_fact_keys"] = _safe_field_names(goal.get("known_facts"))
    questions = candidate.get("questions")
    if isinstance(questions, list):
        summary["question_count"] = len(questions)
        allowed_kinds = set(_ALLOWED_QUESTION_KINDS)
        question_kinds = {
            question.get("question_kind")
            for question in questions
            if isinstance(question, dict)
            and isinstance(question.get("question_kind"), str)
            and question.get("question_kind") in allowed_kinds
        }
        summary["question_kinds"] = sorted(question_kinds)
        invalid_question_kinds: list[dict[str, Any]] = []
        missing_question_kind_indexes: list[int] = []
        for index, question in enumerate(questions[:10]):
            if not isinstance(question, dict):
                continue
            if "question_kind" not in question:
                missing_question_kind_indexes.append(index)
                continue
            question_kind = question.get("question_kind")
            if not isinstance(question_kind, str) or question_kind not in allowed_kinds:
                invalid_question_kinds.append(
                    {
                        "index": index,
                        "value": _safe_question_kind_value(question_kind),
                    }
                )
        if invalid_question_kinds:
            summary["invalid_question_kinds"] = invalid_question_kinds
        if missing_question_kind_indexes:
            summary["missing_question_kind_indexes"] = (
                missing_question_kind_indexes
            )
    return summary


def _baseline_diff(candidate: object, baseline: IntentPlan) -> dict[str, Any]:
    """Return bounded structural differences without retaining candidate values."""

    if not isinstance(candidate, dict):
        return {"candidate_object_missing": True}
    goal = candidate.get("goal")
    if not isinstance(goal, dict):
        return {"goal_object_missing": True}
    baseline_goal = baseline.goal.to_dict()
    baseline_facts = baseline_goal["known_facts"]
    candidate_facts = goal.get("known_facts")
    diff: dict[str, Any] = {
        "goal_id_changed": goal.get("goal_id") != baseline_goal["goal_id"],
        "intent_changed": goal.get("intent") != baseline_goal["intent"],
        "budget_fields_changed": sorted(
            field_name
            for field_name in ("max_steps", "max_tool_calls")
            if goal.get(field_name) != baseline_goal[field_name]
        ),
    }
    if isinstance(candidate_facts, dict):
        diff["known_fact_keys_removed"] = sorted(
            key for key in baseline_facts if key not in candidate_facts
        )
        diff["known_fact_keys_changed"] = sorted(
            key
            for key, value in baseline_facts.items()
            if key in candidate_facts and candidate_facts[key] != value
        )
        diff["known_fact_keys_added"] = [
            key for key in _safe_field_names(candidate_facts) if key not in baseline_facts
        ]
    else:
        diff["known_facts_object_missing"] = True
    questions = candidate.get("questions")
    if isinstance(questions, list):
        diff["question_count_delta"] = len(questions) - len(baseline.questions)
    return diff


def _contract_failure(error: Exception) -> tuple[str, str]:
    message = str(error).casefold()
    if "intent is invalid" in message:
        return IntentPlannerReasonCode.INTENT_INVALID.value, "$.goal.intent"
    if "question_kind is invalid" in message or "unsupported_question_kind" in message:
        return IntentPlannerReasonCode.UNSUPPORTED_QUESTION_KIND.value, "$.questions"
    if "known_facts" in message:
        return IntentPlannerReasonCode.MALFORMED_OUTPUT.value, "$.goal.known_facts"
    if "goal" in message and "question" not in message:
        return IntentPlannerReasonCode.MALFORMED_OUTPUT.value, "$.goal"
    if "question" in message:
        return IntentPlannerReasonCode.MALFORMED_OUTPUT.value, "$.questions"
    return IntentPlannerReasonCode.MALFORMED_OUTPUT.value, "$"


def _failure_details(
    error: Exception,
    *,
    candidate_summary: dict[str, Any] | None = None,
) -> tuple[str, str, str | None]:
    if isinstance(error, _IntentCandidateValidationError):
        return (
            PlannerFailureCategory.SEMANTIC_VALIDATION_ERROR.value,
            error.reason_code,
            error.field_path,
        )
    if isinstance(error, LLMOutputValidationError):
        return (
            PlannerFailureCategory.OUTPUT_PARSE_ERROR.value,
            IntentPlannerReasonCode.MALFORMED_OUTPUT.value,
            "$",
        )
    reason_code, field_path = _contract_failure(error)
    if reason_code == IntentPlannerReasonCode.UNSUPPORTED_QUESTION_KIND.value:
        invalid_kinds = (candidate_summary or {}).get("invalid_question_kinds")
        if isinstance(invalid_kinds, list) and invalid_kinds:
            invalid_index = invalid_kinds[0].get("index")
            if type(invalid_index) is int and invalid_index >= 0:
                field_path = f"$.questions[{invalid_index}].question_kind"
    return (
        PlannerFailureCategory.CONTRACT_VALIDATION_ERROR.value,
        reason_code,
        field_path,
    )


def _repair_feedback(
    *,
    failure_category: str,
    reason_code: str,
    field_path: str | None,
    message: str,
    candidate_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build bounded machine-readable feedback for the next model attempt."""

    feedback: dict[str, Any] = {
        "failure_category": failure_category,
        "reason_code": reason_code,
        "field_path": field_path,
        "message": message,
        "allowed_question_kinds": list(_ALLOWED_QUESTION_KINDS),
    }
    for key in ("invalid_question_kinds", "missing_question_kind_indexes"):
        value = candidate_summary.get(key)
        if value:
            feedback[key] = value
    return feedback


def _provider_request_id(response: object) -> str | None:
    request_id = getattr(response, "provider_request_id", None)
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    return request_id.strip()[:100]


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
        """Compatibility interface returning only the accepted IntentPlan."""

        return self.plan_with_diagnostics(user_query, lot_id=lot_id).plan

    def plan_with_diagnostics(
        self,
        user_query: str,
        *,
        lot_id: str | None = None,
    ) -> IntentPlanOutcome:
        """Return the accepted plan plus an immutable audit for every attempt."""

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
        attempt_diagnostics: list[PlannerAttemptDiagnostic] = []
        previous_validation_feedback: dict[str, Any] | None = None

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
                    "allowed_question_kinds": list(_ALLOWED_QUESTION_KINDS),
                    "fixed_max_steps": baseline.goal.max_steps,
                    "fixed_max_tool_calls": baseline.goal.max_tool_calls,
                    "deterministic_intent_plan": baseline.to_dict(),
                    "output_attempt": attempt,
                    "previous_validation_error": (
                        validation_errors[-1] if validation_errors else None
                    ),
                    "previous_validation_feedback": previous_validation_feedback,
                },
                temperature=0.0,
            )
            response = None
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
                accepted_plan = replace(
                    candidate,
                    capability_notices=list(baseline.capability_notices),
                )
                attempt_diagnostics.append(
                    PlannerAttemptDiagnostic(
                        stage=PlannerAttemptStage.INTENT_PLANNING.value,
                        attempt=attempt,
                        prompt_name=request.prompt_name,
                        prompt_version=request.prompt_version,
                        outcome=PlannerAttemptOutcome.SUCCESS.value,
                        repair_feedback_sent=False,
                        candidate_summary=_candidate_summary(response.data),
                        baseline_diff=_baseline_diff(response.data, baseline),
                        provider_request_id=_provider_request_id(response),
                    )
                )
                return IntentPlanOutcome(
                    plan=accepted_plan,
                    attempt_diagnostics=attempt_diagnostics,
                )
            except (
                InvestigationValidationError,
                LLMOutputValidationError,
                KeyError,
                TypeError,
            ) as exc:
                message = _safe_message(exc)
                validation_errors.append(message)
                response_data = response.data if response is not None else None
                candidate_summary = _candidate_summary(response_data)
                failure_category, reason_code, field_path = _failure_details(
                    exc,
                    candidate_summary=candidate_summary,
                )
                previous_validation_feedback = _repair_feedback(
                    failure_category=failure_category,
                    reason_code=reason_code,
                    field_path=field_path,
                    message=message,
                    candidate_summary=candidate_summary,
                )
                attempt_diagnostics.append(
                    PlannerAttemptDiagnostic(
                        stage=PlannerAttemptStage.INTENT_PLANNING.value,
                        attempt=attempt,
                        prompt_name=request.prompt_name,
                        prompt_version=request.prompt_version,
                        outcome=PlannerAttemptOutcome.FAILURE.value,
                        failure_category=failure_category,
                        reason_code=reason_code,
                        field_path=field_path,
                        message=message,
                        repair_feedback_sent=attempt < _OUTPUT_ATTEMPTS,
                        candidate_summary=candidate_summary,
                        baseline_diff=_baseline_diff(response_data, baseline),
                        provider_request_id=_provider_request_id(response),
                    )
                )

        raise QwenIntentPlannerError(
            validation_errors,
            attempt_diagnostics,
            fallback_plan=baseline,
        )

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
            raise _IntentCandidateValidationError(
                IntentPlannerReasonCode.GOAL_ID_CHANGED,
                "$.goal.goal_id",
                "Qwen changed the requested goal_id",
            )
        if candidate.goal.max_steps != baseline.goal.max_steps:
            raise _IntentCandidateValidationError(
                IntentPlannerReasonCode.BUDGET_CHANGED,
                "$.goal.max_steps",
                "Qwen changed the fixed max_steps budget",
            )
        if candidate.goal.max_tool_calls != baseline.goal.max_tool_calls:
            raise _IntentCandidateValidationError(
                IntentPlannerReasonCode.BUDGET_CHANGED,
                "$.goal.max_tool_calls",
                "Qwen changed the fixed max_tool_calls budget",
            )
        for key, expected_value in baseline.goal.known_facts.items():
            if key not in candidate.goal.known_facts:
                reason_code = (
                    IntentPlannerReasonCode.SOURCE_LOT_SCOPE_MISMATCH
                    if key == "lot_id"
                    else IntentPlannerReasonCode.KNOWN_FACT_REMOVED
                )
                raise _IntentCandidateValidationError(
                    reason_code,
                    f"$.goal.known_facts.{key}",
                    f"Qwen changed or removed explicit known fact: {key}",
                )
            if candidate.goal.known_facts[key] != expected_value:
                reason_code = (
                    IntentPlannerReasonCode.SOURCE_LOT_SCOPE_MISMATCH
                    if key == "lot_id"
                    else IntentPlannerReasonCode.KNOWN_FACT_CHANGED
                )
                raise _IntentCandidateValidationError(
                    reason_code,
                    f"$.goal.known_facts.{key}",
                    f"Qwen changed or removed explicit known fact: {key}",
                )
        forbidden_keys = _FORBIDDEN_KNOWN_FACT_KEYS & set(candidate.goal.known_facts)
        if forbidden_keys:
            raise _IntentCandidateValidationError(
                IntentPlannerReasonCode.FORBIDDEN_KNOWN_FACT_ADDED,
                "$.goal.known_facts",
                "Intent Planner cannot assert conclusions as known facts: "
                f"{sorted(forbidden_keys)}",
            )
        baseline_notice_kinds = {
            notice.capability for notice in baseline.capability_notices
        }
        for question_index, question in enumerate(candidate.questions):
            if question.question_kind == QuestionKind.UNSUPPORTED.value:
                raise _IntentCandidateValidationError(
                    IntentPlannerReasonCode.UNSUPPORTED_QUESTION_KIND,
                    f"$.questions[{question_index}].question_kind",
                    "unsupported_question_kind: Qwen created an unrecognized Question kind",
                )
            if (
                question.question_kind == QuestionKind.MATERIAL_TRACE.value
                and QuestionKind.MATERIAL_TRACE.value not in baseline_notice_kinds
            ):
                raise _IntentCandidateValidationError(
                    IntentPlannerReasonCode.UNREQUESTED_MATERIAL_TRACE,
                    f"$.questions[{question_index}].question_kind",
                    "unsupported_question_kind: material_trace was not requested by the user",
                )
        if explicit_lot_id is not None:
            if candidate.goal.known_facts.get("lot_id") != explicit_lot_id:
                raise _IntentCandidateValidationError(
                    IntentPlannerReasonCode.SOURCE_LOT_SCOPE_MISMATCH,
                    "$.goal.known_facts.lot_id",
                    "Qwen changed or removed the explicit lot_id",
                )
            for question_index, question in enumerate(candidate.questions):
                scoped_lot_id = question.scope.get("lot_id")
                if scoped_lot_id is not None and scoped_lot_id != explicit_lot_id:
                    raise _IntentCandidateValidationError(
                        IntentPlannerReasonCode.SOURCE_LOT_SCOPE_MISMATCH,
                        f"$.questions[{question_index}].scope.lot_id",
                        "an initial question cannot expand to another Lot scope",
                    )
