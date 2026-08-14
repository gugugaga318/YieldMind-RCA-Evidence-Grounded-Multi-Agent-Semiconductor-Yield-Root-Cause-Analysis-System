"""Deterministic conversion of Matrix claim gaps into legal investigation work."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from yield_rca_core.causal_evidence_matrix import CausalEvidenceMatrix
from yield_rca_core.causal_hypothesis import CausalClaim
from yield_rca_core.question_capability import QUESTION_CAPABILITY_REGISTRY

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
                    "candidate_index": candidate_index,
                    "claim": claim,
                    "status": result.status,
                    "reason": result.reason,
                    "question_kind": question_kind,
                    "allowed_actions": actions,
                    "evidence_ids": list(result.evidence_ids),
                }
            )
    return gaps


__all__ = ["build_causal_evidence_gaps"]
