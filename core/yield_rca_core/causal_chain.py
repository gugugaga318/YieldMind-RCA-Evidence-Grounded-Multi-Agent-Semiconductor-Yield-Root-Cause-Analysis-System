"""Deterministic causal-chain and source-availability assessment.

The Qwen candidate remains a compact explanation.  This module turns the
typed claim results produced by :mod:`causal_evidence_matrix` into an explicit
``exposure -> parameter -> mechanism -> outcome`` chain.  It also preserves
the distinction between an unobserved claim and a source that was explicitly
reported as unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from yield_rca_core.causal_hypothesis import CausalClaimStatus
from yield_rca_core.causal_investigation_models import CausalChainCompleteness
from yield_rca_core.evidence_models import (
    EVIDENCE_SCHEMA_VERSION,
    AgentKind,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
)

_SUPPORTED = CausalClaimStatus.SUPPORTED.value
_INCOMPLETE = CausalClaimStatus.INCOMPLETE.value
_CONFLICTED = CausalClaimStatus.CONFLICTED.value
_UNAVAILABLE = CausalClaimStatus.UNAVAILABLE.value


@dataclass(frozen=True)
class DataMissingSource:
    """A typed declaration that a source could not answer a question."""

    evidence_id: str
    source_type: str
    source_id: str
    source_table: str | None
    source_field: str | None
    observation: str
    entity_ids: tuple[str, ...] = ()
    required_for_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_table": self.source_table,
            "source_field": self.source_field,
            "observation": self.observation,
            "entity_ids": list(self.entity_ids),
            "required_for_confirmation": self.required_for_confirmation,
        }


@dataclass(frozen=True)
class CausalChainAssessment:
    """Python-owned assessment of the candidate's causal chain."""

    status: str
    stages: Mapping[str, str]
    evidence_ids: tuple[str, ...] = ()
    missing_stages: tuple[str, ...] = ()
    conflicting_stages: tuple[str, ...] = ()
    data_missing_evidence_ids: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        try:
            normalized = CausalChainCompleteness(self.status).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in CausalChainCompleteness)
            raise ValueError(f"causal chain status must be one of: {allowed}") from exc
        object.__setattr__(self, "status", normalized)

    @property
    def complete(self) -> bool:
        return self.status == CausalChainCompleteness.COMPLETE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stages": dict(self.stages),
            "evidence_ids": list(self.evidence_ids),
            "missing_stages": list(self.missing_stages),
            "conflicting_stages": list(self.conflicting_stages),
            "data_missing_evidence_ids": list(self.data_missing_evidence_ids),
            "reason": self.reason,
        }


def collect_data_missing_sources(evidence: Iterable[Evidence]) -> tuple[DataMissingSource, ...]:
    """Return explicit typed unavailable-source declarations in stable order."""

    sources: list[DataMissingSource] = []
    seen: set[str] = set()
    for item in evidence:
        if item.evidence_type != EvidenceType.DATA_MISSING.value:
            continue
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        entity_ids = tuple(
            sorted(
                entity.entity_id
                for entity in item.entities
                if entity.entity_type
                in {
                    EntityType.LOT.value,
                    EntityType.OPERATION.value,
                    EntityType.EQUIPMENT.value,
                    EntityType.CHAMBER.value,
                    EntityType.PARAMETER.value,
                }
            )
        )
        sources.append(
            DataMissingSource(
                evidence_id=item.evidence_id,
                source_type=item.source_type,
                source_id=item.source_id,
                source_table=item.source_table,
                source_field=item.source_field,
                observation=item.observation or item.summary,
                entity_ids=entity_ids,
                required_for_confirmation=(
                    item.metadata.get("required_for_confirmation") is True
                ),
            )
        )
    return tuple(sources)


def build_declared_unavailable_evidence(
    declared_sources: Iterable[str],
    *,
    source_lot_id: str,
) -> tuple[Evidence, ...]:
    """Convert trusted workflow context into typed DATA_MISSING Evidence.

    Public evaluation context may declare a data source unavailable, but it
    may not provide operational facts.  The resulting Evidence therefore
    carries only the source declaration and source-Lot scope.  Each declared
    source is confirmation-blocking until the deployment supplies that data.
    """

    lot_id = str(source_lot_id).strip()
    if not lot_id:
        raise ValueError("declared unavailable sources require source_lot_id")
    normalized = tuple(dict.fromkeys(str(item).strip() for item in declared_sources))
    if any(not item for item in normalized):
        raise ValueError("declared unavailable sources must be non-empty strings")
    return tuple(
        Evidence(
            evidence_id=f"EV_CONTEXT_DATA_MISSING_{index:02d}",
            source_type=EvidenceSourceType.SYSTEM.value,
            source_id="formal_case_context",
            summary=f"Declared source unavailable: {source}.",
            source_table="formal_case_context",
            source_field=source,
            metadata={
                "context_source": "formal_case_context",
                "declared_source": source,
                "required_for_confirmation": True,
            },
            evidence_type=EvidenceType.DATA_MISSING.value,
            source_agent=AgentKind.PLANNER.value,
            source_tool="formal_case_context",
            observation=f"{source} data is unavailable for this RCA case.",
            entities=(EvidenceEntity(EntityType.LOT.value, lot_id),),
            confidence=1.0,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        )
        for index, source in enumerate(normalized, start=1)
    )


