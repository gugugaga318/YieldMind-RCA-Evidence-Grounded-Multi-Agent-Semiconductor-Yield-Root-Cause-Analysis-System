from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ConclusionLevel,
    DecisionType,
    EvidenceGapStatus,
    GoalStatus,
    InvestigationAction,
    InvestigationQuestion,
    PlannerDecision,
    QuestionUpdateDisposition,
    QuestionUpdateReasonCode,
)
from yield_rca_core.question_update_review import (  # noqa: E402
    review_qwen_planner_output,
)

GOAL_ID = "GOAL_REVIEW"
TARGET_ID = "Q_TARGET"
CLOSE_ID = "Q_CLOSE"


def question(
    question_id: str,
    *,
    status: str = EvidenceGapStatus.OPEN.value,
) -> InvestigationQuestion:
    terminal: dict[str, Any] = {}
    if status == EvidenceGapStatus.CLOSED.value:
        terminal = {
            "answer": "Existing evidence already answered this Question.",
            "evidence_ids": ["EV_EXISTING"],
        }
    return InvestigationQuestion(
        question_id=question_id,
        goal_id=GOAL_ID,
        question=f"What does {question_id} show?",
        rationale="Keep the bounded investigation auditable.",
        scope={"lot_id": "LOT_01"},
        status=status,
        **terminal,
    )


def act_payload() -> dict[str, Any]:
    return PlannerDecision(
        decision_id="DECISION_REVIEW",
        goal_id=GOAL_ID,
        decision_type=DecisionType.ACT.value,
        reason="Continue the bounded investigation.",
        goal_status=GoalStatus.IN_PROGRESS.value,
        proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
        next_action=InvestigationAction(
            action_id="ACTION_REVIEW",
            kind=ActionKind.FIND_SHARED_EXPOSURE.value,
            agent="mes",
            reason="Find the shared process exposure.",
            inputs={"lot_id": "LOT_01"},
            scope={"lot_id": "LOT_01"},
        ),
        target_question_ids=[TARGET_ID],
    ).to_dict()


def closed_update(
    question_id: str = CLOSE_ID,
    *,
    evidence_id: str = "EV_CLOSE",
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "status": EvidenceGapStatus.CLOSED.value,
        "answer": "The available Evidence answers this Question.",
        "evidence_ids": [evidence_id],
        "unavailable_reason": None,
    }


def unavailable_update(question_id: str) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "status": EvidenceGapStatus.UNAVAILABLE.value,
        "answer": None,
        "evidence_ids": [],
        "unavailable_reason": "The bounded source has no usable record.",
    }


def test_mixed_updates_accept_supported_delta_and_reject_open_claim() -> None:
    payload = act_payload()
    payload["question_updates"] = [
        closed_update(),
        {
            "question_id": TARGET_ID,
            "status": EvidenceGapStatus.OPEN.value,
            "answer": "Partial progress is not a terminal update.",
            "evidence_ids": ["EV_CLOSE"],
            "unavailable_reason": None,
        },
    ]

    outcome = review_qwen_planner_output(
        payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids={"EV_CLOSE"},
    )

    assert outcome.decision.next_action is not None
    assert outcome.decision.next_action.action_id == "ACTION_REVIEW"
    assert [update.question_id for update in outcome.decision.question_updates] == [
        CLOSE_ID
    ]
    assert [review.disposition for review in outcome.question_update_reviews] == [
        QuestionUpdateDisposition.ACCEPTED.value,
        QuestionUpdateDisposition.REJECTED.value,
    ]
    assert outcome.question_update_reviews[1].reason_code == (
        QuestionUpdateReasonCode.NON_TERMINAL_STATUS.value
    )


