"""Conservative review for QuestionUpdate claims attached to Qwen decisions."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

from yield_rca_core.investigation_models import (
    MAX_INITIAL_QUESTIONS,
    EvidenceGapStatus,
    InvestigationQuestion,
    InvestigationValidationError,
    PlannerDecision,
    PlannerDecisionOutcome,
    QuestionUpdate,
    QuestionUpdateDisposition,
    QuestionUpdateReasonCode,
    QuestionUpdateReview,
)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _review(
    decision: PlannerDecision,
    *,
    disposition: QuestionUpdateDisposition,
    reason_code: QuestionUpdateReasonCode,
    reason: str,
    update_index: int | None,
    question_id: str | None = None,
    claimed_status: str | None = None,
) -> QuestionUpdateReview:
    return QuestionUpdateReview(
        decision_id=decision.decision_id,
        disposition=disposition.value,
        reason_code=reason_code.value,
        reason=reason,
        update_index=update_index,
        question_id=question_id,
        claimed_status=claimed_status,
    )


def review_question_updates(
    decision: PlannerDecision,
    raw_updates: object,
    *,
    questions: list[InvestigationQuestion],
    available_evidence_ids: set[str],
) -> PlannerDecisionOutcome:
    """Accept supported terminal deltas and reject unsafe claims independently."""

    if not isinstance(raw_updates, list):
        return PlannerDecisionOutcome(
            decision=decision,
            question_update_reviews=[
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.MALFORMED_COLLECTION,
                    reason=(
                        "question_updates was rejected because it must be a JSON list; "
                        "the core Planner decision was not changed."
                    ),
                    update_index=None,
                )
            ],
            raw_question_update_count=1,
        )
    if len(raw_updates) > MAX_INITIAL_QUESTIONS:
        return PlannerDecisionOutcome(
            decision=decision,
            question_update_reviews=[
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.TOO_MANY_UPDATES,
                    reason=(
                        "The complete question_updates collection was rejected because "
                        f"it exceeded the {MAX_INITIAL_QUESTIONS}-item boundary."
                    ),
                    update_index=None,
                )
            ],
            raw_question_update_count=len(raw_updates),
        )

    question_ids = [
        item.get("question_id")
        for item in raw_updates
        if isinstance(item, dict) and isinstance(item.get("question_id"), str)
    ]
    duplicate_question_ids = {
        question_id
        for question_id, count in Counter(question_ids).items()
        if count > 1
    }
    existing_questions = {
        question.question_id: question for question in questions
    }
    new_question_ids = {
        question.question_id for question in decision.new_questions
    }
    target_question_ids = set(decision.target_question_ids)
    accepted_updates: list[QuestionUpdate] = []
    reviews: list[QuestionUpdateReview] = []

    for index, raw_update in enumerate(raw_updates):
        if not isinstance(raw_update, dict):
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.MALFORMED_UPDATE,
                    reason="QuestionUpdate was rejected because it must be a JSON object.",
                    update_index=index,
                )
            )
            continue
        question_id = (
            _optional_string(raw_update.get("question_id"))
        )
        claimed_status = (
            _optional_string(raw_update.get("status"))
        )
        if question_id in duplicate_question_ids:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.DUPLICATE_QUESTION,
                    reason=(
                        f"QuestionUpdate {question_id} was rejected because the same "
                        "question_id appeared more than once in this decision."
                    ),
                    update_index=index,
                    question_id=question_id,
                    claimed_status=claimed_status,
                )
            )
            continue
        if claimed_status is None:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.MALFORMED_UPDATE,
                    reason="QuestionUpdate was rejected because status is missing.",
                    update_index=index,
                    question_id=question_id,
                )
            )
            continue
        if claimed_status not in {
            EvidenceGapStatus.CLOSED.value,
            EvidenceGapStatus.UNAVAILABLE.value,
        }:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.NON_TERMINAL_STATUS,
                    reason=(
                        "QuestionUpdate was rejected because status must be closed or "
                        "unavailable; the Question remains open."
                    ),
                    update_index=index,
                    question_id=question_id,
                    claimed_status=claimed_status,
                )
            )
            continue
        try:
            update = QuestionUpdate.from_dict(raw_update)
        except InvestigationValidationError as exc:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.MALFORMED_UPDATE,
                    reason=f"QuestionUpdate was rejected: {exc}.",
                    update_index=index,
                    question_id=question_id,
                    claimed_status=claimed_status,
                )
            )
            continue
        if update.question_id in new_question_ids:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.NEW_QUESTION_CONFLICT,
                    reason=(
                        f"QuestionUpdate {update.question_id} was rejected because a "
                        "decision cannot create and terminally update the same Question."
                    ),
                    update_index=index,
                    question_id=update.question_id,
                    claimed_status=update.status,
                )
            )
            continue
        current = existing_questions.get(update.question_id)
        if current is None:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.UNKNOWN_QUESTION,
                    reason=(
                        f"QuestionUpdate {update.question_id} was rejected because it "
                        "does not reference a current investigation Question."
                    ),
                    update_index=index,
                    question_id=update.question_id,
                    claimed_status=update.status,
                )
            )
            continue
        if current.status != EvidenceGapStatus.OPEN.value:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.TERMINAL_QUESTION,
                    reason=(
                        f"QuestionUpdate {update.question_id} was rejected because the "
                        "Question is already terminal."
                    ),
                    update_index=index,
                    question_id=update.question_id,
                    claimed_status=update.status,
                )
            )
            continue
        if update.question_id in target_question_ids:
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.TARGET_OVERLAP,
                    reason=(
                        f"QuestionUpdate {update.question_id} was rejected because the "
                        "next action still targets that Question; it remains open."
                    ),
                    update_index=index,
                    question_id=update.question_id,
                    claimed_status=update.status,
                )
            )
            continue
        if (
            update.status == EvidenceGapStatus.CLOSED.value
            and not set(update.evidence_ids) <= available_evidence_ids
        ):
            reviews.append(
                _review(
                    decision,
                    disposition=QuestionUpdateDisposition.REJECTED,
                    reason_code=QuestionUpdateReasonCode.UNKNOWN_EVIDENCE,
                    reason=(
                        f"QuestionUpdate {update.question_id} was rejected because its "
                        "closed answer references Evidence that is not available."
                    ),
                    update_index=index,
                    question_id=update.question_id,
                    claimed_status=update.status,
                )
            )
            continue

        accepted_updates.append(update)
        reviews.append(
            _review(
                decision,
                disposition=QuestionUpdateDisposition.ACCEPTED,
                reason_code=QuestionUpdateReasonCode.ACCEPTED,
                reason=(
                    f"QuestionUpdate {update.question_id} is a supported terminal "
                    "delta and may be committed with the core decision."
                ),
                update_index=index,
                question_id=update.question_id,
                claimed_status=update.status,
            )
        )

    return PlannerDecisionOutcome(
        decision=replace(decision, question_updates=accepted_updates),
        question_update_reviews=reviews,
        raw_question_update_count=len(raw_updates),
    )


def review_qwen_planner_output(
    output: object,
    *,
    questions: list[InvestigationQuestion],
    available_evidence_ids: set[str],
) -> PlannerDecisionOutcome:
    """Strictly parse the core decision while reviewing update claims separately."""

    if not isinstance(output, dict):
        raise InvestigationValidationError("PlannerDecision must be a JSON object")
    core_payload: dict[str, Any] = dict(output)
    raw_updates = core_payload.get("question_updates", [])
    core_payload["question_updates"] = []
    decision = PlannerDecision.from_dict(
        core_payload,
        allow_legacy_question_updates=False,
    )
    return review_question_updates(
        decision,
        raw_updates,
        questions=questions,
        available_evidence_ids=available_evidence_ids,
    )


__all__ = ["review_question_updates", "review_qwen_planner_output"]
