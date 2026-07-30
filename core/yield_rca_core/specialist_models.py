"""Strict contracts for bounded Specialist V2 Tool selection and analysis.

The model chooses only a Python-issued candidate identifier.  Concrete Tool
names and parameters remain in :class:`SpecialistToolCandidate`, so structured
LLM output cannot replace the source Lot, broaden scope, or call an
unregistered Tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.models import AgentKind

MAX_SPECIALIST_TOOL_STEPS = 2
SPECIALIST_AGENT_KINDS = frozenset(
    {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
        AgentKind.KNOWLEDGE.value,
    }
)


class SpecialistValidationError(ValueError):
    """Raised when a Specialist V2 structured contract is invalid."""


class SpecialistDecisionType(StrEnum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"


class SpecialistStepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


def _non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecialistValidationError(f"{name} must be a non-empty string")
    return value


def _specialist_agent(value: object, name: str = "agent") -> str:
    agent = _non_empty(value, name)
    if agent not in SPECIALIST_AGENT_KINDS:
        raise SpecialistValidationError(
            f"{name} must be one of: {', '.join(sorted(SPECIALIST_AGENT_KINDS))}"
        )
    return agent


def _json_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecialistValidationError(f"{name} must be a JSON object")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise SpecialistValidationError(f"{name} must contain non-empty string keys")
    try:
        serialized = json.dumps(value, allow_nan=False)
        cloned = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise SpecialistValidationError(
            f"{name} must contain JSON-compatible values"
        ) from exc
    if not isinstance(cloned, dict):
        raise SpecialistValidationError(f"{name} must be a JSON object")
    return cloned


def _string_list(
    value: object,
    name: str,
    *,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list):
        raise SpecialistValidationError(f"{name} must be a list")
    if not allow_empty and not value:
        raise SpecialistValidationError(f"{name} must not be empty")
    normalized = [_non_empty(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if len(normalized) != len(set(normalized)):
        raise SpecialistValidationError(f"{name} must not contain duplicates")
    return normalized


def _strict_object(
    data: object,
    *,
    fields: set[str],
    name: str,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecialistValidationError(f"{name} must be a JSON object")
    if any(not isinstance(key, str) or not key.strip() for key in data):
        raise SpecialistValidationError(f"{name} must contain non-empty string keys")
    missing = fields - set(data)
    if missing:
        raise SpecialistValidationError(f"{name} is missing fields: {sorted(missing)}")
    unknown = set(data) - fields
    if unknown:
        raise SpecialistValidationError(f"{name} has unknown fields: {sorted(unknown)}")
    return data


@dataclass(frozen=True)
class SpecialistToolCandidate:
    """One Python-authorized Tool call with immutable model-facing arguments."""

    candidate_id: str
    tool_name: str
    parameters: dict[str, Any]
    purpose: str

    def __post_init__(self) -> None:
        _non_empty(self.candidate_id, "candidate_id")
        _non_empty(self.tool_name, "tool_name")
        object.__setattr__(
            self,
            "parameters",
            _json_object(self.parameters, "parameters"),
        )
        _non_empty(self.purpose, "purpose")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "tool_name": self.tool_name,
            "parameters": _json_object(self.parameters, "parameters"),
            "purpose": self.purpose,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            fields={"candidate_id", "tool_name", "parameters", "purpose"},
            name="SpecialistToolCandidate",
        )
        return cls(
            candidate_id=payload["candidate_id"],
            tool_name=payload["tool_name"],
            parameters=_json_object(payload["parameters"], "parameters"),
            purpose=payload["purpose"],
        )


@dataclass(frozen=True)
class SpecialistToolDecision:
    """Exactly one request to call a candidate or finish the local investigation."""

    decision_id: str
    action_id: str
    agent: str
    decision_type: str
    reason: str
    candidate_id: str | None
    stop_reason: str | None

    def __post_init__(self) -> None:
        _non_empty(self.decision_id, "decision_id")
        _non_empty(self.action_id, "action_id")
        _specialist_agent(self.agent)
        try:
            decision_type = SpecialistDecisionType(self.decision_type)
        except ValueError as exc:
            raise SpecialistValidationError(
                "decision_type must be call_tool or finish"
            ) from exc
        _non_empty(self.reason, "reason")
        if decision_type is SpecialistDecisionType.CALL_TOOL:
            _non_empty(self.candidate_id, "candidate_id")
            if self.stop_reason is not None:
                raise SpecialistValidationError(
                    "a call_tool decision cannot include stop_reason"
                )
        else:
            if self.candidate_id is not None:
                raise SpecialistValidationError(
                    "a finish decision cannot include candidate_id"
                )
            _non_empty(self.stop_reason, "stop_reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "agent": self.agent,
            "decision_type": self.decision_type,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            fields={
                "decision_id",
                "action_id",
                "agent",
                "decision_type",
                "reason",
                "candidate_id",
                "stop_reason",
            },
            name="SpecialistToolDecision",
        )
        candidate_id = payload["candidate_id"]
        stop_reason = payload["stop_reason"]
        if candidate_id is not None and not isinstance(candidate_id, str):
            raise SpecialistValidationError("candidate_id must be a string or null")
        if stop_reason is not None and not isinstance(stop_reason, str):
            raise SpecialistValidationError("stop_reason must be a string or null")
        return cls(
            decision_id=payload["decision_id"],
            action_id=payload["action_id"],
            agent=payload["agent"],
            decision_type=payload["decision_type"],
            reason=payload["reason"],
            candidate_id=candidate_id,
            stop_reason=stop_reason,
        )


@dataclass(frozen=True)
class SpecialistStepRecord:
    """Auditable record of one selected and executed local Tool candidate."""

    step_id: str
    step_index: int
    action_id: str
    agent: str
    decision_id: str
    candidate_id: str
    tool_name: str
    parameters: dict[str, Any]
    reason: str
    evidence_ids: list[str]
    output_summary: str
    status: str = SpecialistStepStatus.COMPLETED.value

    def __post_init__(self) -> None:
        _non_empty(self.step_id, "step_id")
        if (
            type(self.step_index) is not int
            or self.step_index < 1
            or self.step_index > MAX_SPECIALIST_TOOL_STEPS
        ):
            raise SpecialistValidationError(
                f"step_index must be between 1 and {MAX_SPECIALIST_TOOL_STEPS}"
            )
        _non_empty(self.action_id, "action_id")
        _specialist_agent(self.agent)
        _non_empty(self.decision_id, "decision_id")
        _non_empty(self.candidate_id, "candidate_id")
        _non_empty(self.tool_name, "tool_name")
        object.__setattr__(
            self,
            "parameters",
            _json_object(self.parameters, "parameters"),
        )
        _non_empty(self.reason, "reason")
        object.__setattr__(
            self,
            "evidence_ids",
            _string_list(self.evidence_ids, "evidence_ids", allow_empty=True),
        )
        _non_empty(self.output_summary, "output_summary")
        try:
            status = SpecialistStepStatus(self.status)
        except ValueError as exc:
            raise SpecialistValidationError("status must be completed or failed") from exc
        if status is SpecialistStepStatus.FAILED and self.evidence_ids:
            raise SpecialistValidationError(
                "a failed Specialist Tool step cannot claim evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "action_id": self.action_id,
            "agent": self.agent,
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "tool_name": self.tool_name,
            "parameters": _json_object(self.parameters, "parameters"),
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "output_summary": self.output_summary,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            fields={
                "step_id",
                "step_index",
                "action_id",
                "agent",
                "decision_id",
                "candidate_id",
                "tool_name",
                "parameters",
                "reason",
                "evidence_ids",
                "output_summary",
                "status",
            },
            name="SpecialistStepRecord",
        )
        return cls(
            step_id=payload["step_id"],
            step_index=payload["step_index"],
            action_id=payload["action_id"],
            agent=payload["agent"],
            decision_id=payload["decision_id"],
            candidate_id=payload["candidate_id"],
            tool_name=payload["tool_name"],
            parameters=_json_object(payload["parameters"], "parameters"),
            reason=payload["reason"],
            evidence_ids=_string_list(
                payload["evidence_ids"],
                "evidence_ids",
                allow_empty=True,
            ),
            output_summary=payload["output_summary"],
            status=payload["status"],
        )


@dataclass(frozen=True)
class SpecialistAnalysis:
    """Strict model-authored Finding draft backed by observed Evidence IDs."""

    summary: str
    confidence: float
    evidence_ids: list[str]
    engineering_interpretation: str

    def __post_init__(self) -> None:
        _non_empty(self.summary, "summary")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int | float)
            or not 0 <= float(self.confidence) <= 1
        ):
            raise SpecialistValidationError("confidence must be a number between 0 and 1")
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(
            self,
            "evidence_ids",
            _string_list(self.evidence_ids, "evidence_ids", allow_empty=False),
        )
        _non_empty(self.engineering_interpretation, "engineering_interpretation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "confidence": float(self.confidence),
            "evidence_ids": list(self.evidence_ids),
            "engineering_interpretation": self.engineering_interpretation,
        }

    @classmethod
    def from_dict(cls, data: object) -> Self:
        payload = _strict_object(
            data,
            fields={
                "summary",
                "confidence",
                "evidence_ids",
                "engineering_interpretation",
            },
            name="SpecialistAnalysis",
        )
        return cls(
            summary=payload["summary"],
            confidence=payload["confidence"],
            evidence_ids=_string_list(
                payload["evidence_ids"],
                "evidence_ids",
                allow_empty=False,
            ),
            engineering_interpretation=payload["engineering_interpretation"],
        )
