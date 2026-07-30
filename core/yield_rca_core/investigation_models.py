"""Typed contracts for the controlled ReAct RCA orchestration mode.

These contracts deliberately do not execute Tools or Agents.  They describe
the bounded investigation actions a deterministic policy may select after each
observation.  Keeping this layer framework-free makes the policy and its audit
trail independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InvestigationValidationError(ValueError):
    """Raised when a controlled investigation contract is invalid."""


class OrchestrationMode(StrEnum):
    FIXED = "fixed"
    CONTROLLED_REACT = "controlled_react"


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


@dataclass(frozen=True)
class InvestigationGoal:
    """The user outcome and bounded resources for one investigation."""

    goal_id: str
    intent: str
    summary: str
    known_facts: dict[str, Any] = field(default_factory=dict)
    required_evidence: list[str] = field(default_factory=list)
    max_steps: int = 8
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
        if not isinstance(self.max_steps, int) or self.max_steps < 1:
            raise InvestigationValidationError("max_steps must be a positive integer")
        if not isinstance(self.max_tool_calls, int) or self.max_tool_calls < 1:
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


@dataclass(frozen=True)
class InvestigationAction:
    """One policy-authorized, auditable next action; never a free-form Tool call."""

    action_id: str
    kind: str
    agent: str
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)
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
        _string_list(self.required_evidence_ids, "required_evidence_ids")
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise InvestigationValidationError("max_attempts must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "agent": self.agent,
            "reason": self.reason,
            "inputs": dict(self.inputs),
            "required_evidence_ids": list(self.required_evidence_ids),
            "max_attempts": self.max_attempts,
        }


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