def _claim_status(claims: Mapping[str, Any], claim: str) -> str:
    result = claims.get(claim)
    status = getattr(result, "status", None)
    if status is None and isinstance(result, Mapping):
        status = result.get("status")
    return str(status or _UNAVAILABLE)


def _claim_evidence_ids(claims: Mapping[str, Any], claim: str) -> tuple[str, ...]:
    result = claims.get(claim)
    values = getattr(result, "evidence_ids", None)
    if values is None and isinstance(result, Mapping):
        values = result.get("evidence_ids", [])
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if str(value).strip())


def assess_causal_chain(
    claims: Mapping[str, Any],
    *,
    data_missing_evidence_ids: Iterable[str] = (),
) -> CausalChainAssessment:
    """Assess the causal chain from Python-owned claim statuses.

    ``exposure`` is a derived stage: a scope claim plus at least one typed
    equipment/chamber/operation claim is sufficient to establish that the
    candidate is attached to a process exposure.  It deliberately does not
    infer a mechanism from Knowledge text.
    """

    entity_statuses = {
        claim: _claim_status(claims, claim)
        for claim in ("equipment", "chamber", "operation")
    }
    scope_status = _claim_status(claims, "scope")
    if _CONFLICTED in {*entity_statuses.values(), scope_status}:
        exposure_status = _CONFLICTED
    elif scope_status == _SUPPORTED and _SUPPORTED in entity_statuses.values():
        exposure_status = _SUPPORTED
    elif _UNAVAILABLE in {*entity_statuses.values(), scope_status}:
        exposure_status = _UNAVAILABLE
    else:
        exposure_status = _INCOMPLETE

    stages = {
        "exposure": exposure_status,
        "parameter": _claim_status(claims, "parameter"),
        "mechanism": _claim_status(claims, "mechanism"),
        "outcome": _claim_status(claims, "outcome"),
        "temporal": _claim_status(claims, "temporal"),
        "scope": scope_status,
    }
    chain_stages = ("exposure", "parameter", "mechanism", "outcome")
    conflicting = tuple(stage for stage in chain_stages if stages[stage] == _CONFLICTED)
    missing = tuple(
        stage for stage in chain_stages if stages[stage] in {_INCOMPLETE, _UNAVAILABLE}
    )
    if conflicting:
        status = CausalChainCompleteness.CONFLICTING.value
        reason = "The typed Evidence contains a conflict in the causal chain."
    elif not missing and stages["temporal"] == _SUPPORTED and scope_status == _SUPPORTED:
        status = CausalChainCompleteness.COMPLETE.value
        reason = (
            "Exposure, abnormal parameter, mechanism, outcome, temporal, and scope "
            "claims converge."
        )
    else:
        status = CausalChainCompleteness.INCOMPLETE.value
        reason = "One or more causal-chain stages remain incomplete or unavailable."

    ids: list[str] = []
    for claim in (
        "equipment",
        "chamber",
        "operation",
        "scope",
        "parameter",
        "mechanism",
        "outcome",
        "temporal",
    ):
        ids.extend(_claim_evidence_ids(claims, claim))
    missing_ids = tuple(dict.fromkeys(str(item) for item in data_missing_evidence_ids))
    return CausalChainAssessment(
        status=status,
        stages=stages,
        evidence_ids=tuple(dict.fromkeys(ids)),
        missing_stages=missing,
        conflicting_stages=conflicting,
        data_missing_evidence_ids=missing_ids,
        reason=reason,
    )


# Descriptive aliases make the Python-owned boundary discoverable to callers
# without introducing a second implementation.
build_causal_chain_assessment = assess_causal_chain
extract_data_missing_sources = collect_data_missing_sources


__all__ = [
    "CausalChainAssessment",
    "DataMissingSource",
    "assess_causal_chain",
    "build_causal_chain_assessment",
    "build_declared_unavailable_evidence",
    "collect_data_missing_sources",
    "extract_data_missing_sources",
]
