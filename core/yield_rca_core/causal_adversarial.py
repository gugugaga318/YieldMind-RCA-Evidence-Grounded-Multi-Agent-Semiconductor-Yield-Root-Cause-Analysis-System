"""Python-bounded adversarial challenge for Qwen RCA candidates.

Qwen is allowed to identify the strongest alternative and explain which
Evidence would distinguish the candidates.  Python owns the candidate and
Evidence identifiers, validates the references, and derives the competition
status used by the Confirmation Gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from yield_rca_core.causal_evidence_matrix import CausalEvidenceMatrix
from yield_rca_core.causal_investigation_models import (
    AlternativeSearchStatus,
    CandidateChallenge,
    ChallengeStatus,
)
from yield_rca_core.evidence_models import Evidence
from yield_rca_core.llm_gateway import (
    LLMCallError,
    LLMClient,
    LLMOutputValidationError,
    LLMRequest,
)
from yield_rca_core.models import AgentKind, ModelValidationError

_OUTPUT_ATTEMPTS = 2


@dataclass(frozen=True)
class AdversarialChallengeGeneration:
    """Result of one bounded Qwen challenge request."""

    challenges: tuple[CandidateChallenge, ...]
    attempt_count: int
    validation_errors: tuple[str, ...] = ()
    output_invalid: bool = False
    alternative_search_status: str = AlternativeSearchStatus.NOT_SEARCHED.value


def _string_list(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise LLMOutputValidationError(f"{field_name} must be an array")
    values = tuple(str(item).strip() for item in value)
    if any(not item for item in values):
        raise LLMOutputValidationError(f"{field_name} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise LLMOutputValidationError(f"{field_name} must not contain duplicates")
    return values


def _normalize_challenge_payload(
    payload: object,
    *,
    candidate_ids: set[str],
    lane_ids: set[str],
    evidence_ids: set[str],
    gap_ids: set[str],
    evidence_by_id: Mapping[str, Evidence] | None = None,
) -> CandidateChallenge:
    if not isinstance(payload, Mapping):
        raise LLMOutputValidationError("candidate challenge must be an object")
    candidate_id = str(payload.get("candidate_id", "")).strip()
    if not candidate_id or candidate_id not in candidate_ids:
        raise LLMOutputValidationError(
            f"candidate challenge references unknown candidate_id: {candidate_id!r}"
        )

    alternative = payload.get("strongest_alternative_lane_id")
    if alternative is None:
        # The public design wording calls this field strongest_alternative;
        # accept the alias but normalize it into the Python-owned state model.
        alternative = payload.get("strongest_alternative")
    alternative_id = (
        str(alternative).strip() if alternative is not None else None
    )
    if alternative_id == "":
        alternative_id = None
    if alternative_id is not None and alternative_id not in lane_ids:
        raise LLMOutputValidationError(
            "candidate challenge references an unknown strongest alternative Lane"
        )

    supporting = _string_list(
        payload.get("supporting_evidence_ids", []),
        "supporting_evidence_ids",
    )
    contradicting = _string_list(
        payload.get("contradicting_evidence_ids", []),
        "contradicting_evidence_ids",
    )
    precursor = _string_list(
        payload.get("unexplained_precursor_evidence_ids", []),
        "unexplained_precursor_evidence_ids",
    )
    referenced_evidence = set(supporting + contradicting + precursor)
    unknown_evidence = sorted(referenced_evidence - evidence_ids)
    if unknown_evidence:
        raise LLMOutputValidationError(
            f"candidate challenge references unknown Evidence IDs: {unknown_evidence}"
        )
    if set(supporting) & set(contradicting):
        raise LLMOutputValidationError(
            "candidate challenge Evidence cannot be both supporting and contradicting"
        )
    if evidence_by_id is not None:
        for evidence_id in referenced_evidence:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            lane_value = evidence.metadata.get("lane_id")
            if lane_value is not None and str(lane_value).strip() not in lane_ids:
                raise LLMOutputValidationError(
                    "candidate challenge Evidence references an unknown causal Lane"
                )

    gap_values = payload.get("distinguishing_gap_ids", [])
    gaps = _string_list(gap_values, "distinguishing_gap_ids")
    unknown_gaps = sorted(set(gaps) - gap_ids)
    if unknown_gaps:
        raise LLMOutputValidationError(
            f"candidate challenge references unknown Evidence Gaps: {unknown_gaps}"
        )
    questions = _string_list(
        payload.get("distinguishing_questions", []),
        "distinguishing_questions",
    )

    explanation = payload.get("challenge_explanation")
    if explanation is None:
        explanation = payload.get("explanation", "")
    if not isinstance(explanation, str) or not explanation.strip():
        raise LLMOutputValidationError("challenge_explanation must be non-empty")

    raw_status = str(payload.get("status", ChallengeStatus.OPEN.value)).strip()
    try:
        status = ChallengeStatus(raw_status).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ChallengeStatus)
        raise LLMOutputValidationError(
            f"candidate challenge status must be one of: {allowed}"
        ) from exc
    return CandidateChallenge(
        candidate_id=candidate_id,
        strongest_alternative_lane_id=alternative_id,
        strongest_alternative=alternative_id,
        supporting_evidence_ids=supporting,
        contradicting_evidence_ids=contradicting,
        unexplained_precursor_evidence_ids=precursor,
        distinguishing_gap_ids=gaps,
        distinguishing_questions=questions,
        challenge_explanation=explanation.strip(),
        status=status,
    )


def _candidate_ids(candidates: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    result: list[str] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(
            candidate.get("candidate_id")
            or candidate.get("hypothesis_id")
            or f"candidate_{index}"
        ).strip()
        if candidate_id and candidate_id not in result:
            result.append(candidate_id)
    return tuple(result)


def derive_alternative_search_status(
    *,
    challenges: Sequence[CandidateChallenge],
    matrices: Sequence[CausalEvidenceMatrix],
    active_lane_ids: Sequence[str] = (),
    eliminated_lane_ids: Sequence[str] = (),
) -> str:
    """Derive competition status without trusting a Qwen conclusion field.

    A single candidate never implies that alternatives do not exist.  The only
    status that permits a strict Confirmation Gate to treat alternatives as
    eliminated is ``alternatives_eliminated``.  A named alternative or an
    unresolved/blocked challenge remains a live competition.
    """

    if not challenges:
        return AlternativeSearchStatus.NOT_SEARCHED.value
    if any(challenge.status == ChallengeStatus.BLOCKED.value for challenge in challenges):
        return AlternativeSearchStatus.BLOCKED_BY_MISSING_DATA.value
    if any(challenge.strongest_alternative_lane_id for challenge in challenges):
        return AlternativeSearchStatus.ALTERNATIVE_FOUND.value

    unresolved_statuses = {
        ChallengeStatus.OPEN.value,
        ChallengeStatus.UNRESOLVED.value,
        ChallengeStatus.ALTERNATIVE_IDENTIFIED.value,
    }
    if any(challenge.status in unresolved_statuses for challenge in challenges):
        return AlternativeSearchStatus.UNRESOLVED.value

    # With multiple active Lanes, an empty alternative claim is not enough:
    # every other Lane must have been explicitly eliminated by Python-owned
    # state.  For multiple candidates, a fully conflicted non-winner is also a
    # valid deterministic elimination signal.
    active = set(active_lane_ids)
    eliminated = set(eliminated_lane_ids)
    if active and not active <= eliminated:
        if len(matrices) <= 1:
            return AlternativeSearchStatus.UNRESOLVED.value
        if not all(matrix.has_critical_conflict for matrix in matrices[1:]):
            return AlternativeSearchStatus.UNRESOLVED.value
    return AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value


@dataclass(frozen=True)
class QwenAdversarialChallenger:
    """Ask Qwen for bounded challenge claims; Python owns the result status."""

    llm_client: LLMClient
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if self.llm_client is None:
            raise ModelValidationError("adversarial challenger requires an LLM client")

    def generate(
        self,
        *,
        request_id: str,
        candidates: Sequence[Mapping[str, Any]],
        matrices: Sequence[CausalEvidenceMatrix],
        evidence_gaps: Sequence[Mapping[str, Any]],
        evidence_ids: Sequence[str],
        evidence_by_id: Mapping[str, Evidence] | None = None,
        lane_ids: Sequence[str] = (),
        active_lane_ids: Sequence[str] = (),
        eliminated_lane_ids: Sequence[str] = (),
    ) -> AdversarialChallengeGeneration:
        if not candidates or len(candidates) != len(matrices):
            return AdversarialChallengeGeneration(challenges=(), attempt_count=0)
        ids = _candidate_ids(candidates)
        gap_id_set = {str(item.get("gap_id")) for item in evidence_gaps}
        payload_candidates = []
        for index, (candidate, matrix) in enumerate(zip(candidates, matrices, strict=True)):
            payload_candidates.append(
                {
                    "candidate_id": ids[index],
                    "root_cause": str(candidate.get("root_cause", "")),
                    "causal_explanation": str(candidate.get("causal_explanation", "")),
                    "causal_evidence_matrix": matrix.to_dict(),
                }
            )
        validation_errors: list[str] = []
        for attempt in range(1, _OUTPUT_ATTEMPTS + 1):
            request = LLMRequest(
                agent=AgentKind.RCA_REASONING.value,
                prompt_name="causal_adversarial_challenge",
                prompt_version=self.prompt_version,
                payload={
                    "request_id": request_id,
                    "candidates": payload_candidates,
                    "evidence_gaps": [dict(item) for item in evidence_gaps],
                    "available_evidence_ids": sorted(set(evidence_ids)),
                    "causal_lane_ids": list(dict.fromkeys(lane_ids)),
                    "active_lane_ids": list(dict.fromkeys(active_lane_ids)),
                    "eliminated_lane_ids": list(dict.fromkeys(eliminated_lane_ids)),
                    "output_attempt": attempt,
                    "previous_validation_feedback": (
                        validation_errors[-1] if validation_errors else None
                    ),
                    "deterministic_challenges": [],
                },
                temperature=0.0,
            )
            try:
                response = self.llm_client.complete_json(request)
                data = response.data
                if set(data) != {"challenges", "analysis_summary"}:
                    raise LLMOutputValidationError(
                        "adversarial challenge output must contain exactly challenges "
                        "and analysis_summary"
                    )
                raw_challenges = data.get("challenges")
                summary = data.get("analysis_summary")
                if not isinstance(raw_challenges, list):
                    raise LLMOutputValidationError("challenges must be an array")
                if not isinstance(summary, str) or not summary.strip():
                    raise LLMOutputValidationError("analysis_summary must be non-empty")
                parsed: list[CandidateChallenge] = []
                candidate_errors: list[str] = []
                for index, raw in enumerate(raw_challenges):
                    try:
                        parsed.append(
                            _normalize_challenge_payload(
                                raw,
                                candidate_ids=set(ids),
                                lane_ids=set(lane_ids),
                                evidence_ids=set(evidence_ids),
                                gap_ids=gap_id_set,
                                evidence_by_id=evidence_by_id,
                            )
                        )
                    except (LLMOutputValidationError, TypeError, ValueError) as exc:
                        candidate_errors.append(
                            str(exc).strip() or f"challenges[{index}] is invalid"
                        )
                if candidate_errors and not parsed:
                    raise LLMOutputValidationError("; ".join(candidate_errors))
                validation_errors.extend(candidate_errors)
                status = derive_alternative_search_status(
                    challenges=parsed,
                    matrices=matrices,
                    active_lane_ids=active_lane_ids,
                    eliminated_lane_ids=eliminated_lane_ids,
                )
                return AdversarialChallengeGeneration(
                    challenges=tuple(parsed),
                    attempt_count=attempt,
                    validation_errors=tuple(validation_errors),
                    alternative_search_status=status,
                )
            except LLMCallError as exc:
                return AdversarialChallengeGeneration(
                    challenges=(),
                    attempt_count=attempt,
                    validation_errors=(str(exc).strip() or type(exc).__name__,),
                    alternative_search_status=AlternativeSearchStatus.UNRESOLVED.value,
                )
            except LLMOutputValidationError as exc:
                validation_errors.append(str(exc).strip() or type(exc).__name__)
                continue
        return AdversarialChallengeGeneration(
            challenges=(),
            attempt_count=_OUTPUT_ATTEMPTS,
            validation_errors=tuple(validation_errors),
            output_invalid=True,
            alternative_search_status=AlternativeSearchStatus.UNRESOLVED.value,
        )


__all__ = [
    "AdversarialChallengeGeneration",
    "QwenAdversarialChallenger",
    "derive_alternative_search_status",
]
