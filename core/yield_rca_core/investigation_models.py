"""Typed contracts shared by controlled and LLM ReAct RCA orchestration.

These contracts deliberately do not execute Tools or Agents.  They describe
the investigation goal, engineering questions, bounded actions, planner
decisions, and compact evaluations exchanged by orchestration components.
Keeping this layer framework-free makes structured LLM output and its audit
trail independently testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self


class InvestigationValidationError(ValueError):
    """Raised when a controlled investigation contract is invalid."""


class OrchestrationMode(StrEnum):
    FIXED = "fixed"
    CONTROLLED_REACT = "controlled_react"
    LLM_REACT = "llm_react"


class InvestigationIntent(StrEnum):
    IMPACT_SCOPE = "impact_scope"
    SPC_CHECK = "spc_check"
    ROOT_CAUSE = "root_cause"
    HISTORICAL_LOOKUP = "historical_lookup"
    FULL_RCA = "full_rca"


class ActionKind(StrEnum):
    INSPECT_DEFECT_PATTERN = "inspect_defect_pattern"
    VALIDATE_SHARED_DEFECT_PATTERN = "validate_shared_defect_pattern"
    FIND_SHARED_EXPOSURE = "find_shared_exposure"
    ASSESS_IMPACT_SCOPE = "assess_impact_scope"
    INSPECT_FDC_SPC = "inspect_fdc_spc"
    INSPECT_RECIPE_CHANGE = "inspect_recipe_change"
    VALIDATE_HISTORICAL_CASE = "validate_historical_case"
    RUN_RCA_REASONING = "run_rca_reasoning"
    CONCLUDE_INCONCLUSIVE = "conclude_inconclusive"


class GoalStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ConclusionLevel(StrEnum):
    SIGNAL = "signal"
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    INCONCLUSIVE = "inconclusive"


class StopReason(StrEnum):
    GOAL_SATISFIED = "goal_satisfied"
    CRITICAL_CONTRADICTION = "critical_contradiction"
    NO_ALLOWED_ACTION = "no_allowed_action"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DATA_UNAVAILABLE = "data_unavailable"


class EvidenceGapStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNAVAILABLE = "unavailable"


class DecisionType(StrEnum):
    ACT = "act"
    STOP = "stop"


MAX_CROSS_DOMAIN_ACTIONS = 8
MAX_INITIAL_QUESTIONS = 5


def _non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvestigationValidationError(f"{name} must be a non-empty string")


def _string_list(values: list[str], name: str) -> None:
    valid_values = isinstance(values, list) and all(
        isinstance(value, str) and value.strip() for value in values
    )
    if not valid_values:
        raise InvestigationValidationError(f"{name} must be a list of non-empty strings")
    if len(values) != len(set(values)):
        raise InvestigationValidationError(f"{name} must not contain duplicates")


def _json_object(value: dict[str, Any], name: str) -> None:
    valid_keys = isinstance(value, dict) and all(
        isinstance(key, str) and key.strip() for key in value
    )
    if not valid_keys:
        raise InvestigationValidationError(f"{name} must be a JSON object with non-empty keys")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvestigationValidationError(f"{name} must contain JSON-compatible values") from exc


def _boolean(value: bool, name: str) -> None:
    if not isinstance(value, bool):
        raise InvestigationValidationError(f"{name} must be a boolean")


def _strict_object(
    data: object,
    *,
    required: set[str],
    optional: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InvestigationValidationError(f"{name} must be a JSON object")
    invalid_keys = [key for key in data if not isinstance(key, str) or not key.strip()]
    if invalid_keys:
        raise InvestigationValidationError(f"{name} must contain non-empty string keys")
    actual_keys = set(data)
    missing = required - actual_keys
    if missing:
        raise InvestigationValidationError(f"{name} is missing fields: {sorted(missing)}")
    unknown = actual_keys - required - optional
    if unknown:
        raise InvestigationValidationError(f"{name} has unknown fields: {sorted(unknown)}")
    return data


@dataclass(frozen=True)
class InvestigationGoal:
    """The user outcome and bounded resources for one investigation."""

    goal_id: str
    intent: str
    summary: str
    known_facts: dict[str, Any] = field(default_factory=dict)
    required_evidence: list[str] = field(default_factory=list)
    max_steps: int = MAX_CROSS_DOMAIN_ACTIONS
    max_tool_calls: int = 20

    def __post_init__(self) -> None:
        _non_empty(self.goal_id, "goal_id")
        try:
            InvestigationIntent(self.intent)
        except ValueError as exc:
            raise InvestigationValidationError("intent is invalid") from exc
        _non_empty(self.summary, "summary")
        _json_object(self.known_facts, "known_facts")
        _string_list(self.required_evidence, "required_evidence")
        if (
            type(self.max_steps) is not int
            or self.max_steps < 1
            or self.max_steps > MAX_CROSS_DOMAIN_ACTIONS
        ):
            raise InvestigationValidationError(
                f"max_steps must be between 1 and {MAX_CROSS_DOMAIN_ACTIONS}"
            )
        if type(self.max_tool_calls) is not int or self.max_tool_calls < 1:
            raise InvestigationValidationError("max_tool_calls must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "intent": self.intent,
            "summary": self.summary,
            "known_facts": dict(self.known_facts),
            "required_evidence": list(self.required_evidence),
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={"goal_id", "intent", "summary"},
            optional={
                "known_facts",
                "required_evidence",
                "max_steps",
                "max_tool_calls",
            },
            name="InvestigationGoal",
        )
        return cls(
            goal_id=payload["goal_id"],
            intent=payload["intent"],
            summary=payload["summary"],
            known_facts=payload.get("known_facts", {}),
            required_evidence=payload.get("required_evidence", []),
            max_steps=payload.get("max_steps", MAX_CROSS_DOMAIN_ACTIONS),
            max_tool_calls=payload.get("max_tool_calls", 20),
        )


@dataclass(frozen=True)
class InvestigationAction:
    """One policy-authorized, auditable next action; never a free-form Tool call."""

    action_id: str
    kind: str
    agent: str
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    required_evidence_ids: list[str] = field(default_factory=list)
    max_attempts: int = 1

    def __post_init__(self) -> None:
        _non_empty(self.action_id, "action_id")
        try:
            ActionKind(self.kind)
        except ValueError as exc:
            raise InvestigationValidationError("action kind is invalid") from exc
        _non_empty(self.agent, "agent")
        _non_empty(self.reason, "reason")
        _json_object(self.inputs, "inputs")
        _json_object(self.scope, "scope")
        _string_list(self.required_evidence_ids, "required_evidence_ids")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvestigationValidationError("max_attempts must be a positive integer")

    @property
    def deduplication_key(self) -> tuple[str, str]:
        """Return the stable Action + Scope key used by loop protection."""

        effective_scope = self.scope or self.inputs
        serialized_scope = json.dumps(
            effective_scope,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return self.kind, serialized_scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "agent": self.agent,
            "reason": self.reason,
            "inputs": dict(self.inputs),
            "scope": dict(self.scope),
            "required_evidence_ids": list(self.required_evidence_ids),
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={"action_id", "kind", "agent", "reason"},
            optional={"inputs", "scope", "required_evidence_ids", "max_attempts"},
            name="InvestigationAction",
        )
        return cls(
            action_id=payload["action_id"],
            kind=payload["kind"],
            agent=payload["agent"],
            reason=payload["reason"],
            inputs=payload.get("inputs", {}),
            scope=payload.get("scope", {}),
            required_evidence_ids=payload.get("required_evidence_ids", []),
            max_attempts=payload.get("max_attempts", 1),
        )


@dataclass(frozen=True)
class ActionRecord:
    """The immutable audit record for an action selection and its observation."""

    action: InvestigationAction
    status: str
    produced_finding_ids: list[str] = field(default_factory=list)
    produced_evidence_ids: list[str] = field(default_factory=list)
    decision_summary: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.action, InvestigationAction):
            raise InvestigationValidationError("action must be an InvestigationAction")
        if self.status not in {"completed", "skipped", "failed"}:
            raise InvestigationValidationError("status must be completed, skipped, or failed")
        _string_list(self.produced_finding_ids, "produced_finding_ids")
        _string_list(self.produced_evidence_ids, "produced_evidence_ids")
        _non_empty(self.decision_summary, "decision_summary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "status": self.status,
            "produced_finding_ids": list(self.produced_finding_ids),
            "produced_evidence_ids": list(self.produced_evidence_ids),
            "decision_summary": self.decision_summary,
        }


@dataclass(frozen=True)
class InvestigationQuestion:
    """One evidence gap expressed as a bounded engineering question."""

    question_id: str
    goal_id: str
    question: str
    rationale: str
    scope: dict[str, Any] = field(default_factory=dict)
    status: str = EvidenceGapStatus.OPEN.value
    answer: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        _non_empty(self.question_id, "question_id")
        _non_empty(self.goal_id, "goal_id")
        _non_empty(self.question, "question")
        _non_empty(self.rationale, "rationale")
        _json_object(self.scope, "scope")
        try:
            status = EvidenceGapStatus(self.status)
        except ValueError as exc:
            raise InvestigationValidationError("question status is invalid") from exc
        _string_list(self.evidence_ids, "evidence_ids")
        if status == EvidenceGapStatus.OPEN:
            if self.answer is not None or self.unavailable_reason is not None:
                raise InvestigationValidationError(
                    "an open question cannot have an answer or unavailable_reason"
                )
        elif status == EvidenceGapStatus.CLOSED:
            if self.answer is None:
                raise InvestigationValidationError("a closed question requires an answer")
            _non_empty(self.answer, "answer")
            if not self.evidence_ids:
                raise InvestigationValidationError(
                    "a closed question requires supporting evidence_ids"
                )
            if self.unavailable_reason is not None:
                raise InvestigationValidationError(
                    "a closed question cannot have unavailable_reason"
                )
        else:
            if self.unavailable_reason is None:
                raise InvestigationValidationError(
                    "an unavailable question requires unavailable_reason"
                )
            _non_empty(self.unavailable_reason, "unavailable_reason")
            if self.answer is not None:
                raise InvestigationValidationError(
                    "an unavailable question cannot have an answer"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "goal_id": self.goal_id,
            "question": self.question,
            "rationale": self.rationale,
            "scope": dict(self.scope),
            "status": self.status,
            "answer": self.answer,
            "evidence_ids": list(self.evidence_ids),
            "unavailable_reason": self.unavailable_reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={"question_id", "goal_id", "question", "rationale"},
            optional={
                "scope",
                "status",
                "answer",
                "evidence_ids",
                "unavailable_reason",
            },
            name="InvestigationQuestion",
        )
        return cls(
            question_id=payload["question_id"],
            goal_id=payload["goal_id"],
            question=payload["question"],
            rationale=payload["rationale"],
            scope=payload.get("scope", {}),
            status=payload.get("status", EvidenceGapStatus.OPEN.value),
            answer=payload.get("answer"),
            evidence_ids=payload.get("evidence_ids", []),
            unavailable_reason=payload.get("unavailable_reason"),
        )


@dataclass(frozen=True)
class IntentPlan:
    """The bounded Goal and initial evidence questions produced from user intent."""

    goal: InvestigationGoal
    questions: list[InvestigationQuestion]

    def __post_init__(self) -> None:
        if not isinstance(self.goal, InvestigationGoal):
            raise InvestigationValidationError("goal must be an InvestigationGoal")
        if not isinstance(self.questions, list) or not self.questions:
            raise InvestigationValidationError("questions must be a non-empty list")
        if len(self.questions) > MAX_INITIAL_QUESTIONS:
            raise InvestigationValidationError(
                f"questions must not exceed {MAX_INITIAL_QUESTIONS} items"
            )
        question_ids: list[str] = []
        for question in self.questions:
            if not isinstance(question, InvestigationQuestion):
                raise InvestigationValidationError(
                    "questions must contain InvestigationQuestion instances"
                )
            if question.goal_id != self.goal.goal_id:
                raise InvestigationValidationError(
                    "intent questions must reference the planned goal"
                )
            if question.status != EvidenceGapStatus.OPEN.value:
                raise InvestigationValidationError(
                    "initial intent questions must start open"
                )
            question_ids.append(question.question_id)
        if len(question_ids) != len(set(question_ids)):
            raise InvestigationValidationError("questions must not contain duplicate ids")

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal.to_dict(),
            "questions": [question.to_dict() for question in self.questions],
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={"goal", "questions"},
            optional=set(),
            name="IntentPlan",
        )
        raw_questions = payload["questions"]
        if not isinstance(raw_questions, list):
            raise InvestigationValidationError("questions must be a list")
        return cls(
            goal=InvestigationGoal.from_dict(payload["goal"]),
            questions=[
                InvestigationQuestion.from_dict(question) for question in raw_questions
            ],
        )


@dataclass(frozen=True)
class PlannerDecision:
    """One strict planner result: execute one action or stop explicitly."""

    decision_id: str
    goal_id: str
    decision_type: str
    reason: str
    goal_status: str
    proposed_conclusion_level: str
    next_action: InvestigationAction | None = None
    target_question_ids: list[str] = field(default_factory=list)
    new_questions: list[InvestigationQuestion] = field(default_factory=list)
    stop_reason: str | None = None
    question_updates: list[InvestigationQuestion] = field(default_factory=list)

    def __post_init__(self) -> None:
        _non_empty(self.decision_id, "decision_id")
        _non_empty(self.goal_id, "goal_id")
        _non_empty(self.reason, "reason")
        try:
            decision_type = DecisionType(self.decision_type)
        except ValueError as exc:
            raise InvestigationValidationError("decision_type is invalid") from exc
        try:
            goal_status = GoalStatus(self.goal_status)
        except ValueError as exc:
            raise InvestigationValidationError("goal_status is invalid") from exc
        try:
            ConclusionLevel(self.proposed_conclusion_level)
        except ValueError as exc:
            raise InvestigationValidationError("proposed_conclusion_level is invalid") from exc
        _string_list(self.target_question_ids, "target_question_ids")
        if not isinstance(self.new_questions, list):
            raise InvestigationValidationError("new_questions must be a list")
        question_ids: list[str] = []
        for question in self.new_questions:
            if not isinstance(question, InvestigationQuestion):
                raise InvestigationValidationError(
                    "new_questions must contain InvestigationQuestion instances"
                )
            if question.goal_id != self.goal_id:
                raise InvestigationValidationError(
                    "a planner decision cannot create a question for another goal"
                )
            if question.status != EvidenceGapStatus.OPEN.value:
                raise InvestigationValidationError("new planner questions must start open")
            question_ids.append(question.question_id)
        if len(question_ids) != len(set(question_ids)):
            raise InvestigationValidationError("new_questions must not contain duplicate ids")
        if not isinstance(self.question_updates, list):
            raise InvestigationValidationError("question_updates must be a list")
        updated_question_ids: list[str] = []
        for question in self.question_updates:
            if not isinstance(question, InvestigationQuestion):
                raise InvestigationValidationError(
                    "question_updates must contain InvestigationQuestion instances"
                )
            if question.goal_id != self.goal_id:
                raise InvestigationValidationError(
                    "a planner decision cannot update a question for another goal"
                )
            if question.status == EvidenceGapStatus.OPEN.value:
                raise InvestigationValidationError(
                    "question_updates must close a question or mark it unavailable"
                )
            updated_question_ids.append(question.question_id)
        if len(updated_question_ids) != len(set(updated_question_ids)):
            raise InvestigationValidationError(
                "question_updates must not contain duplicate ids"
            )
        if set(updated_question_ids) & set(question_ids):
            raise InvestigationValidationError(
                "a question cannot be both new and updated in one decision"
            )

        if decision_type == DecisionType.ACT:
            if not isinstance(self.next_action, InvestigationAction):
                raise InvestigationValidationError("an act decision requires next_action")
            if self.stop_reason is not None:
                raise InvestigationValidationError(
                    "an act decision cannot include stop_reason"
                )
            if goal_status != GoalStatus.IN_PROGRESS:
                raise InvestigationValidationError(
                    "an act decision requires goal_status=in_progress"
                )
            if not self.target_question_ids:
                raise InvestigationValidationError(
                    "an act decision must target at least one investigation question"
                )
        else:
            if self.next_action is not None:
                raise InvestigationValidationError(
                    "a stop decision cannot include next_action"
                )
            if self.stop_reason is None:
                raise InvestigationValidationError("a stop decision requires stop_reason")
            try:
                StopReason(self.stop_reason)
            except ValueError as exc:
                raise InvestigationValidationError("stop_reason is invalid") from exc
            if goal_status == GoalStatus.IN_PROGRESS:
                raise InvestigationValidationError(
                    "a stop decision cannot leave the goal in progress"
                )
            if self.new_questions:
                raise InvestigationValidationError(
                    "a stop decision cannot create new open questions"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "goal_id": self.goal_id,
            "decision_type": self.decision_type,
            "reason": self.reason,
            "goal_status": self.goal_status,
            "proposed_conclusion_level": self.proposed_conclusion_level,
            "next_action": self.next_action.to_dict() if self.next_action else None,
            "target_question_ids": list(self.target_question_ids),
            "new_questions": [question.to_dict() for question in self.new_questions],
            "stop_reason": self.stop_reason,
            "question_updates": [
                question.to_dict() for question in self.question_updates
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={
                "decision_id",
                "goal_id",
                "decision_type",
                "reason",
                "goal_status",
                "proposed_conclusion_level",
            },
            optional={
                "next_action",
                "target_question_ids",
                "new_questions",
                "stop_reason",
                "question_updates",
            },
            name="PlannerDecision",
        )
        raw_action = payload.get("next_action")
        if raw_action is not None and not isinstance(raw_action, dict):
            raise InvestigationValidationError("next_action must be a JSON object or null")
        raw_questions = payload.get("new_questions", [])
        if not isinstance(raw_questions, list):
            raise InvestigationValidationError("new_questions must be a list")
        raw_question_updates = payload.get("question_updates", [])
        if not isinstance(raw_question_updates, list):
            raise InvestigationValidationError("question_updates must be a list")
        return cls(
            decision_id=payload["decision_id"],
            goal_id=payload["goal_id"],
            decision_type=payload["decision_type"],
            reason=payload["reason"],
            goal_status=payload["goal_status"],
            proposed_conclusion_level=payload["proposed_conclusion_level"],
            next_action=(
                InvestigationAction.from_dict(raw_action) if raw_action is not None else None
            ),
            target_question_ids=payload.get("target_question_ids", []),
            new_questions=[
                InvestigationQuestion.from_dict(question) for question in raw_questions
            ],
            stop_reason=payload.get("stop_reason"),
            question_updates=[
                InvestigationQuestion.from_dict(question)
                for question in raw_question_updates
            ],
        )


@dataclass(frozen=True)
class DecisionEvaluation:
    """The three agreed metrics for one planner decision."""

    decision_id: str
    decision_valid: bool
    evidence_gain: bool
    redundant: bool
    reason: str
    new_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _non_empty(self.decision_id, "decision_id")
        _boolean(self.decision_valid, "decision_valid")
        _boolean(self.evidence_gain, "evidence_gain")
        _boolean(self.redundant, "redundant")
        _non_empty(self.reason, "reason")
        _string_list(self.new_evidence_ids, "new_evidence_ids")
        if self.evidence_gain != bool(self.new_evidence_ids):
            raise InvestigationValidationError(
                "evidence_gain must match whether new_evidence_ids are present"
            )
        if self.evidence_gain and self.redundant:
            raise InvestigationValidationError(
                "a decision with evidence gain cannot be redundant"
            )
        if not self.decision_valid and self.evidence_gain:
            raise InvestigationValidationError(
                "an invalid decision cannot claim evidence gain"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision_valid": self.decision_valid,
            "evidence_gain": self.evidence_gain,
            "redundant": self.redundant,
            "reason": self.reason,
            "new_evidence_ids": list(self.new_evidence_ids),
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={
                "decision_id",
                "decision_valid",
                "evidence_gain",
                "redundant",
                "reason",
            },
            optional={"new_evidence_ids"},
            name="DecisionEvaluation",
        )
        return cls(
            decision_id=payload["decision_id"],
            decision_valid=payload["decision_valid"],
            evidence_gain=payload["evidence_gain"],
            redundant=payload["redundant"],
            reason=payload["reason"],
            new_evidence_ids=payload.get("new_evidence_ids", []),
        )


@dataclass(frozen=True)
class RunEvaluation:
    """The two agreed outcome metrics plus the per-decision evaluations."""

    goal_id: str
    goal_success: bool
    stop_correct: bool
    summary: str
    decision_evaluations: list[DecisionEvaluation]

    def __post_init__(self) -> None:
        _non_empty(self.goal_id, "goal_id")
        _boolean(self.goal_success, "goal_success")
        _boolean(self.stop_correct, "stop_correct")
        _non_empty(self.summary, "summary")
        if not isinstance(self.decision_evaluations, list) or not self.decision_evaluations:
            raise InvestigationValidationError(
                "decision_evaluations must be a non-empty list"
            )
        decision_ids: list[str] = []
        for evaluation in self.decision_evaluations:
            if not isinstance(evaluation, DecisionEvaluation):
                raise InvestigationValidationError(
                    "decision_evaluations must contain DecisionEvaluation instances"
                )
            decision_ids.append(evaluation.decision_id)
        if len(decision_ids) != len(set(decision_ids)):
            raise InvestigationValidationError(
                "decision_evaluations must not contain duplicate decision ids"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_success": self.goal_success,
            "stop_correct": self.stop_correct,
            "summary": self.summary,
            "decision_evaluations": [
                evaluation.to_dict() for evaluation in self.decision_evaluations
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            required={
                "goal_id",
                "goal_success",
                "stop_correct",
                "summary",
                "decision_evaluations",
            },
            optional=set(),
            name="RunEvaluation",
        )
        raw_evaluations = payload["decision_evaluations"]
        if not isinstance(raw_evaluations, list):
            raise InvestigationValidationError("decision_evaluations must be a list")
        return cls(
            goal_id=payload["goal_id"],
            goal_success=payload["goal_success"],
            stop_correct=payload["stop_correct"],
            summary=payload["summary"],
            decision_evaluations=[
                DecisionEvaluation.from_dict(evaluation) for evaluation in raw_evaluations
            ],
        )
