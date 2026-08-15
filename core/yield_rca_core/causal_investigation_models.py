"""Python-owned models for adversarial causal investigation state.

``causal_scope.CausalLane`` is an existing enum describing broad search
directions.  This module deliberately uses ``CausalLaneRecord`` for one
concrete operation/equipment/chamber investigation path so the existing
scope contract remains backwards compatible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.evidence_models import SCHEMA_VERSION, ModelValidationError


def _non_empty(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ModelValidationError(f"{field_name} must be a list or tuple")
    result = tuple(_non_empty(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise ModelValidationError(f"{field_name} must not contain duplicates")
    return result


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _non_empty(value, field_name)


def _time_window(value: object, field_name: str) -> tuple[str, ...]:
    result = _string_tuple(value, field_name)
    if len(result) not in {0, 2}:
        raise ModelValidationError(f"{field_name} must contain zero or two timestamps")
    if not result:
        return result
    try:
        start = datetime.fromisoformat(result[0].replace("Z", "+00:00"))
        end = datetime.fromisoformat(result[1].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelValidationError(f"{field_name} must contain ISO-8601 timestamps") from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise ModelValidationError(f"{field_name} timestamps must include a timezone")
    if start > end:
        raise ModelValidationError(f"{field_name} start must not be after end")
    return result


class InvestigationLaneStatus(StrEnum):
    UNINVESTIGATED = "uninvestigated"
    IN_PROGRESS = "in_progress"
    EVIDENCE_COLLECTED = "evidence_collected"
    ELIMINATED = "eliminated"
    BLOCKED = "blocked"


class AlternativeSearchStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_SEARCHED = "not_searched"
    IN_PROGRESS = "in_progress"
    ALTERNATIVE_FOUND = "alternative_found"
    ALTERNATIVES_ELIMINATED = "alternatives_eliminated"
    UNRESOLVED = "unresolved"
    BLOCKED_BY_MISSING_DATA = "blocked_by_missing_data"


class CausalChainCompleteness(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CONFLICTING = "conflicting"


class ChallengeStatus(StrEnum):
    OPEN = "open"
    ALTERNATIVE_IDENTIFIED = "alternative_identified"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CausalLaneRecord:
    """One concrete, auditable causal investigation path."""

    lane_id: str
    operation: str = ""
    equipment: str = ""
    chamber: str = ""
    recipe: str = ""
    parameter_scope: tuple[str, ...] = ()
    exposed_lot_ids: tuple[str, ...] = ()
    time_window: tuple[str, ...] = ()
    initial_evidence_ids: tuple[str, ...] = ()
    priority_score: float = 0.0
    investigation_status: str = InvestigationLaneStatus.UNINVESTIGATED.value
    pruned_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _non_empty(self.lane_id, "lane_id")
        for field_name in ("operation", "equipment", "chamber", "recipe"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ModelValidationError(f"{field_name} must be a string")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(
            self,
            "parameter_scope",
            _string_tuple(self.parameter_scope, "parameter_scope"),
        )
        object.__setattr__(
            self,
            "exposed_lot_ids",
            _string_tuple(self.exposed_lot_ids, "exposed_lot_ids"),
        )
        object.__setattr__(
            self,
            "initial_evidence_ids",
            _string_tuple(self.initial_evidence_ids, "initial_evidence_ids"),
        )
        object.__setattr__(self, "time_window", _time_window(self.time_window, "time_window"))
        try:
            status = InvestigationLaneStatus(self.investigation_status).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in InvestigationLaneStatus)
            raise ModelValidationError(
                f"investigation_status must be one of: {allowed}"
            ) from exc
        object.__setattr__(self, "investigation_status", status)
        if not isinstance(self.priority_score, int | float) or not 0 <= float(
            self.priority_score
        ) <= 1:
            raise ModelValidationError("priority_score must be between 0 and 1")
        object.__setattr__(self, "priority_score", float(self.priority_score))
        if self.pruned_reason is not None:
            object.__setattr__(
                self,
                "pruned_reason",
                _non_empty(self.pruned_reason, "pruned_reason"),
            )
        if status in {
            InvestigationLaneStatus.ELIMINATED.value,
            InvestigationLaneStatus.BLOCKED.value,
        } and self.pruned_reason is None:
            raise ModelValidationError(
                "eliminated or blocked lanes require pruned_reason"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "operation": self.operation,
            "equipment": self.equipment,
            "chamber": self.chamber,
            "recipe": self.recipe,
            "parameter_scope": list(self.parameter_scope),
            "exposed_lot_ids": list(self.exposed_lot_ids),
            "time_window": list(self.time_window),
            "initial_evidence_ids": list(self.initial_evidence_ids),
            "priority_score": self.priority_score,
            "investigation_status": self.investigation_status,
            "pruned_reason": self.pruned_reason,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            lane_id=data["lane_id"],
            operation=data.get("operation", ""),
            equipment=data.get("equipment", ""),
            chamber=data.get("chamber", ""),
            recipe=data.get("recipe", ""),
            parameter_scope=tuple(data.get("parameter_scope", [])),
            exposed_lot_ids=tuple(data.get("exposed_lot_ids", [])),
            time_window=tuple(data.get("time_window", [])),
            initial_evidence_ids=tuple(data.get("initial_evidence_ids", [])),
            priority_score=float(data.get("priority_score", 0.0)),
            investigation_status=data.get(
                "investigation_status",
                InvestigationLaneStatus.UNINVESTIGATED.value,
            ),
            pruned_reason=data.get("pruned_reason"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class CandidateChallenge:
    """An auditable adversarial challenge for one RCA candidate.

    This is a state record, not the Candidate Generation response contract.
    Qwen may propose the challenge fields, but Python validates IDs and owns
    the resulting AlternativeSearchStatus.
    """

    candidate_id: str
    strongest_alternative_lane_id: str | None = None
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    unexplained_precursor_evidence_ids: tuple[str, ...] = ()
    distinguishing_gap_ids: tuple[str, ...] = ()
    challenge_explanation: str = ""
    status: str = ChallengeStatus.OPEN.value
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _non_empty(self.candidate_id, "candidate_id")
        object.__setattr__(
            self,
            "strongest_alternative_lane_id",
            _optional_string(
                self.strongest_alternative_lane_id,
                "strongest_alternative_lane_id",
            ),
        )
        for field_name in (
            "supporting_evidence_ids",
            "contradicting_evidence_ids",
            "unexplained_precursor_evidence_ids",
            "distinguishing_gap_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(getattr(self, field_name), field_name),
            )
        if not isinstance(self.challenge_explanation, str):
            raise ModelValidationError("challenge_explanation must be a string")
        object.__setattr__(self, "challenge_explanation", self.challenge_explanation.strip())
        try:
            status = ChallengeStatus(self.status).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in ChallengeStatus)
            raise ModelValidationError(f"status must be one of: {allowed}") from exc
        object.__setattr__(self, "status", status)
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strongest_alternative_lane_id": self.strongest_alternative_lane_id,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "unexplained_precursor_evidence_ids": list(
                self.unexplained_precursor_evidence_ids
            ),
            "distinguishing_gap_ids": list(self.distinguishing_gap_ids),
            "challenge_explanation": self.challenge_explanation,
            "status": self.status,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            candidate_id=data["candidate_id"],
            strongest_alternative_lane_id=data.get("strongest_alternative_lane_id"),
            supporting_evidence_ids=tuple(data.get("supporting_evidence_ids", [])),
            contradicting_evidence_ids=tuple(data.get("contradicting_evidence_ids", [])),
            unexplained_precursor_evidence_ids=tuple(
                data.get("unexplained_precursor_evidence_ids", [])
            ),
            distinguishing_gap_ids=tuple(data.get("distinguishing_gap_ids", [])),
            challenge_explanation=data.get("challenge_explanation", ""),
            status=data.get("status", ChallengeStatus.OPEN.value),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class CompetitionTrace:
    """Python-owned state describing whether alternatives were searched."""

    active_lane_ids: tuple[str, ...] = ()
    overflow_lane_ids: tuple[str, ...] = ()
    represented_lane_ids: tuple[str, ...] = ()
    unresolved_lane_ids: tuple[str, ...] = ()
    eliminated_lane_ids: tuple[str, ...] = ()
    alternative_search_status: str = AlternativeSearchStatus.NOT_SEARCHED.value
    challenge_round_count: int = 0
    resolution_evidence_ids: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "active_lane_ids",
            "overflow_lane_ids",
            "represented_lane_ids",
            "unresolved_lane_ids",
            "eliminated_lane_ids",
            "resolution_evidence_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(getattr(self, field_name), field_name),
            )
        if set(self.active_lane_ids) & set(self.overflow_lane_ids):
            raise ModelValidationError("active and overflow Lane IDs must be disjoint")
        if set(self.unresolved_lane_ids) & set(self.eliminated_lane_ids):
            raise ModelValidationError("unresolved and eliminated Lane IDs must be disjoint")
        try:
            status = AlternativeSearchStatus(self.alternative_search_status).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in AlternativeSearchStatus)
            raise ModelValidationError(
                f"alternative_search_status must be one of: {allowed}"
            ) from exc
        object.__setattr__(self, "alternative_search_status", status)
        if not isinstance(self.challenge_round_count, int) or self.challenge_round_count < 0:
            raise ModelValidationError("challenge_round_count must be a non-negative integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_lane_ids": list(self.active_lane_ids),
            "overflow_lane_ids": list(self.overflow_lane_ids),
            "represented_lane_ids": list(self.represented_lane_ids),
            "unresolved_lane_ids": list(self.unresolved_lane_ids),
            "eliminated_lane_ids": list(self.eliminated_lane_ids),
            "alternative_search_status": self.alternative_search_status,
            "challenge_round_count": self.challenge_round_count,
            "resolution_evidence_ids": list(self.resolution_evidence_ids),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            active_lane_ids=tuple(data.get("active_lane_ids", [])),
            overflow_lane_ids=tuple(data.get("overflow_lane_ids", [])),
            represented_lane_ids=tuple(data.get("represented_lane_ids", [])),
            unresolved_lane_ids=tuple(data.get("unresolved_lane_ids", [])),
            eliminated_lane_ids=tuple(data.get("eliminated_lane_ids", [])),
            alternative_search_status=data.get(
                "alternative_search_status",
                AlternativeSearchStatus.NOT_SEARCHED.value,
            ),
            challenge_round_count=int(data.get("challenge_round_count", 0)),
            resolution_evidence_ids=tuple(data.get("resolution_evidence_ids", [])),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


__all__ = [
    "AlternativeSearchStatus",
    "CandidateChallenge",
    "CausalChainCompleteness",
    "CausalLaneRecord",
    "ChallengeStatus",
    "CompetitionTrace",
    "InvestigationLaneStatus",
]
