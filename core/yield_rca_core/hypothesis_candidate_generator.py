"""Qwen proposes novel causal hypotheses; Python owns every acceptance gate."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix
from yield_rca_core.causal_hypothesis import CausalHypothesis
from yield_rca_core.evidence_models import EntityType, Evidence, EvidenceType
from yield_rca_core.evidence_synthesis import (
    build_lane_first_evidence_synthesis,
    compact_evidence_record,
)
from yield_rca_core.llm_gateway import (
    LLMClient,
    LLMOutputValidationError,
    LLMRequest,
)
from yield_rca_core.models import AgentFinding, AgentKind, ModelValidationError

_OUTPUT_ATTEMPTS = 2
_MAX_CANDIDATES = 2
_NON_SUPPORTING_TYPES = {
    EvidenceType.DATA_MISSING.value,
    EvidenceType.NEGATIVE_SIGNAL.value,
    EvidenceType.SOP_GUIDANCE.value,
}
_KNOWLEDGE_MECHANISM_TYPES = {
    EvidenceType.HISTORICAL_CASE_MATCH.value,
    EvidenceType.ENGINEERING_NOTE.value,
}
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
_PRODUCT_TYPES = {
    EvidenceType.DEFECT_SIGNAL.value,
    EvidenceType.METROLOGY_DEVIATION.value,
    EvidenceType.ELECTRICAL_FAILURE.value,
}
_DUPLICATE_EVIDENCE_OVERLAP_THRESHOLD = 0.75
_DUPLICATE_MECHANISM_SIMILARITY_THRESHOLD = 0.65


@dataclass(frozen=True)
class HypothesisCandidateProposal:
    """A model-authored explanation with IDs bound to immutable Evidence."""

    root_cause: str
    causal_explanation: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.root_cause, str) or not self.root_cause.strip():
            raise ModelValidationError("candidate root_cause must be non-empty")
        if self.root_cause.strip().casefold() == "inconclusive":
            raise ModelValidationError("inconclusive is not a hypothesis candidate")
        if (
            not isinstance(self.causal_explanation, str)
            or not self.causal_explanation.strip()
        ):
            raise ModelValidationError(
                "candidate causal_explanation must be non-empty"
            )
        for field_name, values in (
            ("supporting_evidence_ids", self.supporting_evidence_ids),
            ("contradicting_evidence_ids", self.contradicting_evidence_ids),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ModelValidationError(
                    f"candidate {field_name} must contain non-empty strings"
                )
            if len(values) != len(set(values)):
                raise ModelValidationError(
                    f"candidate {field_name} must not contain duplicates"
                )
        if not self.supporting_evidence_ids:
            raise ModelValidationError(
                "candidate supporting_evidence_ids must not be empty"
            )
        if set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids):
            raise ModelValidationError(
                "candidate Evidence cannot be both supporting and contradicting"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_cause": self.root_cause,
            "causal_explanation": self.causal_explanation,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
        }


@dataclass(frozen=True)
class HypothesisCandidateGeneration:
    candidates: tuple[HypothesisCandidateProposal, ...]
    attempt_count: int
    validation_errors: tuple[str, ...] = ()
    candidate_output_invalid: bool = False
    analysis_summary: str = ""
    targeted_investigation_results: tuple[dict[str, Any], ...] = ()
    competition_repair_exhausted: bool = False
    rejected_candidates: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _CandidateDedupSignature:
    lane_ids: tuple[str, ...]
    recipes: tuple[str, ...]
    chambers: tuple[str, ...]
    parameter_scope: tuple[str, ...]
    parameter_evidence_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    evidence_types: tuple[str, ...]
    discriminator_gap_ids: tuple[str, ...]
    mechanism_tokens: frozenset[str]


@dataclass(frozen=True)
class _DuplicateAssessment:
    is_duplicate: bool
    duplicate_score: float
    reason: str
    left_signature: _CandidateDedupSignature
    right_signature: _CandidateDedupSignature


def _evidence_register(
    findings: list[AgentFinding],
    context_evidence: Sequence[Evidence] = (),
    *,
    allowed_evidence_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    evidence_by_id = {
        evidence.evidence_id: evidence
        for finding in findings
        for evidence in finding.evidence
        if evidence.is_typed
    }
    evidence_by_id.update(
        {
            evidence.evidence_id: evidence
            for evidence in context_evidence
            if evidence.is_typed
        }
    )
    return [
        compact_evidence_record(evidence)
        for evidence_id, evidence in evidence_by_id.items()
        if allowed_evidence_ids is None or evidence_id in allowed_evidence_ids
    ]


def _eligible_evidence_ids_by_lane(
    evidence_by_id: dict[str, Evidence],
) -> dict[str, list[str]]:
    """Expose typed repair choices without claiming causal relevance for Qwen."""

    lane_types = {
        "shared_exposure": _EXPOSURE_TYPES,
        "process_anomaly": _PROCESS_TYPES,
        "product_outcome": _PRODUCT_TYPES,
    }
    result = {
        lane: sorted(
            evidence_id
            for evidence_id, evidence in evidence_by_id.items()
            if evidence.evidence_type in evidence_types
            and evidence.evidence_type not in _NON_SUPPORTING_TYPES
        )
        for lane, evidence_types in lane_types.items()
    }
    result["mechanism_support"] = sorted(
        evidence_id
        for evidence_id, evidence in evidence_by_id.items()
        if _is_approved_knowledge_support(evidence)
    )
    return result


def _is_approved_knowledge_support(evidence: Evidence) -> bool:
    """Knowledge may support mechanism only after explicit approval."""

    if (
        evidence.source_type != "knowledge"
        or evidence.evidence_type not in _KNOWLEDGE_MECHANISM_TYPES
    ):
        return False
    statuses = [
        str(value).upper()
        for key, value in evidence.metadata.items()
        if str(key).casefold() == "validation_status"
    ]
    statuses.extend(
        str(value).upper()
        for entity in evidence.entities
        for key, value in entity.attributes.items()
        if str(key).casefold() == "validation_status"
    )
    return bool(statuses) and all(status == "CONFIRMED" for status in statuses)


def _prior_candidate_mechanism_feedback(
    prior_candidates: Sequence[Mapping[str, Any]],
    *,
    evidence_by_id: Mapping[str, Evidence],
) -> list[dict[str, Any]]:
    """Expose Python-owned mechanism gaps during a reasoning refresh."""

    feedback: list[dict[str, Any]] = []
    for index, candidate in enumerate(prior_candidates[:_MAX_CANDIDATES]):
        root_cause = str(candidate.get("root_cause", "")).strip()
        explanation = str(
            candidate.get("causal_explanation", candidate.get("root_cause", ""))
        ).strip()
        supporting = tuple(
            dict.fromkeys(
                str(item)
                for item in candidate.get("supporting_evidence_ids", [])
                if str(item) in evidence_by_id
            )
        )
        contradicting = tuple(
            dict.fromkeys(
                str(item)
                for item in candidate.get("contradicting_evidence_ids", [])
                if str(item) in evidence_by_id and str(item) not in supporting
            )
        )
        if not root_cause or not explanation or not supporting:
            continue
        try:
            matrix = build_causal_evidence_matrix(
                CausalHypothesis(
                    root_cause=root_cause,
                    causal_explanation=explanation,
                    supporting_evidence_ids=supporting,
                    contradicting_evidence_ids=contradicting,
                ),
                evidence_by_id.values(),
            )
        except (TypeError, ValueError):
            continue
        mechanism = matrix.claims["mechanism"]
        feedback.append(
            {
                "candidate_index": index,
                "mechanism_status": mechanism.status,
                "mechanism_support_source": mechanism.support_source,
                "reason": mechanism.reason,
                "evidence_ids": list(mechanism.evidence_ids),
                "proposed_physical_bridge_terms": list(
                    mechanism.facts.get("proposed_physical_bridge_terms", [])
                ),
                "empirical_shared_lot_ids": list(
                    mechanism.facts.get("empirical_shared_lot_ids", [])
                ),
                "causal_chain_status": matrix.causal_chain_completeness,
            }
        )
    return feedback


def _candidate_repair_feedback(
    validation_error: str,
    *,
    evidence_by_id: dict[str, Evidence],
    candidate_competition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    eligible_by_lane = _eligible_evidence_ids_by_lane(evidence_by_id)
    return {
        "message": validation_error,
        "missing_causal_lanes": [],
        "eligible_supporting_evidence_ids_by_lane": eligible_by_lane,
        "source_agent_by_evidence_id": {
            evidence_id: evidence.source_agent
            for evidence_id, evidence in sorted(evidence_by_id.items())
            if evidence.source_agent is not None
        },
        "repair_instruction": (
            "Repair only the reported schema or Evidence-reference error. Use "
            "causally relevant IDs from eligible_supporting_evidence_ids_by_lane, "
            "but do not attach an irrelevant Evidence ID merely to make a candidate "
            "look complete. An incomplete evidence-bounded candidate is valid: "
            "Python records its missing lanes in CausalEvidenceMatrix and may seek "
            "targeted Evidence later. Return candidates=[] only when no causal "
            "candidate is justified at all."
        ),
        "valid_empty_output": {
            "candidates": [],
            "analysis_summary": (
                "No evidence-bounded causal candidate is justified."
            ),
        },
        "candidate_competition": (
            dict(candidate_competition)
            if candidate_competition is not None
            else None
        ),
    }


def _candidate_similarity_tokens(value: str) -> set[str]:
    """Return conservative content tokens for near-duplicate isolation."""

    ignored = {
        "abnormal",
        "abnormality",
        "cause",
        "causing",
        "control",
        "degradation",
        "drift",
        "excursion",
        "failure",
        "issue",
        "problem",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 2 and token not in ignored
    }


def _token_similarity(left: str, right: str) -> float:
    left_compact = re.sub(r"[^a-z0-9]+", "", left.casefold())
    right_compact = re.sub(r"[^a-z0-9]+", "", right.casefold())
    if left_compact and left_compact == right_compact:
        return 1.0
    left_tokens = _candidate_similarity_tokens(left)
    right_tokens = _candidate_similarity_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    containment = overlap / min(len(left_tokens), len(right_tokens))
    jaccard = overlap / len(left_tokens | right_tokens)
    return max(containment, jaccard)


def _root_causes_near_duplicate(left: str, right: str) -> bool:
    similarity = _token_similarity(left, right)
    return similarity >= 0.85


def _scope_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {
            item.strip().casefold()
            for item in value.split(",")
            if item.strip()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return {
            normalized
            for item in value
            for normalized in _scope_values(item)
        }
    return set()


def _metadata_scope_values(evidence: Evidence, *keys: str) -> set[str]:
    normalized_keys = {key.casefold() for key in keys}
    values = {
        normalized
        for key, value in evidence.metadata.items()
        if str(key).casefold() in normalized_keys
        for normalized in _scope_values(value)
    }
    values.update(
        normalized
        for entity in evidence.entities
        for key, value in entity.attributes.items()
        if str(key).casefold() in normalized_keys
        for normalized in _scope_values(value)
    )
    return values


def _entity_scope_values(evidence: Evidence, entity_type: str) -> set[str]:
    return {
        entity.entity_id.strip().casefold()
        for entity in evidence.entities
        if entity.entity_type == entity_type and entity.entity_id.strip()
    }


def _overlap_coefficient(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _candidate_discriminator_gap_ids(
    proposal: HypothesisCandidateProposal,
    competition_context: Mapping[str, Any],
) -> set[str]:
    supporting_ids = set(proposal.supporting_evidence_ids)
    return {
        str(item.get("gap_id", "")).strip()
        for item in competition_context.get("targeted_investigation_results", [])
        if isinstance(item, Mapping)
        and str(item.get("gap_id", "")).strip()
        and supporting_ids
        & {
            str(evidence_id)
            for evidence_id in item.get("new_supporting_evidence_ids", [])
        }
    }


def _candidate_dedup_signature(
    proposal: HypothesisCandidateProposal,
    *,
    evidence_by_id: Mapping[str, Evidence],
    competition_context: Mapping[str, Any],
) -> _CandidateDedupSignature:
    supporting_evidence = [
        evidence_by_id[evidence_id]
        for evidence_id in proposal.supporting_evidence_ids
        if evidence_id in evidence_by_id
    ]
    lane_ids = {
        value
        for evidence in supporting_evidence
        for value in _metadata_scope_values(evidence, "lane_id")
    }
    recipes = {
        value
        for evidence in supporting_evidence
        for value in (
            _entity_scope_values(evidence, EntityType.RECIPE.value)
            | _metadata_scope_values(evidence, "recipe", "recipe_id")
        )
    }
    chambers = {
        value
        for evidence in supporting_evidence
        for value in (
            _entity_scope_values(evidence, EntityType.CHAMBER.value)
            | _metadata_scope_values(evidence, "chamber", "chamber_id")
        )
    }
    parameter_scope = {
        value
        for evidence in supporting_evidence
        for value in (
            _entity_scope_values(evidence, EntityType.PARAMETER.value)
            | _metadata_scope_values(
                evidence,
                "parameter",
                "parameter_name",
                "parameter_scope",
                "parameters",
            )
        )
    }
    parameter_evidence_ids = {
        evidence.evidence_id
        for evidence in supporting_evidence
        if (
            evidence.evidence_type in _PROCESS_TYPES
            and (
                _entity_scope_values(evidence, EntityType.PARAMETER.value)
                or _metadata_scope_values(
                    evidence,
                    "parameter",
                    "parameter_name",
                    "parameter_scope",
                    "parameters",
                )
            )
        )
    }
    scope_tokens = {
        token
        for value in (
            *lane_ids,
            *recipes,
            *chambers,
            *parameter_scope,
            *[
                entity.entity_id.casefold()
                for evidence in supporting_evidence
                for entity in evidence.entities
            ],
        )
        for token in re.findall(r"[a-z0-9]+", value)
    }
    mechanism_tokens = _candidate_similarity_tokens(
        f"{proposal.root_cause} {proposal.causal_explanation}"
    ) - scope_tokens - {
        "candidate",
        "chamber",
        "equipment",
        "mechanism",
        "operation",
        "process",
        "recipe",
        "root",
    }
    return _CandidateDedupSignature(
        lane_ids=tuple(sorted(lane_ids)),
        recipes=tuple(sorted(recipes)),
        chambers=tuple(sorted(chambers)),
        parameter_scope=tuple(sorted(parameter_scope)),
        parameter_evidence_ids=tuple(sorted(parameter_evidence_ids)),
        supporting_evidence_ids=tuple(sorted(proposal.supporting_evidence_ids)),
        evidence_types=tuple(
            sorted(
                {
                    str(evidence.evidence_type)
                    for evidence in supporting_evidence
                }
            )
        ),
        discriminator_gap_ids=tuple(
            sorted(
                _candidate_discriminator_gap_ids(
                    proposal,
                    competition_context,
                )
            )
        ),
        mechanism_tokens=frozenset(mechanism_tokens),
    )


def _candidate_duplicate_assessment(
    left: HypothesisCandidateProposal,
    right: HypothesisCandidateProposal,
    *,
    evidence_by_id: Mapping[str, Evidence],
    competition_context: Mapping[str, Any],
) -> _DuplicateAssessment:
    left_signature = _candidate_dedup_signature(
        left,
        evidence_by_id=evidence_by_id,
        competition_context=competition_context,
    )
    right_signature = _candidate_dedup_signature(
        right,
        evidence_by_id=evidence_by_id,
        competition_context=competition_context,
    )
    text_similarity = _token_similarity(left.root_cause, right.root_cause)
    evidence_overlap = _overlap_coefficient(
        set(left_signature.supporting_evidence_ids),
        set(right_signature.supporting_evidence_ids),
    )
    left_mechanism = set(left_signature.mechanism_tokens)
    right_mechanism = set(right_signature.mechanism_tokens)
    mechanism_similarity = (
        1.0
        if not left_mechanism and not right_mechanism
        else _overlap_coefficient(left_mechanism, right_mechanism)
    )
    duplicate_score = round(
        0.35 * text_similarity
        + 0.35 * evidence_overlap
        + 0.30 * mechanism_similarity,
        3,
    )

    differentiators = (
        ("different_lane_id", left_signature.lane_ids, right_signature.lane_ids),
        ("different_recipe", left_signature.recipes, right_signature.recipes),
        ("different_chamber", left_signature.chambers, right_signature.chambers),
        (
            "different_parameter_scope",
            left_signature.parameter_scope,
            right_signature.parameter_scope,
        ),
        (
            "different_parameter_evidence",
            left_signature.parameter_evidence_ids,
            right_signature.parameter_evidence_ids,
        ),
        (
            "different_discriminator_gap",
            left_signature.discriminator_gap_ids,
            right_signature.discriminator_gap_ids,
        ),
    )
    for reason, left_values, right_values in differentiators:
        if (left_values or right_values) and left_values != right_values:
            return _DuplicateAssessment(
                is_duplicate=False,
                duplicate_score=duplicate_score,
                reason=reason,
                left_signature=left_signature,
                right_signature=right_signature,
            )
    if mechanism_similarity < _DUPLICATE_MECHANISM_SIMILARITY_THRESHOLD:
        return _DuplicateAssessment(
            is_duplicate=False,
            duplicate_score=duplicate_score,
            reason="different_causal_mechanism",
            left_signature=left_signature,
            right_signature=right_signature,
        )
    if evidence_overlap < _DUPLICATE_EVIDENCE_OVERLAP_THRESHOLD:
        return _DuplicateAssessment(
            is_duplicate=False,
            duplicate_score=duplicate_score,
            reason="insufficient_supporting_evidence_overlap",
            left_signature=left_signature,
            right_signature=right_signature,
        )
    if not _root_causes_near_duplicate(left.root_cause, right.root_cause):
        return _DuplicateAssessment(
            is_duplicate=False,
            duplicate_score=duplicate_score,
            reason="materially_different_root_cause_text",
            left_signature=left_signature,
            right_signature=right_signature,
        )
    return _DuplicateAssessment(
        is_duplicate=True,
        duplicate_score=duplicate_score,
        reason=(
            "same_lane_identity; supporting_evidence_overlap="
            f"{evidence_overlap:.3f}; same_causal_mechanism_family; "
            f"root_cause_text_similarity={text_similarity:.3f}"
        ),
        left_signature=left_signature,
        right_signature=right_signature,
    )


def _rejected_candidate_audit(
    proposal: HypothesisCandidateProposal,
    *,
    candidate_index: int,
    compared_candidate_index: int,
    output_attempt: int,
    assessment: _DuplicateAssessment,
) -> dict[str, Any]:
    signature = assessment.left_signature
    return {
        "rejected_candidate_index": candidate_index,
        "candidate_root_cause": proposal.root_cause,
        "lane_id": signature.lane_ids[0] if len(signature.lane_ids) == 1 else None,
        "lane_ids": list(signature.lane_ids),
        "recipe": signature.recipes[0] if len(signature.recipes) == 1 else None,
        "recipes": list(signature.recipes),
        "chambers": list(signature.chambers),
        "parameter_scope": list(signature.parameter_scope),
        "evidence_ids": list(proposal.supporting_evidence_ids),
        "evidence_types": list(signature.evidence_types),
        "discriminator_gap_ids": list(signature.discriminator_gap_ids),
        "causal_mechanism_tokens": sorted(signature.mechanism_tokens),
        "duplicate_score": assessment.duplicate_score,
        "duplicate_reason": assessment.reason,
        "compared_candidate_id": f"candidate_{compared_candidate_index}",
        "output_attempt": output_attempt,
    }


def _normalized_lane_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only immutable Lane facts needed to interpret targeted Evidence."""

    return {
        key: value.get(key)
        for key in (
            "lane_id",
            "operation",
            "equipment",
            "chamber",
            "recipe",
            "parameter_scope",
            "exposed_lot_ids",
            "time_window",
            "investigation_status",
        )
        if value.get(key) not in (None, "", [], ())
    }


