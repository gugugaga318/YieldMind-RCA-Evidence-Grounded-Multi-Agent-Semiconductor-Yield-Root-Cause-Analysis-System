"""Deterministic Confirmation and Impact-Lot gates for RCA results."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from yield_rca_core.causal_chain import assess_causal_chain
from yield_rca_core.causal_evidence_matrix import CausalEvidenceMatrix
from yield_rca_core.causal_hypothesis import CausalClaim, CausalClaimStatus, CausalHypothesis
from yield_rca_core.causal_investigation_models import AlternativeSearchStatus
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
    data_missing_evidence_ids: tuple[str, ...] = ()
    causal_chain_completeness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "unresolved_gaps": list(self.unresolved_gaps),
            "data_missing_evidence_ids": list(self.data_missing_evidence_ids),
            "causal_chain_completeness": self.causal_chain_completeness,
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
    alternative_search_status: str | None = None,
    require_causal_chain: bool = True,
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
    chain = matrix.causal_chain or assess_causal_chain(
        matrix.claims,
        data_missing_evidence_ids=matrix.data_missing_evidence_ids,
    )
    checks["causal_chain"] = chain.status == "complete"
    if require_causal_chain and not checks["causal_chain"]:
        reasons.append(f"causal chain is {chain.status}.")
        gaps.append(f"causal_chain.{chain.status}")
    data_missing_ids = tuple(matrix.data_missing_evidence_ids)
    required_statuses = {
        claim: _status(matrix, claim)
        for claim in (
            CausalClaim.PARAMETER.value,
            CausalClaim.OUTCOME.value,
            CausalClaim.MECHANISM.value,
            CausalClaim.SCOPE.value,
        )
    }
    data_missing_relevant = bool(data_missing_ids) and (
        any(
            status
            in {
                CausalClaimStatus.UNAVAILABLE.value,
                CausalClaimStatus.INCOMPLETE.value,
            }
            for status in required_statuses.values()
        )
        or not checks["causal_chain"]
    )
    checks["data_available"] = not data_missing_relevant
    if data_missing_relevant:
        reasons.append(
            "One or more typed operational sources explicitly report unavailable data."
        )
        gaps.extend(f"data_missing.{evidence_id}" for evidence_id in data_missing_ids)

    exposure_types = {
        evidence_type
        for claim in (
            CausalClaim.EQUIPMENT.value,
            CausalClaim.CHAMBER.value,
            CausalClaim.OPERATION.value,
        )
        if claim in matrix.claims
        for evidence_type in matrix.claims[claim].facts.get("evidence_types", [])
        if isinstance(evidence_type, str)
    }
    checks["exposure"] = bool(exposure_types & _EXPOSURE_TYPES)
    if not checks["exposure"]:
        reasons.append("no cited typed Evidence establishes current-Lot exposure.")
        gaps.append("exposure.unavailable")

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
    if strict and alternative_search_status is not None:
        checks["no_equal_alternative"] = (
            alternative_search_status
            == AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value
        )
        if not checks["no_equal_alternative"]:
            reasons.append(
                "Adversarial alternative search is not complete; a single candidate "
                "cannot be treated as the only explanation."
            )
            gaps.append(f"alternative_search.{alternative_search_status}")
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
        checks["exposure"],
        checks["parameter"],
        checks["outcome"],
        checks["mechanism"],
        checks["scope"],
        checks["temporal"],
        checks["contradiction_free"],
        *([checks["causal_chain"]] if require_causal_chain else []),
        *(
            [checks["no_equal_alternative"]]
            if strict and alternative_search_status is not None
            else []
        ),
        *[
            checks[claim]
            for claim in ("equipment", "chamber", "operation")
            if checks[claim] is False
        ],
    ]
    if all(hard_checks):
        status = CONCLUSION_SUPPORTED
    elif data_missing_relevant:
        status = CONCLUSION_INSUFFICIENT_EVIDENCE
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
        data_missing_evidence_ids=data_missing_ids,
        causal_chain_completeness=chain.status,
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
    tokens = {
        token.rstrip(".,;:)")
        for token in text.replace("/", " ").split()
        if token.startswith(prefixes.get(entity_type, ()))
    }
    if entity_type == EntityType.CHAMBER.value:
        tokens.update(
            match.group(1)
            for match in re.finditer(
                r"\bCHAMBER\s*[:#_-]?\s*([A-Z0-9_]+)", text
            )
        )
        tokens.update(
            token
            for token in re.findall(r"\b[A-Z][A-Z0-9_]+\b", text)
            if re.search(r"_CH[A-Z0-9]+(?:_|$)", token)
        )
    if entity_type == EntityType.OPERATION.value:
        tokens.update(
            match.group(1)
            for match in re.finditer(
                r"\b(?:OPERATION(?:_NO)?|OP)\s*[:#_-]?\s*([A-Z0-9_]+)",
                text,
            )
        )
    return tokens


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
        matches = {
            (expected_id, actual_id)
            for expected_id in expected
            for actual_id in actual
            if _compact(expected_id) == _compact(actual_id)
            or (
                entity_type == EntityType.EQUIPMENT.value
                and _compact(expected_id).startswith(_compact(actual_id))
                and _compact(expected_id)[len(_compact(actual_id)) :].startswith("ch")
            )
            or (
                entity_type == EntityType.CHAMBER.value
                and _compact(expected_id).endswith(_compact(actual_id))
            )
        }
        if expected and actual and not matches:
            return False
    return True


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _metadata_values(item: Evidence, keys: set[str]) -> set[str]:
    values: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).casefold() in keys and isinstance(
                    child, str | int | float
                ):
                    values.add(str(child))
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(item.metadata)
    return values


def _typed_values(item: Evidence, entity_type: str) -> set[str]:
    values = _entity_ids(item, entity_type)
    metadata_keys = {
        EntityType.EQUIPMENT.value: {"equipment", "equipment_id"},
        EntityType.CHAMBER.value: {"chamber", "chamber_id"},
        EntityType.OPERATION.value: {
            "operation",
            "operation_id",
            "operation_no",
            "target_operation_no",
        },
        EntityType.RECIPE.value: {"recipe", "recipe_id"},
        EntityType.PARAMETER.value: {
            "parameter",
            "parameter_id",
            "parameter_name",
            "metric_name",
        },
    }
    values.update(_metadata_values(item, metadata_keys.get(entity_type, set())))
    if entity_type == EntityType.PARAMETER.value and item.source_field:
        values.add(item.source_field)
    return values


def _common_lane_values(
    exposure: Sequence[Evidence],
    process: Sequence[Evidence],
    entity_type: str,
) -> set[str]:
    exposure_values = {
        value for item in exposure for value in _typed_values(item, entity_type)
    }
    process_values = {
        value for item in process for value in _typed_values(item, entity_type)
    }
    if not exposure_values or not process_values:
        return set()
    return {
        left
        for left in exposure_values
        if any(_compact(left) == _compact(right) for right in process_values)
    }


def _candidate_matches_values(
    candidate: CausalHypothesis,
    values: set[str],
) -> bool:
    raw_text = f"{candidate.root_cause} {candidate.causal_explanation}"
    compact_text = _compact(raw_text)
    candidate_tokens = set(re.findall(r"[a-z0-9]+", raw_text.casefold()))
    return any(
        (
            len(_compact(value)) >= 6
            and _compact(value) in compact_text
        )
        or (
            bool(value_tokens := set(re.findall(r"[a-z0-9]+", value.casefold())))
            and value_tokens <= candidate_tokens
        )
        for value in values
    )


def _candidate_direction(candidate: CausalHypothesis) -> str | None:
    aliases = {
        "above": "high",
        "decrease": "low",
        "decreased": "low",
        "drop": "low",
        "high": "high",
        "higher": "high",
        "increase": "high",
        "increased": "high",
        "low": "low",
        "lower": "low",
        "reduced": "low",
    }
    for token in re.findall(
        r"[a-z]+",
        f"{candidate.root_cause} {candidate.causal_explanation}".casefold(),
    ):
        if token in aliases:
            return aliases[token]
    return None


def _evidence_directions(items: Sequence[Evidence]) -> set[str]:
    directions: set[str] = set()
    for item in items:
        raw = _metadata_values(
            item,
            {
                "direction",
                "parameter_direction",
                "same_side_direction",
                "trend_direction",
            },
        )
        for value in raw:
            lowered = value.casefold()
            if lowered in {"high", "higher", "above", "increase", "increased"}:
                directions.add("high")
            elif lowered in {"low", "lower", "below", "decrease", "decreased"}:
                directions.add("low")
        for key in ("delta", "delta_percent", "avg_delta_percent", "mean_z_score"):
            for value in _metadata_values(item, {key}):
                try:
                    numeric = float(value)
                except ValueError:
                    continue
                if numeric > 0:
                    directions.add("high")
                elif numeric < 0:
                    directions.add("low")
    return directions


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _evidence_windows(items: Sequence[Evidence]) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            normalized = {str(key).casefold(): child for key, child in value.items()}
            starts = [
                normalized[key]
                for key in (
                    "excursion_start",
                    "processing_start",
                    "target_window_start",
                    "window_start",
                    "start",
                )
                if key in normalized
            ]
            ends = [
                normalized[key]
                for key in (
                    "excursion_end",
                    "processing_end",
                    "target_window_end",
                    "window_end",
                    "end",
                )
                if key in normalized
            ]
            if starts and ends:
                start = _parse_time(starts[0])
                end = _parse_time(ends[0])
                if start is not None and end is not None and start <= end:
                    windows.append((start, end))
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for item in items:
        visit(item.metadata)
        for entity in item.entities:
            visit(entity.attributes)
    return list(dict.fromkeys(windows))


def _timestamp_inside_window(
    process: Sequence[Evidence],
    windows: Sequence[tuple[datetime, datetime]],
) -> bool:
    if not windows:
        return False
    timestamps = [
        parsed
        for item in process
        if item.timestamp
        if (parsed := _parse_time(item.timestamp)) is not None
    ]
    return bool(timestamps) and all(
        any(start <= timestamp <= end for start, end in windows)
        for timestamp in timestamps
    )


def _compatible_outcomes(
    outcomes: Sequence[Evidence],
    candidate: CausalHypothesis,
) -> list[Evidence]:
    compatible: list[Evidence] = []
    for item in outcomes:
        values = {
            *(_typed_values(item, EntityType.DEFECT.value)),
            *(_typed_values(item, EntityType.WAT_ITEM.value)),
        }
        if values and _candidate_matches_values(candidate, values):
            compatible.append(item)
    return compatible


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
        candidate_parameters = {
            value
            for item in process
            for value in _typed_values(item, EntityType.PARAMETER.value)
            if _candidate_matches_values(normalized, {value})
        }
        compatible_outcomes = _compatible_outcomes(outcomes, normalized)
        common_equipment = _common_lane_values(
            exposure, process, EntityType.EQUIPMENT.value
        )
        common_chamber = _common_lane_values(
            exposure, process, EntityType.CHAMBER.value
        )
        common_operation = _common_lane_values(
            exposure, process, EntityType.OPERATION.value
        )
        exposure_recipes = {
            value for item in exposure for value in _typed_values(item, EntityType.RECIPE.value)
        }
        process_recipes = {
            value for item in process for value in _typed_values(item, EntityType.RECIPE.value)
        }
        recipe_consistent = not (exposure_recipes or process_recipes) or bool(
            {_compact(value) for value in exposure_recipes}
            & {_compact(value) for value in process_recipes}
        )
        windows = _evidence_windows([*exposure, *process])
        time_consistent = _timestamp_inside_window(process, windows)
        candidate_direction = _candidate_direction(normalized)
        evidence_directions = _evidence_directions(process)
        direction_consistent = (
            candidate_direction is None
            or not evidence_directions
            or candidate_direction in evidence_directions
        )
        supporting = [*exposure, *process, *compatible_outcomes]
        checks = {
            "exposure": bool(exposure),
            "excursion": bool(process),
            "equipment": bool(common_equipment),
            "chamber": bool(common_chamber),
            "operation": bool(common_operation),
            "recipe": recipe_consistent,
            "parameter": bool(candidate_parameters),
            "parameter_direction": direction_consistent,
            "excursion_window": bool(windows),
            "temporal": time_consistent,
            "outcome": bool(compatible_outcomes),
        }
        if all(checks.values()):
            rows.append(
                {
                    "lot_id": lot_id,
                    "included": True,
                    "included_reason": (
                        "Lot has matching candidate exposure, process excursion, "
                        "equipment/chamber/operation, parameter, excursion window, "
                        "and compatible outcome Evidence."
                    ),
                    "excluded_reason": None,
                    "supporting_evidence_ids": list(
                        dict.fromkeys(item.evidence_id for item in supporting)
                    ),
                    "checks": checks,
                }
            )
        else:
            missing = [label for label, passed in checks.items() if not passed]
            rows.append(
                {
                    "lot_id": lot_id,
                    "included": False,
                    "included_reason": None,
                    "excluded_reason": "missing " + ", ".join(missing) + " Evidence",
                    "supporting_evidence_ids": list(
                        dict.fromkeys(item.evidence_id for item in supporting)
                    ),
                    "checks": checks,
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
