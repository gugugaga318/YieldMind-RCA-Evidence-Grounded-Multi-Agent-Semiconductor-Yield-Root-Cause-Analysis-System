"""Compact, traceable synthesis of typed Evidence for RCA reasoning.

The synthesis is deliberately a projection, not an inference layer.  Every
fact in the returned object is copied from an immutable :class:`Evidence`
record and retains its source Evidence ID so a caller can always expand the
summary back to the original observation.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from yield_rca_core.evidence_models import Evidence, EvidenceType

_EXPOSURE_TYPES = {
    EvidenceType.LOT_CONTEXT.value,
    EvidenceType.PROCESS_EXPOSURE.value,
    EvidenceType.EQUIPMENT_EXPOSURE.value,
    EvidenceType.IMPACT_SCOPE.value,
    EvidenceType.EXCURSION_WINDOW.value,
}
_PROCESS_TYPES = {
    EvidenceType.RECIPE_CHANGE.value,
    EvidenceType.HOLD_EVENT.value,
    EvidenceType.PARAMETER_DEVIATION.value,
    EvidenceType.TREND_DEVIATION.value,
    EvidenceType.OOC_EVENT.value,
    EvidenceType.SPC_VIOLATION.value,
}
_OUTCOME_TYPES = {
    EvidenceType.DEFECT_SIGNAL.value,
    EvidenceType.METROLOGY_DEVIATION.value,
    EvidenceType.ELECTRICAL_FAILURE.value,
}
_KNOWLEDGE_TYPES = {
    EvidenceType.HISTORICAL_CASE_MATCH.value,
    EvidenceType.ENGINEERING_NOTE.value,
}


def _record(item: Evidence) -> dict[str, Any]:
    """Return only objective, JSON-safe fields from one Evidence item."""

    serialized = item.to_dict()
    return {
        "evidence_id": item.evidence_id,
        "evidence_type": item.evidence_type,
        "source_agent": item.source_agent,
        "source_tool": item.source_tool,
        "source_field": item.source_field,
        "timestamp": item.timestamp,
        "observation": item.observation,
        "summary": item.summary,
        "entities": [
            {
                "entity_type": entity["entity_type"],
                "entity_id": entity["entity_id"],
                "attributes": entity.get("attributes", {}),
            }
            for entity in serialized.get("entities", [])
        ],
        "metadata": serialized.get("metadata", {}),
    }


def build_evidence_synthesis(evidence: Iterable[Evidence]) -> dict[str, Any]:
    """Group typed Evidence into a compact, ID-traceable fact register.

    No causal conclusion is made here.  In particular, a Knowledge record is
    placed in ``knowledge`` and never promoted into a current-Lot process fact.
    """

    unique: dict[str, Evidence] = {}
    for item in evidence:
        if not isinstance(item, Evidence) or not item.is_typed:
            continue
        unique.setdefault(item.evidence_id, item)

    groups: dict[str, list[dict[str, Any]]] = {
        "shared_exposure": [],
        "process_excursions": [],
        "outcomes": [],
        "controls": [],
        "knowledge": [],
        "contradictions": [],
        "other": [],
    }
    for item in unique.values():
        record = _record(item)
        if item.evidence_type in _EXPOSURE_TYPES:
            groups["shared_exposure"].append(record)
        elif item.evidence_type in _PROCESS_TYPES:
            groups["process_excursions"].append(record)
        elif item.evidence_type in _OUTCOME_TYPES:
            groups["outcomes"].append(record)
        elif item.evidence_type == EvidenceType.NEGATIVE_SIGNAL.value:
            groups["controls"].append(record)
        elif item.evidence_type in _KNOWLEDGE_TYPES:
            groups["knowledge"].append(record)
        elif item.evidence_type in {
            EvidenceType.DATA_MISSING.value,
            EvidenceType.SOP_GUIDANCE.value,
        }:
            groups["other"].append(record)
        else:
            groups["other"].append(record)

        # A negative signal can be a contradiction only when its typed record
        # explicitly carries a contradiction marker.  We do not infer one from
        # the mere existence of a normal control.
        if bool(item.metadata.get("contradicts_candidate")):
            groups["contradictions"].append(record)

    return {
        "evidence_ids": sorted(unique),
        "evidence_count": len(unique),
        **groups,
    }


__all__ = ["build_evidence_synthesis"]