def test_unsafe_update_claims_are_rejected_without_changing_core_action() -> None:
    cases = [
        (
            "target_overlap",
            closed_update(TARGET_ID),
            [question(TARGET_ID), question(CLOSE_ID)],
            {"EV_CLOSE"},
            QuestionUpdateReasonCode.TARGET_OVERLAP.value,
        ),
        (
            "unknown_question",
            unavailable_update("Q_UNKNOWN"),
            [question(TARGET_ID), question(CLOSE_ID)],
            set(),
            QuestionUpdateReasonCode.UNKNOWN_QUESTION.value,
        ),
        (
            "terminal_question",
            unavailable_update(CLOSE_ID),
            [question(TARGET_ID), question(CLOSE_ID, status="closed")],
            set(),
            QuestionUpdateReasonCode.TERMINAL_QUESTION.value,
        ),
        (
            "unknown_evidence",
            closed_update(evidence_id="EV_UNKNOWN"),
            [question(TARGET_ID), question(CLOSE_ID)],
            set(),
            QuestionUpdateReasonCode.UNKNOWN_EVIDENCE.value,
        ),
    ]

    for _name, raw_update, questions, evidence_ids, expected_reason in cases:
        payload = act_payload()
        payload["question_updates"] = [raw_update]

        outcome = review_qwen_planner_output(
            payload,
            questions=questions,
            available_evidence_ids=evidence_ids,
        )

        assert outcome.decision.next_action is not None
        assert outcome.decision.next_action.action_id == "ACTION_REVIEW"
        assert outcome.decision.question_updates == []
        assert outcome.question_update_reviews[0].reason_code == expected_reason


def test_duplicate_and_oversized_collections_are_rejected_conservatively() -> None:
    duplicate_payload = act_payload()
    duplicate_payload["question_updates"] = [
        unavailable_update(CLOSE_ID),
        unavailable_update(CLOSE_ID),
    ]
    duplicate_outcome = review_qwen_planner_output(
        duplicate_payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids=set(),
    )

    assert duplicate_outcome.decision.question_updates == []
    assert len(duplicate_outcome.question_update_reviews) == 2
    assert all(
        review.reason_code == QuestionUpdateReasonCode.DUPLICATE_QUESTION.value
        for review in duplicate_outcome.question_update_reviews
    )

    oversized_payload = act_payload()
    oversized_payload["question_updates"] = [
        unavailable_update(f"Q_EXTRA_{index}") for index in range(6)
    ]
    oversized_outcome = review_qwen_planner_output(
        oversized_payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids=set(),
    )

    assert oversized_outcome.decision.question_updates == []
    assert oversized_outcome.raw_question_update_count == 6
    assert len(oversized_outcome.question_update_reviews) == 1
    assert oversized_outcome.question_update_reviews[0].reason_code == (
        QuestionUpdateReasonCode.TOO_MANY_UPDATES.value
    )


def test_malformed_collection_and_create_update_conflict_are_audited() -> None:
    malformed_payload = act_payload()
    malformed_payload["question_updates"] = {"status": "open"}
    malformed_outcome = review_qwen_planner_output(
        malformed_payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids=set(),
    )
    assert malformed_outcome.question_update_reviews[0].reason_code == (
        QuestionUpdateReasonCode.MALFORMED_COLLECTION.value
    )

    malformed_item_payload = act_payload()
    malformed_item_payload["question_updates"] = ["not-an-object"]
    malformed_item_outcome = review_qwen_planner_output(
        malformed_item_payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids=set(),
    )
    assert malformed_item_outcome.question_update_reviews[0].reason_code == (
        QuestionUpdateReasonCode.MALFORMED_UPDATE.value
    )

    conflict_payload = act_payload()
    new_question = question("Q_NEW")
    conflict_payload["new_questions"] = [new_question.to_dict()]
    conflict_payload["question_updates"] = [unavailable_update("Q_NEW")]
    conflict_outcome = review_qwen_planner_output(
        conflict_payload,
        questions=[question(TARGET_ID), question(CLOSE_ID)],
        available_evidence_ids=set(),
    )
    assert conflict_outcome.decision.question_updates == []
    assert conflict_outcome.question_update_reviews[0].reason_code == (
        QuestionUpdateReasonCode.NEW_QUESTION_CONFLICT.value
    )