def _normalized_prior_challenge(
    value: Mapping[str, Any],
    *,
    evidence_by_id: Mapping[str, Evidence],
) -> dict[str, Any]:
    known_ids = set(evidence_by_id)
    return {
        "candidate_id": str(value.get("candidate_id", "")),
        "strongest_alternative_lane_id": value.get(
            "strongest_alternative_lane_id",
            value.get("strongest_alternative"),
        ),
        "supporting_evidence_ids": [
            str(item)
            for item in value.get("supporting_evidence_ids", [])
            if str(item) in known_ids
        ],
        "contradicting_evidence_ids": [
            str(item)
            for item in value.get("contradicting_evidence_ids", [])
            if str(item) in known_ids
        ],
        "unexplained_precursor_evidence_ids": [
            str(item)
            for item in value.get("unexplained_precursor_evidence_ids", [])
            if str(item) in known_ids
        ],
        "distinguishing_gap_ids": [
            str(item) for item in value.get("distinguishing_gap_ids", [])
        ],
        "challenge_explanation": str(value.get("challenge_explanation", "")),
        "status": str(value.get("status", "")),
    }


def _evidence_matches_lane(evidence: Evidence, lane_id: str) -> bool:
    return bool(lane_id) and str(evidence.metadata.get("lane_id", "")) == lane_id


