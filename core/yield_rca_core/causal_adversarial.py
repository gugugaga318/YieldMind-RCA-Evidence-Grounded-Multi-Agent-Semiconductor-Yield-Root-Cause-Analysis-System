"""Python-bounded adversarial challenge for Qwen RCA candidates.

Qwen is allowed to identify the strongest alternative and explain which
Evidence would distinguish the candidates.  Python owns the candidate and
Evidence identifiers, validates the references, and derives the competition
status used by the Confirmation Gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _gap_information_gain_for_lane(
    gap: Mapping[str, Any],
    lane_id: str,
) -> float | None:
    raw_by_lane = gap.get("information_gain_by_lane")
    if isinstance(raw_by_lane, Mapping):
        raw_score = raw_by_lane.get(lane_id)
        if isinstance(raw_score, int | float):
            return float(raw_score)
        return None
    raw_score = gap.get("information_gain")
    if isinstance(raw_score, int | float):
        return float(raw_score)
    return None


def _normalize_challenge_payload(
    payload: object,
    *,
    candidate_ids: set[str],
    lane_ids: set[str],
    evidence_ids: set[str],
    gap_ids: set[str],
    gap_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    evidence_by_id: Mapping[str, Evidence] | None = None,
    lane_contexts: Mapping[str, Mapping[str, Any]] | None = None,
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
    if gap_by_id is not None:
        for gap_id in gaps:
            gap = gap_by_id.get(gap_id)
            if gap is None:
                continue
            gap_candidate_id = str(gap.get("candidate_id", "")).strip()
            if gap_candidate_id and gap_candidate_id != candidate_id:
                raise LLMOutputValidationError(
                    "candidate challenge selected an Evidence Gap owned by another "
                    "candidate"
                )
            raw_scope = gap.get("target_scope", {})
            target_lane_id = (
                str(raw_scope.get("lane_id", "")).strip()
                if isinstance(raw_scope, Mapping)
                else ""
            )
            lane_binding = str(gap.get("lane_binding", "")).strip()
            raw_applicable_lanes = gap.get("applicable_lane_ids", [])
            applicable_lanes = (
                {
                    str(item).strip()
                    for item in raw_applicable_lanes
                    if str(item).strip()
                }
                if isinstance(raw_applicable_lanes, list | tuple)
                else set()
            )
            template_binding = (
                lane_binding == "challenge_selected" and not target_lane_id
            )
            if (
                alternative_id is not None
                and target_lane_id != alternative_id
                and not template_binding
            ):
                raise LLMOutputValidationError(
                    "candidate challenge selected a discriminator Gap for a "
                    "different causal Lane"
                )
            if (
                alternative_id is not None
                and applicable_lanes
                and alternative_id not in applicable_lanes
            ):
                raise LLMOutputValidationError(
                    "candidate challenge selected a discriminator Gap that cannot "
                    "produce an independent observation for the strongest "
                    "alternative Lane"
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
    if status == ChallengeStatus.RESOLVED.value and precursor:
        raise LLMOutputValidationError(
            "resolved challenge cannot retain unexplained precursor Evidence"
        )
    if alternative_id is not None and status != ChallengeStatus.RESOLVED.value:
        if len(gaps) != 1:
            raise LLMOutputValidationError(
                "an unresolved named alternative Lane requires exactly one "
                "highest-information-gain Python-generated typed discriminator Gap"
            )
        if gap_by_id is not None:
            selected_gap_type = str(
                gap_by_id.get(gaps[0], {}).get("gap_type", "")
            ).strip()
            if selected_gap_type != "hypothesis_discrimination":
                raise LLMOutputValidationError(
                    "an unresolved named alternative Lane must select a "
                    "hypothesis_discrimination typed Gap"
                )
            scored_gaps = {
                gap_id: score
                for gap_id, gap in gap_by_id.items()
                if str(gap.get("gap_type", "")) == "hypothesis_discrimination"
                and str(gap.get("candidate_id", "")).strip() in {"", candidate_id}
                and (
                    score := _gap_information_gain_for_lane(gap, alternative_id)
                )
                is not None
            }
            selected_score = scored_gaps.get(gaps[0])
            if scored_gaps and selected_score is not None:
                highest_score = max(scored_gaps.values())
                if selected_score < highest_score:
                    highest_gap_ids = sorted(
                        gap_id
                        for gap_id, score in scored_gaps.items()
                        if score == highest_score
                    )
                    raise LLMOutputValidationError(
                        "selected discriminator is not the highest-information-gain "
                        f"observation for the strongest alternative Lane; choose one "
                        f"of {highest_gap_ids}"
                    )
    if alternative_id is not None and lane_contexts:
        lane = lane_contexts.get(alternative_id)
        if lane is None:
            raise LLMOutputValidationError(
                "strongest alternative Lane has no Python-owned context"
            )
        if gap_by_id is not None:
            for gap_id in gaps:
                gap = gap_by_id.get(gap_id, {})
                kind = str(gap.get("discriminator_kind", ""))
                if kind == "parameter_anomaly" and not lane.get("parameter_scope"):
                    raise LLMOutputValidationError(
                        "parameter_anomaly discriminator is not applicable to the "
                        "selected causal Lane"
                    )
                if kind == "recipe_commonality" and not lane.get("recipe"):
                    raise LLMOutputValidationError(
                        "recipe_commonality discriminator is not applicable to the "
                        "selected causal Lane"
                    )
                if kind == "product_outcome" and not lane.get("exposed_lot_ids"):
                    raise LLMOutputValidationError(
                        "product_outcome discriminator is not applicable to the "
                        "selected causal Lane"
                    )
                raw_window = lane.get("time_window", [])
                if kind == "temporal_alignment" and not (
                    isinstance(raw_window, list | tuple) and len(raw_window) == 2
                ):
                    raise LLMOutputValidationError(
                        "temporal_alignment discriminator is not applicable to the "
                        "selected causal Lane"
                    )
        lane_evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in referenced_evidence
            if evidence_by_id is not None and evidence_id in evidence_by_id
        ]
        if not any(_evidence_matches_lane(item, lane) for item in lane_evidence):
            raise LLMOutputValidationError(
                "challenge does not cite Entity/time-consistent Evidence for the "
                "strongest alternative Lane"
            )
        if status == ChallengeStatus.RESOLVED.value and not (supporting or contradicting):
            raise LLMOutputValidationError(
                "resolved alternative challenge requires distinguishing Evidence"
            )
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


def _normalized_entity(value: object) -> str:
    normalized = str(value or "").strip().upper()
    for prefix in ("OP_", "EQ_", "CH_", "RCP_"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _evidence_matches_lane(
    evidence: Evidence,
    lane: Mapping[str, Any],
) -> bool:
    """Validate a challenge citation against Python-owned Lane facts."""

    lane_id = str(lane.get("lane_id", "")).strip()
    evidence_lane_id = str(evidence.metadata.get("lane_id", "")).strip()
    if evidence_lane_id and evidence_lane_id != lane_id:
        return False

    entity_types = {
        "operation": "operation",
        "equipment": "equipment",
        "chamber": "chamber",
        "recipe": "recipe",
    }
    matched_fact = bool(evidence_lane_id and evidence_lane_id == lane_id)
    for lane_field, entity_type in entity_types.items():
        expected = _normalized_entity(lane.get(lane_field))
        if not expected:
            continue
        observed = {
            _normalized_entity(entity.entity_id)
            for entity in evidence.entities
            if entity.entity_type == entity_type
        }
        if observed and expected not in observed:
            return False
        matched_fact = matched_fact or expected in observed

    raw_window = lane.get("time_window", [])
    if (
        evidence.timestamp
        and isinstance(raw_window, (list, tuple))
        and len(raw_window) == 2
    ):
        observed_at = _parse_time(evidence.timestamp)
        start = _parse_time(raw_window[0])
        end = _parse_time(raw_window[1])
        if observed_at is not None and start is not None and end is not None:
            if not start <= observed_at <= end:
                return False
            matched_fact = True
    return matched_fact


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
    if any(challenge.unexplained_precursor_evidence_ids for challenge in challenges):
        return AlternativeSearchStatus.UNRESOLVED.value

    unresolved_statuses = {
        ChallengeStatus.OPEN.value,
        ChallengeStatus.UNRESOLVED.value,
        ChallengeStatus.ALTERNATIVE_IDENTIFIED.value,
    }
    if any(challenge.status in unresolved_statuses for challenge in challenges):
        if any(challenge.strongest_alternative_lane_id for challenge in challenges):
            return AlternativeSearchStatus.ALTERNATIVE_FOUND.value
        return AlternativeSearchStatus.UNRESOLVED.value

    resolved_alternative_ids = {
        challenge.strongest_alternative_lane_id
        for challenge in challenges
        if challenge.status == ChallengeStatus.RESOLVED.value
        and challenge.strongest_alternative_lane_id is not None
    }
    if resolved_alternative_ids:
        return AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value

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
        lane_contexts: Sequence[Mapping[str, Any]] = (),
    ) -> AdversarialChallengeGeneration:
        if not candidates or len(candidates) != len(matrices):
            return AdversarialChallengeGeneration(challenges=(), attempt_count=0)
        ids = _candidate_ids(candidates)
        gap_by_id = {
            str(item.get("gap_id")): item
            for item in evidence_gaps
            if str(item.get("gap_id", "")).strip()
        }
        gap_id_set = set(gap_by_id)
        lane_context_by_id = {
            str(item.get("lane_id", "")).strip(): item
            for item in lane_contexts
            if str(item.get("lane_id", "")).strip()
        }
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
                    "causal_lanes": [dict(item) for item in lane_contexts],
                    "output_attempt": attempt,
                    "previous_validation_feedback": (
                        {
                            "category": "challenge_output_validation_error",
                            "message": validation_errors[-1],
                            "must_repair_before_resubmission": True,
                            "unresolved_named_alternative_gap_rule": (
                                "Select exactly one highest-information-gain "
                                "hypothesis_discrimination Gap for each unresolved "
                                "named alternative Lane."
                            ),
                            "allowed_gap_ids": sorted(gap_id_set),
                        }
                        if validation_errors
                        else None
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
                                gap_by_id=gap_by_id,
                                evidence_by_id=evidence_by_id,
                                lane_contexts=lane_context_by_id,
                            )
                        )
                    except (LLMOutputValidationError, TypeError, ValueError) as exc:
                        candidate_errors.append(
                            str(exc).strip() or f"challenges[{index}] is invalid"
                        )
                if candidate_errors and (
                    not parsed or attempt < _OUTPUT_ATTEMPTS
                ):
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
