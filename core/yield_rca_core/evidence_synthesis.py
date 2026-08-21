"""Compact, traceable synthesis of typed Evidence for RCA reasoning.

The synthesis is deliberately a projection, not an inference layer.  Every
fact in the returned object is copied from an immutable :class:`Evidence`
record and retains its source Evidence ID so a caller can always expand the
summary back to the original observation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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

_MAX_TEXT_LENGTH = 480
_MAX_ENTITIES_PER_EVIDENCE = 16
_MAX_SEQUENCE_ITEMS = 16
_METADATA_ALLOWLIST = frozenset(
    {
        "abnormal_row_count",
        "analysis_cutoff",
        "chamber_id",
        "context_source",
        "declared_source",
        "defect_counts",
        "direction",
        "evidence_scope",
        "equipment_id",
        "excursion_end",
        "excursion_start",
        "fail_count",
        "fail_modes",
        "insufficient_parameters",
        "lane_id",
        "lot_id",
        "lot_ids",
        "magnitude",
        "measurement_stage",
        "metric_name",
        "minimum_baseline_samples",
        "ooc_count",
        "operation_no",
        "parameter_name",
        "parameter_names",
        "pattern_counts",
        "processing_window",
        "recipe_id",
        "required_for_confirmation",
        "source_lot_id",
        "target_row_count",
        "unit",
        "validation_status",
    }
)
_ENTITY_ATTRIBUTE_ALLOWLIST = frozenset(
    {
        "direction",
        "dominant_pattern",
        "end",
        "magnitude",
        "role",
        "start",
        "status",
        "unit",
        "validation_status",
    }
)

_PRIMARY_GROUPS = (
    "shared_exposure",
    "process_excursions",
    "outcomes",
    "controls",
    "knowledge",
    "data_missing",
    "contradictions",
    "other",
)
_LANE_FACT_GROUPS = (
    "shared_exposure",
    "process_excursions",
    "outcomes",
    "controls",
    "approved_knowledge",
    "data_missing",
    "contradictions",
)
_GLOBAL_FACT_GROUPS = (
    "analysis_context",
    "unassigned_shared_exposure",
    "unassigned_process_excursions",
    "outcomes",
    "controls",
    "approved_knowledge",
    "data_missing",
    "contradictions",
)
_MAX_ACTIVE_LANES = 3
_MAX_FACTS_PER_GROUP = 8


def _bounded_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    compact = " ".join(value.split())
    if len(compact) <= _MAX_TEXT_LENGTH:
        return compact
    return compact[: _MAX_TEXT_LENGTH - 1].rstrip() + "…"


def _bounded_value(value: Any) -> Any:
    """Keep objective scalar structure while bounding high-cardinality payloads."""

    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item)
            for key, item in list(value.items())[:_MAX_SEQUENCE_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded_value(item) for item in list(value)[:_MAX_SEQUENCE_ITEMS]]
    return _bounded_text(str(value))


def compact_evidence_record(item: Evidence) -> dict[str, Any]:
    """Project one immutable Evidence item for an LLM-facing fact register.

    The full Evidence remains in :class:`RCAState` and is still consumed by
    every Python gate.  This projection deliberately excludes arbitrary Tool
    metadata and repeated prose so prompt size cannot grow with raw row width.
    """

    entities = []
    for entity in item.entities[:_MAX_ENTITIES_PER_EVIDENCE]:
        attributes = {
            key: _bounded_value(value)
            for key, value in entity.attributes.items()
            if key in _ENTITY_ATTRIBUTE_ALLOWLIST
        }
        projected: dict[str, Any] = {
            "entity_type": entity.entity_type,
            "entity_id": entity.entity_id,
        }
        if attributes:
            projected["attributes"] = attributes
        entities.append(projected)
    metadata = {
        key: _bounded_value(value)
        for key, value in item.metadata.items()
        if key in _METADATA_ALLOWLIST
    }
    fact = _bounded_text(item.observation) or _bounded_text(item.summary)
    record: dict[str, Any] = {
        "evidence_id": item.evidence_id,
        "evidence_type": item.evidence_type,
        "fact": fact,
        "entities": entities,
    }
    if item.timestamp:
        record["timestamp"] = item.timestamp
    if item.source_agent or item.source_tool or item.source_field:
        record["source"] = {
            "agent": item.source_agent,
            "tool": item.source_tool,
            "field": item.source_field,
        }
    if metadata:
        record["metadata"] = metadata
    if len(item.entities) > len(entities):
        record["entity_count"] = len(item.entities)
        record["entities_truncated"] = True
    return record


def _record(item: Evidence) -> dict[str, Any]:
    """Return only objective, JSON-safe fields from one Evidence item."""

    return compact_evidence_record(item)


def _primary_group(item: Evidence) -> str:
    if item.evidence_type in _EXPOSURE_TYPES:
        return "shared_exposure"
    if item.evidence_type in _PROCESS_TYPES:
        return "process_excursions"
    if item.evidence_type in _OUTCOME_TYPES:
        return "outcomes"
    if item.evidence_type == EvidenceType.NEGATIVE_SIGNAL.value:
        return "controls"
    if item.evidence_type in _KNOWLEDGE_TYPES:
        return "knowledge"
    if item.evidence_type == EvidenceType.DATA_MISSING.value:
        return "data_missing"
    return "other"


def _is_approved_knowledge(item: Evidence) -> bool:
    """Return whether typed Knowledge carries an explicit engineering approval."""

    if item.evidence_type not in _KNOWLEDGE_TYPES or item.source_type != "knowledge":
        return False
    statuses = [
        str(value).upper()
        for key, value in item.metadata.items()
        if str(key).casefold() == "validation_status"
    ]
    statuses.extend(
        str(value).upper()
        for entity in item.entities
        for key, value in entity.attributes.items()
        if str(key).casefold() == "validation_status"
    )
    return bool(statuses) and all(status == "CONFIRMED" for status in statuses)


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(
            dict.fromkeys(str(item).strip() for item in value if str(item).strip())
        )
    return ()


def _lane_projection(lane: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "lane_id": str(lane.get("lane_id", "")).strip(),
        "operation": str(lane.get("operation", "")).strip(),
        "equipment": str(lane.get("equipment", "")).strip(),
        "chamber": str(lane.get("chamber", "")).strip(),
        "recipe": str(lane.get("recipe", "")).strip(),
        "parameter_scope": list(_string_values(lane.get("parameter_scope", ()))),
        "exposed_lot_ids": list(_string_values(lane.get("exposed_lot_ids", ()))),
        "time_window": list(_string_values(lane.get("time_window", ()))),
        "initial_evidence_ids": list(
            _string_values(lane.get("initial_evidence_ids", ()))
        ),
        "priority_score": float(lane.get("priority_score", 0.0) or 0.0),
        "investigation_status": str(lane.get("investigation_status", "")).strip(),
    }
    if len(projected["parameter_scope"]) > _MAX_SEQUENCE_ITEMS:
        projected["parameter_scope_count"] = len(projected["parameter_scope"])
        projected["parameter_scope"] = projected["parameter_scope"][:_MAX_SEQUENCE_ITEMS]
    if len(projected["exposed_lot_ids"]) > _MAX_SEQUENCE_ITEMS:
        projected["exposed_lot_count"] = len(projected["exposed_lot_ids"])
        projected["exposed_lot_ids"] = projected["exposed_lot_ids"][:_MAX_SEQUENCE_ITEMS]
    return {key: value for key, value in projected.items() if value not in ("", [], ())}


def _active_lane_projections(
    causal_lanes: Sequence[Mapping[str, Any]],
    *,
    max_active_lanes: int,
) -> list[dict[str, Any]]:
    unique: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, lane in enumerate(causal_lanes):
        lane_id = str(lane.get("lane_id", "")).strip()
        if lane_id:
            unique[lane_id] = (index, lane)
    eligible = [
        (index, lane)
        for index, lane in unique.values()
        if str(lane.get("investigation_status", "")) not in {"eliminated", "blocked"}
    ]
    ordered = sorted(
        eligible,
        key=lambda value: (
            -float(value[1].get("priority_score", 0.0) or 0.0),
            value[0],
            str(value[1].get("lane_id", "")),
        ),
    )
    return [_lane_projection(lane) for _, lane in ordered[:max_active_lanes]]


def _entity_scope(item: Evidence) -> dict[str, set[str]]:
    entity_field = {
        "operation": "operation",
        "equipment": "equipment",
        "chamber": "chamber",
        "recipe": "recipe",
    }
    scope: dict[str, set[str]] = {field: set() for field in entity_field}
    for entity in item.entities:
        field = entity_field.get(entity.entity_type)
        if field is not None:
            scope[field].add(entity.entity_id)
    metadata_fields = {
        "operation": ("operation", "operation_no"),
        "equipment": ("equipment", "equipment_id"),
        "chamber": ("chamber", "chamber_id"),
        "recipe": ("recipe", "recipe_id"),
    }
    for field, keys in metadata_fields.items():
        for key in keys:
            scope[field].update(_string_values(item.metadata.get(key)))
    return scope


def _lane_binding(item: Evidence, lane: Mapping[str, Any]) -> str | None:
    """Bind Evidence to a Lane only through explicit IDs or matching typed scope."""

    lane_id = str(lane.get("lane_id", ""))
    explicit_lane_id = str(item.metadata.get("lane_id", "")).strip()
    if explicit_lane_id:
        return "explicit_lane_id" if explicit_lane_id == lane_id else None
    if item.evidence_id in set(_string_values(lane.get("initial_evidence_ids", ()))):
        return "initial_evidence_id"

    evidence_scope = _entity_scope(item)
    matched_dimensions = 0
    for field in ("operation", "equipment", "chamber", "recipe"):
        lane_value = str(lane.get(field, "")).strip()
        observed = evidence_scope[field]
        if not lane_value or not observed:
            continue
        if lane_value not in observed:
            return None
        matched_dimensions += 1
    # One broad equipment or operation match is not enough to assign a fact to
    # a concrete Lane. Two matching scope dimensions are an objective binding.
    return "typed_entity_scope" if matched_dimensions >= 2 else None


def _append_bounded(
    target: dict[str, list[dict[str, Any]]],
    group: str,
    record: dict[str, Any],
) -> bool:
    if len(target[group]) < _MAX_FACTS_PER_GROUP:
        target[group].append(record)
        return True
    return False


def _fact_counts(
    evidence: Sequence[Evidence],
    lane: Mapping[str, Any] | None,
) -> dict[str, int]:
    counts = {group: 0 for group in _LANE_FACT_GROUPS}
    for item in evidence:
        if lane is not None and _lane_binding(item, lane) is None:
            continue
        primary = _primary_group(item)
        if primary == "knowledge":
            if _is_approved_knowledge(item):
                counts["approved_knowledge"] += 1
        elif primary in counts:
            counts[primary] += 1
        if bool(item.metadata.get("contradicts_candidate")):
            counts["contradictions"] += 1
    return counts


def build_lane_first_evidence_synthesis(
    evidence: Iterable[Evidence],
    causal_lanes: Sequence[Mapping[str, Any]],
    *,
    max_active_lanes: int = _MAX_ACTIVE_LANES,
) -> dict[str, Any]:
    """Build a bounded Lane-first fact map for candidate generation.

    The projection selects active Lanes using Python-owned status and priority,
    then binds facts only through an explicit Lane ID, an initial Evidence ID,
    or at least two matching typed scope dimensions. Unbound product outcomes,
    controls, approved Knowledge, and missing-data facts remain visible as global
    context rather than being falsely attributed to every Lane.
    """

    if max_active_lanes < 1:
        raise ValueError("max_active_lanes must be at least 1")
    unique = {
        item.evidence_id: item
        for item in evidence
        if isinstance(item, Evidence) and item.is_typed
    }
    evidence_items = list(unique.values())
    active_lanes = _active_lane_projections(
        causal_lanes,
        max_active_lanes=max_active_lanes,
    )
    emitted_ids: set[str] = set()
    lane_summaries: list[dict[str, Any]] = []
    lane_bound_ids: set[str] = set()
    for lane in active_lanes:
        facts: dict[str, list[dict[str, Any]]] = {
            group: [] for group in _LANE_FACT_GROUPS
        }
        bindings: dict[str, str] = {}
        for item in evidence_items:
            binding = _lane_binding(item, lane)
            if binding is None:
                continue
            lane_bound_ids.add(item.evidence_id)
            primary = _primary_group(item)
            target_group = (
                "approved_knowledge"
                if primary == "knowledge" and _is_approved_knowledge(item)
                else primary
            )
            if target_group in facts:
                if _append_bounded(facts, target_group, _record(item)):
                    bindings[item.evidence_id] = binding
                    emitted_ids.add(item.evidence_id)
            if bool(item.metadata.get("contradicts_candidate")):
                if _append_bounded(facts, "contradictions", _record(item)):
                    emitted_ids.add(item.evidence_id)
        counts = _fact_counts(evidence_items, lane)
        lane_summaries.append(
            {
                **lane,
                "facts": facts,
                "fact_counts": counts,
                "facts_omitted": {
                    group: max(0, counts[group] - len(facts[group]))
                    for group in _LANE_FACT_GROUPS
                },
                "evidence_bindings": bindings,
            }
        )

    global_facts: dict[str, list[dict[str, Any]]] = {
        group: [] for group in _GLOBAL_FACT_GROUPS
    }
    global_counts = {group: 0 for group in _GLOBAL_FACT_GROUPS}
    for item in evidence_items:
        if item.evidence_id in lane_bound_ids:
            continue
        primary = _primary_group(item)
        global_target_group: str | None = None
        if item.evidence_type == EvidenceType.LOT_CONTEXT.value:
            global_target_group = "analysis_context"
        elif primary == "shared_exposure" and not active_lanes:
            global_target_group = "unassigned_shared_exposure"
        elif primary == "process_excursions" and not active_lanes:
            global_target_group = "unassigned_process_excursions"
        elif primary in {"outcomes", "controls", "data_missing"}:
            global_target_group = primary
        elif primary == "knowledge" and _is_approved_knowledge(item):
            global_target_group = "approved_knowledge"
        if global_target_group is not None:
            global_counts[global_target_group] += 1
            if _append_bounded(global_facts, global_target_group, _record(item)):
                emitted_ids.add(item.evidence_id)
        if bool(item.metadata.get("contradicts_candidate")):
            global_counts["contradictions"] += 1
            if _append_bounded(global_facts, "contradictions", _record(item)):
                emitted_ids.add(item.evidence_id)

    grouped = build_evidence_synthesis(evidence_items)
    return {
        "schema": "lane_first_v1",
        "evidence_count": len(evidence_items),
        "group_counts": {
            group: len(grouped[group])
            for group in _PRIMARY_GROUPS
        },
        "active_lane_count": len(lane_summaries),
        "active_causal_lanes": lane_summaries,
        "global_facts": global_facts,
        "global_fact_counts": global_counts,
        "global_facts_omitted": {
            group: max(0, global_counts[group] - len(global_facts[group]))
            for group in _GLOBAL_FACT_GROUPS
        },
        "prompt_evidence_ids": sorted(emitted_ids),
        "prompt_evidence_count": len(emitted_ids),
        "omitted_from_prompt_count": max(0, len(evidence_items) - len(emitted_ids)),
        "synthesis_note": (
            "Facts are copied from typed Evidence and retain Evidence IDs. "
            "Lane binding is organizational only and is not a causal conclusion."
        ),
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
        "data_missing": [],
        "contradictions": [],
        "other": [],
    }
    for item in unique.values():
        record = _record(item)
        groups[_primary_group(item)].append(record)

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


__all__ = [
    "build_evidence_synthesis",
    "build_lane_first_evidence_synthesis",
    "compact_evidence_record",
]