def _supports_discriminator(evidence: Evidence, discriminator_kind: str) -> bool:
    """Return whether one typed observation can answer the selected Gap kind."""

    if evidence.evidence_type in _NON_SUPPORTING_TYPES:
        return False
    if discriminator_kind == "parameter_anomaly":
        return evidence.evidence_type in _PROCESS_TYPES
    if discriminator_kind in {"exposure_commonality", "recipe_commonality"}:
        return evidence.evidence_type in _EXPOSURE_TYPES | {
            EvidenceType.RECIPE_CHANGE.value
        }
    if discriminator_kind == "product_outcome":
        return evidence.evidence_type in _PRODUCT_TYPES
    if discriminator_kind == "mechanism_context":
        return _is_approved_knowledge_support(evidence)
    if discriminator_kind == "temporal_alignment":
        return evidence.evidence_type in _PROCESS_TYPES | _EXPOSURE_TYPES
    return True


def _candidate_competition_context(
    *,
    evidence_by_id: Mapping[str, Evidence],
    new_evidence_ids: Sequence[str],
    prior_challenges: Sequence[Mapping[str, Any]],
    prior_causal_gaps: Sequence[Mapping[str, Any]],
    causal_lanes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind a prior Challenge to the Evidence collected specifically for it."""

    known_new_ids = [
        evidence_id
        for evidence_id in dict.fromkeys(str(item) for item in new_evidence_ids)
        if evidence_id in evidence_by_id
    ]
    gap_by_id = {
        str(item.get("gap_id", "")): item
        for item in prior_causal_gaps
        if str(item.get("gap_id", "")).strip()
    }
    normalized_challenges = [
        _normalized_prior_challenge(item, evidence_by_id=evidence_by_id)
        for item in prior_challenges
    ]
    targeted_results: list[dict[str, Any]] = []
    relevant_lane_ids: set[str] = set()
    for challenge in normalized_challenges:
        lane_id = str(challenge.get("strongest_alternative_lane_id") or "")
        if lane_id:
            relevant_lane_ids.add(lane_id)
        for gap_id in challenge.get("distinguishing_gap_ids", []):
            gap = gap_by_id.get(str(gap_id), {})
            raw_scope = gap.get("target_scope", {})
            target_scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
            gap_lane_id = str(target_scope.get("lane_id", "") or lane_id)
            discriminator_kind = str(gap.get("discriminator_kind", ""))
            if gap_lane_id:
                relevant_lane_ids.add(gap_lane_id)
            scoped_new_ids = [
                evidence_id
                for evidence_id in known_new_ids
                if _evidence_matches_lane(evidence_by_id[evidence_id], gap_lane_id)
            ]
            supporting_ids = [
                evidence_id
                for evidence_id in scoped_new_ids
                if _supports_discriminator(
                    evidence_by_id[evidence_id], discriminator_kind
                )
            ]
            targeted_results.append(
                {
                    "gap_id": str(gap_id),
                    "discriminator_kind": discriminator_kind,
                    "lane_id": gap_lane_id,
                    "target_scope": target_scope,
                    "new_evidence_ids": scoped_new_ids,
                    "new_supporting_evidence_ids": supporting_ids,
                    "new_data_missing_evidence_ids": [
                        evidence_id
                        for evidence_id in scoped_new_ids
                        if evidence_by_id[evidence_id].evidence_type
                        == EvidenceType.DATA_MISSING.value
                    ],
                    "new_evidence": [
                        compact_evidence_record(evidence_by_id[evidence_id])
                        for evidence_id in scoped_new_ids
                    ],
                    "answered": bool(scoped_new_ids),
                    "support_observed": bool(supporting_ids),
                }
            )
    relevant_lanes = [
        _normalized_lane_payload(item)
        for item in causal_lanes
        if str(item.get("lane_id", "")) in relevant_lane_ids
    ]
    targeted_supporting_ids = list(
        dict.fromkeys(
            evidence_id
            for item in targeted_results
            for evidence_id in item["new_supporting_evidence_ids"]
        )
    )
    return {
        "new_evidence_ids_since_prior": known_new_ids,
        "prior_candidate_challenges": normalized_challenges,
        "targeted_investigation_results": targeted_results,
        "relevant_causal_lanes": relevant_lanes,
        "targeted_supporting_evidence_ids": targeted_supporting_ids,
        "requires_distinct_candidate_review": bool(targeted_supporting_ids),
    }


def _competition_repair_required(
    proposals: Sequence[HypothesisCandidateProposal],
    *,
    prior_candidates: Sequence[Mapping[str, Any]],
    competition_context: Mapping[str, Any],
) -> bool:
    targeted_ids = {
        str(item)
        for item in competition_context.get(
            "targeted_supporting_evidence_ids", []
        )
    }
    prior_roots = [
        str(candidate.get("root_cause", "")).strip()
        for candidate in prior_candidates
        if str(candidate.get("root_cause", "")).strip()
    ]
    if not targeted_ids or not prior_roots:
        return False
    targeted_proposals = [
        proposal
        for proposal in proposals
        if targeted_ids & set(proposal.supporting_evidence_ids)
    ]
    if len(proposals) >= 2 and targeted_proposals:
        # Pairwise Lane-aware Dedup already proved that the surviving proposals
        # differ by causal scope, supporting Evidence, or mechanism.
        return False
    return not any(
        not any(
            _root_causes_near_duplicate(proposal.root_cause, prior_root)
            for prior_root in prior_roots
        )
        for proposal in targeted_proposals
    )


def _parse_candidate(
    payload: object,
    *,
    index: int,
    evidence_by_id: dict[str, Evidence],
) -> HypothesisCandidateProposal:
    if not isinstance(payload, dict):
        raise LLMOutputValidationError(f"candidates[{index}] must be an object")
    expected = {
        "root_cause",
        "causal_explanation",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
    }
    if set(payload) != expected:
        raise LLMOutputValidationError(
            f"candidates[{index}] must contain exactly {sorted(expected)}"
        )
    if not isinstance(payload.get("root_cause"), str) or not isinstance(
        payload.get("causal_explanation"), str
    ):
        raise LLMOutputValidationError(
            f"candidates[{index}] root_cause and causal_explanation must be strings"
        )
    supporting = payload.get("supporting_evidence_ids")
    contradicting = payload.get("contradicting_evidence_ids")
    if not isinstance(supporting, list) or not isinstance(contradicting, list):
        raise LLMOutputValidationError(
            f"candidates[{index}] Evidence IDs must be arrays"
        )
    try:
        proposal = HypothesisCandidateProposal(
            root_cause=payload["root_cause"].strip(),
            causal_explanation=payload["causal_explanation"].strip(),
            supporting_evidence_ids=tuple(supporting),
            contradicting_evidence_ids=tuple(contradicting),
        )
    except ModelValidationError as exc:
        raise LLMOutputValidationError(str(exc)) from exc
    referenced = set(proposal.supporting_evidence_ids) | set(
        proposal.contradicting_evidence_ids
    )
    unknown = sorted(referenced - set(evidence_by_id))
    if unknown:
        raise LLMOutputValidationError(
            f"candidates[{index}] references unknown Evidence IDs: {unknown}"
        )
    invalid_support = sorted(
        evidence_id
        for evidence_id in proposal.supporting_evidence_ids
        if (
            evidence_by_id[evidence_id].evidence_type in _NON_SUPPORTING_TYPES
            or (
                evidence_by_id[evidence_id].evidence_type
                in _KNOWLEDGE_MECHANISM_TYPES
                and not _is_approved_knowledge_support(evidence_by_id[evidence_id])
            )
        )
    )
    if invalid_support:
        raise LLMOutputValidationError(
            f"candidates[{index}] uses non-supporting Evidence as support: "
            f"{invalid_support}"
        )
    return proposal


@dataclass(frozen=True)
class QwenHypothesisCandidateGenerator:
    """Generate at most two proposals without deciding the RCA conclusion."""

    llm_client: LLMClient
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if self.llm_client is None:
            raise ModelValidationError(
                "Qwen Hypothesis Candidate Generator requires an LLM client"
            )

    def generate(
        self,
        *,
        request_id: str,
        findings: list[AgentFinding],
        context_evidence: Sequence[Evidence] = (),
        prior_candidates: Sequence[Mapping[str, Any]] = (),
        prior_challenges: Sequence[Mapping[str, Any]] = (),
        prior_causal_gaps: Sequence[Mapping[str, Any]] = (),
        causal_lanes: Sequence[Mapping[str, Any]] = (),
        new_evidence_ids: Sequence[str] = (),
    ) -> HypothesisCandidateGeneration:
        evidence_by_id = {
            evidence.evidence_id: evidence
            for finding in findings
            for evidence in finding.evidence
            if evidence.is_typed
        }
        evidence_by_id.update(
            {
                evidence.evidence_id: evidence
                for evidence in context_evidence
                if evidence.is_typed
            }
        )
        if not evidence_by_id:
            return HypothesisCandidateGeneration(candidates=(), attempt_count=0)
        competition_context = _candidate_competition_context(
            evidence_by_id=evidence_by_id,
            new_evidence_ids=new_evidence_ids,
            prior_challenges=prior_challenges,
            prior_causal_gaps=prior_causal_gaps,
            causal_lanes=causal_lanes,
        )
        evidence_synthesis = build_lane_first_evidence_synthesis(
            evidence_by_id.values(),
            causal_lanes,
        )
        prompt_evidence_ids = {
            str(item)
            for item in evidence_synthesis.get("prompt_evidence_ids", [])
            if str(item) in evidence_by_id
        }
        prompt_evidence_ids.update(
            evidence_id
            for candidate in prior_candidates[:_MAX_CANDIDATES]
            for field in ("supporting_evidence_ids", "contradicting_evidence_ids")
            for evidence_id in (str(item) for item in candidate.get(field, []))
            if evidence_id in evidence_by_id
        )
        prompt_evidence_ids.update(
            str(item)
            for item in competition_context["new_evidence_ids_since_prior"]
            if str(item) in evidence_by_id
        )
        for challenge in competition_context["prior_candidate_challenges"]:
            for field in (
                "supporting_evidence_ids",
                "contradicting_evidence_ids",
                "unexplained_precursor_evidence_ids",
            ):
                prompt_evidence_ids.update(
                    str(item)
                    for item in challenge.get(field, [])
                    if str(item) in evidence_by_id
                )
        # A partial or legacy workflow can reach candidate generation before
        # concrete Lane discovery. Avoid silently sending an empty register.
        if not prompt_evidence_ids:
            prompt_evidence_ids.update(evidence_by_id)
        evidence_synthesis["prompt_evidence_ids"] = sorted(prompt_evidence_ids)
        evidence_synthesis["prompt_evidence_count"] = len(prompt_evidence_ids)
        evidence_synthesis["omitted_from_prompt_count"] = max(
            0,
            len(evidence_by_id) - len(prompt_evidence_ids),
        )
        evidence_register = _evidence_register(
            findings,
            context_evidence,
            allowed_evidence_ids=prompt_evidence_ids,
        )
        prompt_evidence_by_id = {
            evidence_id: evidence_by_id[evidence_id]
            for evidence_id in prompt_evidence_ids
        }
        prior_mechanism_feedback = _prior_candidate_mechanism_feedback(
            prior_candidates,
            evidence_by_id=evidence_by_id,
        )

        validation_errors: list[str] = []
        rejected_candidates: list[dict[str, Any]] = []
        for attempt in range(1, _OUTPUT_ATTEMPTS + 1):
            request = LLMRequest(
                agent=AgentKind.RCA_REASONING.value,
                prompt_name="hypothesis_candidate_generator",
                prompt_version=self.prompt_version,
                payload={
                    "request_id": request_id,
                    "specialist_findings": [
                        {
                            "finding_id": finding.finding_id,
                            "agent": finding.agent,
                            "summary": finding.summary,
                            "confidence": finding.confidence,
                            "evidence_count": len(finding.evidence_ids),
                        }
                        for finding in findings
                    ],
                    "typed_evidence_register": evidence_register,
                    "evidence_synthesis": evidence_synthesis,
                    "prior_authoritative_candidates": [
                        {
                            "root_cause": str(candidate.get("root_cause", "")),
                            "causal_explanation": str(
                                candidate.get(
                                    "causal_explanation",
                                    candidate.get("root_cause", ""),
                                )
                            ),
                            "supporting_evidence_ids": [
                                str(item)
                                for item in candidate.get(
                                    "supporting_evidence_ids", []
                                )
                                if str(item) in evidence_by_id
                            ],
                            "contradicting_evidence_ids": [
                                str(item)
                                for item in candidate.get(
                                    "contradicting_evidence_ids", []
                                )
                                if str(item) in evidence_by_id
                            ],
                        }
                        for candidate in prior_candidates[:_MAX_CANDIDATES]
                        if str(candidate.get("root_cause", "")).strip()
                    ],
                    "prior_candidate_mechanism_feedback": prior_mechanism_feedback,
                    "new_evidence_ids_since_prior": competition_context[
                        "new_evidence_ids_since_prior"
                    ],
                    "prior_candidate_challenges": competition_context[
                        "prior_candidate_challenges"
                    ],
                    "targeted_investigation_results": competition_context[
                        "targeted_investigation_results"
                    ],
                    "relevant_causal_lanes": competition_context[
                        "relevant_causal_lanes"
                    ],
                    "max_candidates": _MAX_CANDIDATES,
                    "output_attempt": attempt,
                    "previous_validation_feedback": (
                        _candidate_repair_feedback(
                            validation_errors[-1],
                            evidence_by_id=prompt_evidence_by_id,
                            candidate_competition=competition_context,
                        )
                        if validation_errors
                        else None
                    ),
                    "deterministic_candidate_proposals": [],
                },
                temperature=0.0,
            )
            try:
                response = self.llm_client.complete_json(request)
            except LLMOutputValidationError as exc:
                validation_errors.append(str(exc).strip() or type(exc).__name__)
                continue
            try:
                if set(response.data) != {"candidates", "analysis_summary"}:
                    raise LLMOutputValidationError(
                        "candidate output must contain exactly candidates and "
                        "analysis_summary"
                    )
                raw_candidates = response.data.get("candidates")
                summary = response.data.get("analysis_summary")
                if not isinstance(raw_candidates, list) or len(raw_candidates) > _MAX_CANDIDATES:
                    raise LLMOutputValidationError(
                        f"candidates must be an array with at most {_MAX_CANDIDATES} items"
                    )
                if not isinstance(summary, str) or not summary.strip():
                    raise LLMOutputValidationError(
                        "analysis_summary must be a non-empty string"
                    )
                proposals_list: list[HypothesisCandidateProposal] = []
                candidate_errors: list[str] = []
                for index, candidate in enumerate(raw_candidates):
                    try:
                        proposals_list.append(
                            _parse_candidate(
                                candidate,
                                index=index,
                                evidence_by_id=evidence_by_id,
                            )
                        )
                    except (LLMOutputValidationError, TypeError, ValueError) as exc:
                        candidate_errors.append(
                            str(exc).strip() or f"candidates[{index}] is invalid"
                        )
                if candidate_errors and proposals_list:
                    validation_errors.extend(candidate_errors)
                if candidate_errors and not proposals_list:
                    validation_errors.extend(candidate_errors)
                    if attempt < _OUTPUT_ATTEMPTS:
                        continue
                    return HypothesisCandidateGeneration(
                        candidates=(),
                        attempt_count=attempt,
                        validation_errors=tuple(validation_errors),
                        candidate_output_invalid=True,
                        analysis_summary=summary.strip(),
                        targeted_investigation_results=tuple(
                            competition_context["targeted_investigation_results"]
                        ),
                        rejected_candidates=tuple(rejected_candidates),
                    )
                distinct: list[HypothesisCandidateProposal] = []
                distinct_candidate_indexes: list[int] = []
                for index, proposal in enumerate(proposals_list):
                    duplicate_match: tuple[int, _DuplicateAssessment] | None = None
                    for distinct_index, existing in zip(
                        distinct_candidate_indexes,
                        distinct,
                        strict=True,
                    ):
                        assessment = _candidate_duplicate_assessment(
                            proposal,
                            existing,
                            evidence_by_id=evidence_by_id,
                            competition_context=competition_context,
                        )
                        if assessment.is_duplicate:
                            duplicate_match = (distinct_index, assessment)
                            break
                    if duplicate_match is not None:
                        compared_index, assessment = duplicate_match
                        validation_errors.append(
                            f"candidates[{index}] is a near-duplicate of an earlier "
                            f"candidate ({assessment.reason}) and was isolated"
                        )
                        rejected_candidates.append(
                            _rejected_candidate_audit(
                                proposal,
                                candidate_index=index,
                                compared_candidate_index=compared_index,
                                output_attempt=attempt,
                                assessment=assessment,
                            )
                        )
                        continue
                    distinct.append(proposal)
                    distinct_candidate_indexes.append(index)
                proposals = tuple(distinct)
                competition_repair_required = _competition_repair_required(
                    proposals,
                    prior_candidates=prior_candidates,
                    competition_context=competition_context,
                )
                if competition_repair_required and attempt < _OUTPUT_ATTEMPTS:
                    validation_errors.append(
                        "candidate competition is incomplete: targeted Evidence "
                        "supports review of an alternative causal Lane, but no "
                        "materially distinct candidate cites that Evidence"
                    )
                    continue
                return HypothesisCandidateGeneration(
                    candidates=proposals,
                    attempt_count=attempt,
                    validation_errors=tuple(validation_errors),
                    analysis_summary=summary.strip(),
                    targeted_investigation_results=tuple(
                        competition_context["targeted_investigation_results"]
                    ),
                    competition_repair_exhausted=competition_repair_required,
                    rejected_candidates=tuple(rejected_candidates),
                )
            except (LLMOutputValidationError, TypeError, ValueError) as exc:
                validation_errors.append(str(exc).strip() or type(exc).__name__)

        raise LLMOutputValidationError(
            "Qwen Hypothesis Candidate Generator returned invalid output twice: "
            + " | ".join(validation_errors)
        )


__all__ = [
    "HypothesisCandidateGeneration",
    "HypothesisCandidateProposal",
    "QwenHypothesisCandidateGenerator",
]
