"""Deterministic Confirmation and Impact-Lot gates for RCA results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from yield_rca_core.causal_evidence_matrix import CausalEvidenceMatrix
from yield_rca_core.causal_hypothesis import CausalClaim, CausalClaimStatus, CausalHypothesis
from yield_rca_core.evidence_models import EntityType, Evidence, EvidenceType

CONCLUSION_SUPPORTED = "supported"
CONCLUSION_INCONCLUSIVE = "inconclusive"
CONCLUSION_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

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
_EXPOSURE_TYPES = {
    EvidenceType.LOT_CONTEXT.value,
    EvidenceType.PROCESS_EXPOSURE.value,
    EvidenceType.EQUIPMENT_EXPOSURE.value,
    EvidenceType.IMPACT_SCOPE.value,
    EvidenceType.EXCURSION_WINDOW.value,
}


@dataclass(frozen=True)
class ConfirmationGateResult:
    """Python-owned final status and audit checks for one candidate."""

    status: str
    checks: Mapping[str, bool]
    reasons: tuple[str, ...] = ()
    unresolved_gaps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "unresolved_gaps": list(self.unresolved_gaps),
        }


def _status(matrix: CausalEvidenceMatrix, claim: str) -> str:
    result = matrix.claims.get(claim)
    return (
        str(cast(Any, result).status)
        if result is not None
        else CausalClaimStatus.UNAVAILABLE.value
    )


def confirm_candidate(
    matrix: CausalEvidenceMatrix,
    *,
    alternative_matrices: Sequence[CausalEvidenceMatrix] = (),
    strict: bool = True,
) -> ConfirmationGateResult:
    """Apply the final Python confirmation gate.

    ``strict=False`` is retained for legacy deterministic snapshots that were
    created before timestamps and chamber/operation entities were mandatory.
    It still rejects explicit conflicts and missing causal lanes.  The active
    Qwen path uses the strict gate.
    """

    checks: dict[str, bool] = {}
    reasons: list[str] = []
    gaps: list[str] = []

    for claim in (
        CausalClaim.EQUIPMENT.value,
        CausalClaim.CHAMBER.value,
        CausalClaim.OPERATION.value,
    ):
        claim_status = _status(matrix, claim)
        # Equipment/chamber/operation are hard checks only when the candidate
        # explicitly names a structured entity.  A generic mechanism can remain
        # analyzable with legacy evidence that lacks one of these entities.
        explicit = any(
            token.casefold() in matrix.candidate.root_cause.casefold()
            for token in matrix.claims[claim].facts.get("entity_ids_by_type", {}).get(
                claim, []
            )
        ) if claim in matrix.claims else False
        checks[claim] = claim_status != CausalClaimStatus.CONFLICTED.value
        if explicit and claim_status != CausalClaimStatus.SUPPORTED.value:
            checks[claim] = False
        if not checks[claim]:
            reasons.append(f"{claim} claim is conflicted or not grounded.")
            gaps.append(f"{claim}.{claim_status}")

    required_claims = (
        CausalClaim.PARAMETER.value,
        CausalClaim.OUTCOME.value,
        CausalClaim.MECHANISM.value,
        CausalClaim.SCOPE.value,
    )
    for claim in required_claims:
        claim_status = _status(matrix, claim)
        checks[claim] = claim_status == CausalClaimStatus.SUPPORTED.value
        if not checks[claim]:
            reasons.append(f"{claim} claim is {claim_status}.")
            gaps.append(f"{claim}.{claim_status}")

    temporal_status = _status(matrix, CausalClaim.TEMPORAL.value)
    checks["temporal"] = temporal_status != CausalClaimStatus.CONFLICTED.value
    if strict and temporal_status != CausalClaimStatus.SUPPORTED.value:
        checks["temporal"] = False
    if not checks["temporal"]:
        reasons.append(f"temporal claim is {temporal_status}.")
        gaps.append(f"temporal.{temporal_status}")

    contradiction_status = _status(matrix, CausalClaim.CONTRADICTION.value)
    checks["contradiction_free"] = contradiction_status != CausalClaimStatus.CONFLICTED.value
    if not checks["contradiction_free"]:
        reasons.append("a cited or invalid Evidence contradiction remains unresolved.")
        gaps.append("contradiction.conflicted")

    # Control/negative Evidence is informative and intentionally not a hard
    # requirement.  Keep it visible for the report and audit payload.
    checks["control_informational"] = _status(matrix, CausalClaim.CONTROL.value) != "conflicted"

    # Alternative comparison is diagnostic.  It is not a hard gate because the
    # user explicitly chose not to require exclusionary proof for confirmation.
    checks["no_equal_alternative"] = True
    if alternative_matrices:
        current_score = sum(
            result.status == CausalClaimStatus.SUPPORTED.value
            for result in matrix.claims.values()
        )
        if any(
            sum(
                result.status == CausalClaimStatus.SUPPORTED.value
                for result in other.claims.values()
            )
            == current_score
            for other in alternative_matrices
            if other is not matrix
        ):
            checks["no_equal_alternative"] = False

    hard_checks = [
        checks["parameter"],
        checks["outcome"],
        checks["mechanism"],
        checks["scope"],
        checks["temporal"],
        checks["contradiction_free"],
        *[
            checks[claim]
            for claim in ("equipment", "chamber", "operation")
            if checks[claim] is False
        ],
    ]
    if all(hard_checks):
        status = CONCLUSION_SUPPORTED
    elif any(
        _status(matrix, claim) == CausalClaimStatus.UNAVAILABLE.value
        for claim in required_claims
    ):
        status = CONCLUSION_INSUFFICIENT_EVIDENCE
    else:
        status = CONCLUSION_INCONCLUSIVE
    return ConfirmationGateResult(
        status=status,
        checks=checks,
        reasons=tuple(dict.fromkeys(reasons)),
        unresolved_gaps=tuple(dict.fromkeys(gaps)),
    )


def _lot_ids(item: Evidence) -> set[str]:
    return {
        entity.entity_id
        for entity in item.entities
        if entity.entity_type == EntityType.LOT.value
    }


def _entity_ids(item: Evidence, entity_type: str) -> set[str]:
    return {
        entity.entity_id
        for entity in item.entities
        if entity.entity_type == entity_type
    }


def _candidate_entity_tokens(candidate: CausalHypothesis, entity_type: str) -> set[str]:
    # Structured IDs are intentionally conservative; arbitrary prose is not
    # converted into an equipment or chamber claim here.
    prefixes = {
        EntityType.EQUIPMENT.value: ("EQ_", "CMP_"),
        EntityType.CHAMBER.value: ("CH_",),
        EntityType.OPERATION.value: ("OP_",),
        EntityType.RECIPE.value: ("RCP_",),
    }
    text = f"{candidate.root_cause} {candidate.causal_explanation}".upper()
    return {
        token.rstrip(".,;:)")
        for token in text.replace("/", " ").split()
        if token.startswith(prefixes.get(entity_type, ()))
    }


def _compatible_with_candidate(
    item: Evidence,
    candidate: CausalHypothesis,
) -> bool:
    for entity_type in (
        EntityType.EQUIPMENT.value,
        EntityType.CHAMBER.value,
        EntityType.OPERATION.value,
        EntityType.RECIPE.value,
    ):
        expected = _candidate_entity_tokens(candidate, entity_type)
        actual = _entity_ids(item, entity_type)
        if expected and actual and not expected & actual:
            return False
    return True


def evaluate_impact_lot_gate(
    *,
    source_lot_id: str | None,
    candidate: CausalHypothesis | Mapping[str, Any],
    evidence: Iterable[Evidence],
    observed_impact_lots: Sequence[str],
) -> dict[str, Any]:
    """Evaluate each observed Lot using exposure, excursion, and outcome facts.

    This function is intentionally independent from ``RCAState.impact_lots``;
    callers can audit the inclusion/exclusion reasons before deciding whether a
    product surface should consume the gated set.
    """

    normalized = (
        candidate
        if isinstance(candidate, CausalHypothesis)
        else CausalHypothesis(
            root_cause=str(candidate.get("root_cause", "")).strip(),
            # Deterministic legacy candidates predate the compact causal
            # explanation field.  Their root-cause label is still sufficient
            # for the independent Impact Lot scope gate.
            causal_explanation=str(
                candidate.get("causal_explanation")
                or candidate.get("root_cause", "")
            ).strip(),
            supporting_evidence_ids=tuple(candidate.get("supporting_evidence_ids", [])),
            contradicting_evidence_ids=tuple(
                candidate.get("contradicting_evidence_ids", [])
            ),
        )
    )
    items = [item for item in evidence if item.is_typed]
    rows: list[dict[str, Any]] = []
    for raw_lot in observed_impact_lots:
        lot_id = str(raw_lot)
        if source_lot_id and lot_id == source_lot_id:
            rows.append(
                {
                    "lot_id": lot_id,
                    "included": False,
                    "included_reason": None,
                    "excluded_reason": "source_lot_is_not_an_impact_lot",
                    "supporting_evidence_ids": [],
                }
            )
            continue
        lot_items = [item for item in items if lot_id in _lot_ids(item)]
        exposure = [
            item
            for item in lot_items
            if item.evidence_type in _EXPOSURE_TYPES
            and _compatible_with_candidate(item, normalized)
        ]
        process = [
            item
            for item in lot_items
            if item.evidence_type in _PROCESS_TYPES
            and _compatible_with_candidate(item, normalized)
        ]
        outcomes = [item for item in lot_items if item.evidence_type in _OUTCOME_TYPES]
        supporting = [*exposure, *process, *outcomes]
        if exposure and process and outcomes:
            rows.append(
                {
                    "lot_id": lot_id,
                    "included": True,
                    "included_reason": (
                        "Lot has matching candidate exposure, process excursion, "
                        "and compatible outcome Evidence."
                    ),
                    "excluded_reason": None,
                    "supporting_evidence_ids": list(
                        dict.fromkeys(item.evidence_id for item in supporting)
                    ),
                }
            )
        else:
            missing = [
                label
                for label, values in (
                    ("exposure", exposure),
                    ("excursion", process),
                    ("outcome", outcomes),
                )
                if not values
            ]
            rows.append(
                {
                    "lot_id": lot_id,
                    "included": False,
                    "included_reason": None,
                    "excluded_reason": "missing " + ", ".join(missing) + " Evidence",
                    "supporting_evidence_ids": list(
                        dict.fromkeys(item.evidence_id for item in supporting)
                    ),
                }
            )
    return {
        "source_lot_id": source_lot_id,
        "candidate_root_cause": normalized.root_cause,
        "confirmed_impact_lots": [row["lot_id"] for row in rows if row["included"]],
        "rows": rows,
    }


__all__ = [
    "CONCLUSION_INCONCLUSIVE",
    "CONCLUSION_INSUFFICIENT_EVIDENCE",
    "CONCLUSION_SUPPORTED",
    "ConfirmationGateResult",
    "confirm_candidate",
    "evaluate_impact_lot_gate",
]
