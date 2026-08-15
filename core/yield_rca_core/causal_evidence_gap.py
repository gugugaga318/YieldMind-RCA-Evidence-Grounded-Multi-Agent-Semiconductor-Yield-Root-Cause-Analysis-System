"""Deterministic conversion of Matrix claim gaps into legal investigation work."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from yield_rca_core.causal_evidence_matrix import CausalEvidenceMatrix
from yield_rca_core.causal_hypothesis import CausalClaim
from yield_rca_core.causal_investigation_models import (
    AlternativeSearchStatus,
    CandidateChallenge,
)
from yield_rca_core.question_capability import QUESTION_CAPABILITY_REGISTRY

_GAP_PRIORITY = {
    "data_missing": 0,
    "hypothesis_discrimination": 1,
    "contradiction": 2,
    "missing_support": 3,
}

_CLAIM_TO_QUESTION = {
    CausalClaim.EQUIPMENT.value: "impact_scope",
    CausalClaim.CHAMBER.value: "impact_scope",
    CausalClaim.OPERATION.value: "impact_scope",
    CausalClaim.PARAMETER.value: "spc_signal",
    CausalClaim.OUTCOME.value: "product_outcome",
    CausalClaim.MECHANISM.value: "process_mechanism",
    CausalClaim.TEMPORAL.value: "spc_signal",
    CausalClaim.SCOPE.value: "impact_scope",
    CausalClaim.CONTRADICTION.value: "process_mechanism",
}
_CLAIM_TO_GROUPS = {
    CausalClaim.EQUIPMENT.value: {"shared_exposure"},
    CausalClaim.CHAMBER.value: {"shared_exposure"},
    CausalClaim.OPERATION.value: {"shared_exposure"},
    CausalClaim.PARAMETER.value: {"process_anomaly"},
    CausalClaim.OUTCOME.value: {"product_signal"},
    CausalClaim.MECHANISM.value: {"process_anomaly", "historical_context"},
    CausalClaim.TEMPORAL.value: {"process_anomaly", "shared_exposure"},
    CausalClaim.SCOPE.value: {"shared_exposure", "impact_scope"},
    CausalClaim.CONTRADICTION.value: {"process_anomaly", "product_signal"},
}


def build_causal_evidence_gaps(
    matrices: Sequence[CausalEvidenceMatrix],
) -> list[dict[str, Any]]:
    """Return only gaps that Python can map to registered capabilities."""

    gaps: list[dict[str, Any]] = []
    for candidate_index, matrix in enumerate(matrices):
        for claim, result in matrix.claims.items():
            if claim == CausalClaim.CONTROL.value:
                # Controls are informative, not a mandatory investigation gap.
                continue
            if result.status == "supported":
                continue
            question_kind = _CLAIM_TO_QUESTION.get(claim, "process_mechanism")
            definition = QUESTION_CAPABILITY_REGISTRY[question_kind]
            expected_groups = _CLAIM_TO_GROUPS.get(claim, set())
            actions = sorted(
                action
                for action in definition.allowed_actions
                if not expected_groups
                or expected_groups
                & set(definition.contribution_for(action))
            )
            gaps.append(
                {
                    "gap_id": f"candidate_{candidate_index}.{claim}.{result.status}",
                    "gap_type": (
                        "data_missing"
                        if result.status == "unavailable"
                        else (
                            "contradiction"
                            if claim == CausalClaim.CONTRADICTION.value
                            else "missing_support"
                        )
                    ),
                    "priority": _GAP_PRIORITY[
                        (
                            "data_missing"
                            if result.status == "unavailable"
                            else (
                                "contradiction"
                                if claim == CausalClaim.CONTRADICTION.value
                                else "missing_support"
                            )
                        )
                    ],
                    "candidate_index": candidate_index,
                    "claim": claim,
                    "status": result.status,
                    "reason": result.reason,
                    "question_kind": question_kind,
                    "allowed_actions": actions,
                    "evidence_ids": list(result.evidence_ids),
                }
            )
    return sorted(
        gaps,
        key=lambda item: (
            int(item.get("priority", _GAP_PRIORITY["missing_support"])),
            int(item.get("candidate_index", 0)),
            str(item.get("claim", "")),
        ),
    )


def build_hypothesis_discrimination_gaps(
    matrices: Sequence[CausalEvidenceMatrix],
    *,
    alternative_search_status: str = AlternativeSearchStatus.NOT_SEARCHED.value,
    candidate_challenges: Sequence[CandidateChallenge] = (),
) -> list[dict[str, Any]]:
    """Create a deterministic gap until the candidate competition is closed.

    One candidate is deliberately enough to create this gap.  The absence of a
    second Qwen proposal is not proof that a second explanation does not exist.
    Qwen may select an existing gap, but it cannot create an Action or invent a
    free-form question from its challenge text.
    """

    if (
        not matrices
        or alternative_search_status
        == AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value
    ):
        return []
    challenge_gap_ids = {
        gap_id
        for challenge in candidate_challenges
        for gap_id in challenge.distinguishing_gap_ids
    }
    actions = sorted(
        QUESTION_CAPABILITY_REGISTRY["process_mechanism"].allowed_actions
    )
    gaps: list[dict[str, Any]] = []
    for candidate_index in range(len(matrices)):
        gap_id = f"candidate_{candidate_index}.hypothesis_discrimination"
        gaps.append(
            {
                "gap_id": gap_id,
                "gap_type": "hypothesis_discrimination",
                "priority": _GAP_PRIORITY["hypothesis_discrimination"],
                "candidate_index": candidate_index,
                "claim": "hypothesis_discrimination",
                "status": "unresolved",
                "reason": (
                    "The candidate has not completed an adversarial search that "
                    "distinguishes it from the strongest competing explanation."
                ),
                "question_kind": "process_mechanism",
                "allowed_actions": actions,
                "evidence_ids": [],
                "challenge_selected": gap_id in challenge_gap_ids,
            }
        )
    return gaps


__all__ = [
    "build_causal_evidence_gaps",
    "build_hypothesis_discrimination_gaps",
]
