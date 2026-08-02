"""Deterministic Question-to-Evidence applicability resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from yield_rca_core.evidence_models import Evidence, EvidenceType
from yield_rca_core.investigation_models import (
    ActionRecord,
    InvestigationQuestion,
    QuestionEvidenceLink,
    QuestionEvidenceRelation,
)
from yield_rca_core.question_capability import (
    QUESTION_CAPABILITY_REGISTRY,
    action_scope_matches_question,
)

_GROUP_PREFERENCES: Mapping[str, tuple[str, ...]] = {
    EvidenceType.DEFECT_SIGNAL.value: (
        "product_signal",
        "shared_product_signal",
    ),
    EvidenceType.ELECTRICAL_FAILURE.value: (
        "product_signal",
        "shared_product_signal",
    ),
    EvidenceType.METROLOGY_DEVIATION.value: (
        "metrology_signal",
        "product_signal",
        "shared_product_signal",
    ),
    EvidenceType.NEGATIVE_SIGNAL.value: (
        "product_signal",
        "shared_product_signal",
        "process_anomaly",
        "shared_exposure",
        "context",
    ),
    EvidenceType.PARAMETER_DEVIATION.value: ("process_anomaly",),
    EvidenceType.TREND_DEVIATION.value: ("process_anomaly",),
    EvidenceType.SPC_VIOLATION.value: ("process_anomaly",),
    EvidenceType.OOC_EVENT.value: ("process_anomaly",),
    EvidenceType.EXCURSION_WINDOW.value: (
        "process_anomaly",
        "shared_exposure",
    ),
    EvidenceType.IMPACT_SCOPE.value: ("impact_scope", "shared_exposure"),
    EvidenceType.LOT_CONTEXT.value: ("shared_exposure", "lot_context"),
    EvidenceType.PROCESS_EXPOSURE.value: ("shared_exposure",),
    EvidenceType.EQUIPMENT_EXPOSURE.value: ("shared_exposure",),
    EvidenceType.RECIPE_CHANGE.value: ("recipe_context",),
    EvidenceType.HOLD_EVENT.value: ("lot_context",),
    EvidenceType.HISTORICAL_CASE_MATCH.value: ("historical_context",),
}


def _normalized(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip().casefold()
    return None


def _entity_values(evidence: Evidence, entity_type: str) -> set[str]:
    return {
        normalized
        for entity in evidence.entities
        if entity.entity_type == entity_type
        if (normalized := _normalized(entity.entity_id)) is not None
    }


def _metadata_values(evidence: Evidence, keys: Iterable[str]) -> set[str]:
    values: set[str] = set()
    for key in keys:
        raw = evidence.metadata.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(
                normalized
                for item in raw
                if (normalized := _normalized(item)) is not None
            )
        elif (normalized := _normalized(raw)) is not None:
            values.add(normalized)
    return values


def evidence_scope_matches_question(
    evidence: Evidence,
    question: InvestigationQuestion,
) -> bool:
    """Reject explicit entity contradictions while allowing omitted metadata."""

    entity_map = {
        "lot_id": _entity_values(evidence, "lot"),
        "lot_ids": _entity_values(evidence, "lot"),
        "product_id": _entity_values(evidence, "product")
        | _metadata_values(evidence, ("product_id",)),
        "operation": _entity_values(evidence, "operation")
        | _metadata_values(evidence, ("operation", "operation_no")),
        "operation_no": _entity_values(evidence, "operation")
        | _metadata_values(evidence, ("operation", "operation_no")),
        "equipment_id": _entity_values(evidence, "equipment")
        | _metadata_values(evidence, ("equipment_id",)),
        "chamber_id": _entity_values(evidence, "chamber")
        | _metadata_values(evidence, ("chamber_id",)),
    }
    for key, expected_raw in question.scope.items():
        normalized_key = str(key).casefold()
        if normalized_key not in entity_map:
            continue
        if isinstance(expected_raw, (list, tuple, set)):
            expected = {
                normalized
                for item in expected_raw
                if (normalized := _normalized(item)) is not None
            }
        else:
            expected = {
                normalized
            } if (normalized := _normalized(expected_raw)) is not None else set()
        observed = entity_map[normalized_key]
        if expected and observed and not expected <= observed:
            return False
    return True


def _matched_group(
    evidence: Evidence,
    contribution_groups: Sequence[str],
) -> str | None:
    preferences = _GROUP_PREFERENCES.get(evidence.evidence_type or "", ())
    for preferred in preferences:
        if preferred in contribution_groups:
            return preferred
    if "context" in contribution_groups:
        return "context"
    return None


class QuestionEvidenceResolver:
    """Create immutable links using only typed Python-owned contracts."""

    def resolve(
        self,
        *,
        questions: Sequence[InvestigationQuestion],
        action_record: ActionRecord,
        evidence: Sequence[Evidence],
    ) -> list[QuestionEvidenceLink]:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        produced_ids = action_record.produced_evidence_ids
        links: list[QuestionEvidenceLink] = []
        for question in questions:
            capability = QUESTION_CAPABILITY_REGISTRY.get(question.question_kind)
            if capability is None or not capability.supported:
                continue
            if action_record.action.kind not in capability.allowed_actions:
                continue
            if not action_scope_matches_question(action_record.action, question):
                continue
            contribution_groups = tuple(
                capability.contribution_for(action_record.action.kind)
            )
            for evidence_id in produced_ids:
                item = evidence_by_id.get(evidence_id)
                if item is None or not item.is_typed:
                    continue
                if not evidence_scope_matches_question(item, question):
                    continue
                if item.evidence_type == EvidenceType.DATA_MISSING.value:
                    links.append(
                        QuestionEvidenceLink(
                            question_id=question.question_id,
                            evidence_id=item.evidence_id,
                            action_id=action_record.action.action_id,
                            relation=QuestionEvidenceRelation.UNAVAILABLE.value,
                            matched_evidence_group="data_unavailable",
                            reason=(
                                "The registered Action produced typed Evidence that "
                                "the required source data is unavailable."
                            ),
                        )
                    )
                    continue
                if item.evidence_type not in capability.accepted_evidence_types:
                    continue
                matched_group = _matched_group(item, contribution_groups)
                if matched_group is None:
                    continue
                raw_relation = item.metadata.get("relation")
                relation = (
                    QuestionEvidenceRelation.CONTRADICTS.value
                    if raw_relation == QuestionEvidenceRelation.CONTRADICTS.value
                    else QuestionEvidenceRelation.SUPPORTS.value
                )
                links.append(
                    QuestionEvidenceLink(
                        question_id=question.question_id,
                        evidence_id=item.evidence_id,
                        action_id=action_record.action.action_id,
                        relation=relation,
                        matched_evidence_group=matched_group,
                        reason=(
                            f"Evidence type {item.evidence_type} is accepted for "
                            f"{question.question_kind} and matches the Action's "
                            f"{matched_group} contribution."
                        ),
                    )
                )
        return links


def resolve_question_evidence_links(
    *,
    questions: Sequence[InvestigationQuestion],
    action_record: ActionRecord,
    evidence: Sequence[Evidence],
) -> list[QuestionEvidenceLink]:
    return QuestionEvidenceResolver().resolve(
        questions=questions,
        action_record=action_record,
        evidence=evidence,
    )


__all__ = [
    "QuestionEvidenceResolver",
    "evidence_scope_matches_question",
    "resolve_question_evidence_links",
]
