from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    MAX_CROSS_DOMAIN_ACTIONS,
    DecisionEvaluation,
    DecisionType,
    EvidenceGapStatus,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    InvestigationValidationError,
    PlannerDecision,
    PlannerDecisionOutcome,
    QuestionUpdate,
    QuestionUpdateDisposition,
    QuestionUpdateReasonCode,
    QuestionUpdateReview,
    RunEvaluation,
)
from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ConclusionLevel,
    GoalStatus,
    StopReason,
)


def open_question(
    *,
    question_id: str = "Q_ROOT_CAUSE",
    goal_id: str = "GOAL_LOT_01",
) -> InvestigationQuestion:
    return InvestigationQuestion(
        question_id=question_id,
        goal_id=goal_id,
        question="Which process mechanism caused the Cu CMP scratch?",
        rationale="The user asked for a root cause, not only an impact list.",
        scope={"lot_id": "LOT_01", "operation": "CU_CMP"},
    )


def inspect_action(*, scope: dict[str, str] | None = None) -> InvestigationAction:
    return InvestigationAction(
        action_id="ACT_FDC_01",
        kind=ActionKind.INSPECT_FDC_SPC.value,
        agent="fdc",
        reason="Check process anomalies after the shared exposure is known.",
        inputs={"lot_ids": ["LOT_01"], "parameter_hint": "EPD"},
        scope=scope or {"lot_id": "LOT_01", "operation": "CU_CMP"},
    )


class AutonomousReactQuestionContractTest(unittest.TestCase):
    def test_goal_cannot_expand_beyond_the_eight_action_runtime_boundary(self) -> None:
        with self.assertRaises(InvestigationValidationError):
            InvestigationGoal(
                goal_id="GOAL_UNBOUNDED",
                intent=InvestigationIntent.ROOT_CAUSE.value,
                summary="Attempt an unbounded autonomous investigation.",
                max_steps=MAX_CROSS_DOMAIN_ACTIONS + 1,
            )

    def test_question_supports_only_open_closed_or_unavailable_lifecycle(self) -> None:
        question = open_question()
        self.assertEqual(
            InvestigationQuestion.from_dict(question.to_dict()),
            question,
        )

        closed = InvestigationQuestion(
            question_id=question.question_id,
            goal_id=question.goal_id,
            question=question.question,
            rationale=question.rationale,
            scope=question.scope,
            status=EvidenceGapStatus.CLOSED.value,
            answer="EPD endpoint detection failed during Cu CMP.",
            evidence_ids=["EV_FDC_EPD_01"],
        )
        unavailable = InvestigationQuestion(
            question_id="Q_RECIPE",
            goal_id=question.goal_id,
            question="Was a recipe version changed before the excursion?",
            rationale="A recipe change is an alternative process mechanism.",
            scope={"lot_id": "LOT_01", "operation": "CU_CMP"},
            status=EvidenceGapStatus.UNAVAILABLE.value,
            unavailable_reason="Recipe history is unavailable for the selected period.",
        )

        self.assertEqual(closed.status, "closed")
        self.assertEqual(unavailable.status, "unavailable")
        json.dumps([closed.to_dict(), unavailable.to_dict()])

    def test_question_rejects_unsupported_or_unexplained_terminal_state(self) -> None:
        with self.assertRaises(InvestigationValidationError):
            InvestigationQuestion(
                question_id="Q_BAD",
                goal_id="GOAL_LOT_01",
                question="Is the evidence sufficient?",
                rationale="Test invalid status.",
                status="partially_closed",
            )
        with self.assertRaises(InvestigationValidationError):
            InvestigationQuestion(
                question_id="Q_NO_EVIDENCE",
                goal_id="GOAL_LOT_01",
                question="What caused the scratch?",
                rationale="A closed gap must remain evidence-backed.",
                status=EvidenceGapStatus.CLOSED.value,
                answer="EPD failed.",
            )
        with self.assertRaises(InvestigationValidationError):
            InvestigationQuestion(
                question_id="Q_NO_REASON",
                goal_id="GOAL_LOT_01",
                question="Did the recipe change?",
                rationale="Unavailable must be explicit.",
                status=EvidenceGapStatus.UNAVAILABLE.value,
            )

    def test_question_update_is_a_terminal_only_delta(self) -> None:
        closed = QuestionUpdate(
            question_id="Q_ROOT_CAUSE",
            status=EvidenceGapStatus.CLOSED.value,
            answer="EPD endpoint detection failed during Cu CMP.",
            evidence_ids=["EV_FDC_EPD_01"],
        )
        unavailable = QuestionUpdate(
            question_id="Q_RECIPE",
            status=EvidenceGapStatus.UNAVAILABLE.value,
            unavailable_reason="Recipe history is unavailable for the selected period.",
        )

        self.assertEqual(QuestionUpdate.from_dict(closed.to_dict()), closed)
        self.assertEqual(QuestionUpdate.from_dict(unavailable.to_dict()), unavailable)
        self.assertEqual(
            set(closed.to_dict()),
            {
                "question_id",
                "status",
                "answer",
                "evidence_ids",
                "unavailable_reason",
            },
        )

    def test_question_update_rejects_open_or_invalid_terminal_fields(self) -> None:
        invalid_updates = [
            {
                "question_id": "Q_OPEN",
                "status": EvidenceGapStatus.OPEN.value,
            },
            {
                "question_id": "Q_CLOSED_NO_EVIDENCE",
                "status": EvidenceGapStatus.CLOSED.value,
                "answer": "EPD failed.",
            },
            {
                "question_id": "Q_UNAVAILABLE_WITH_ANSWER",
                "status": EvidenceGapStatus.UNAVAILABLE.value,
                "answer": "A partial answer is not a terminal unavailable state.",
                "unavailable_reason": "Data was incomplete.",
            },
        ]
        for payload in invalid_updates:
            with self.subTest(payload=payload):
                with self.assertRaises(InvestigationValidationError):
                    QuestionUpdate.from_dict(payload)

    def test_question_update_review_and_outcome_round_trip(self) -> None:
        update = QuestionUpdate(
            question_id="Q_ROOT_CAUSE",
            status=EvidenceGapStatus.CLOSED.value,
            answer="EPD endpoint detection failed during Cu CMP.",
            evidence_ids=["EV_FDC_EPD_01"],
        )
        decision = PlannerDecision(
            decision_id="DECISION_REVIEWED",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.STOP.value,
            reason="The evidence-backed investigation question is closed.",
            goal_status=GoalStatus.SATISFIED.value,
            proposed_conclusion_level=ConclusionLevel.SUPPORTED.value,
            stop_reason=StopReason.GOAL_SATISFIED.value,
            question_updates=[update],
        )
        review = QuestionUpdateReview(
            decision_id=decision.decision_id,
            disposition=QuestionUpdateDisposition.ACCEPTED.value,
            reason_code=QuestionUpdateReasonCode.ACCEPTED.value,
            reason="The terminal update references available Evidence.",
            update_index=0,
            question_id=update.question_id,
            claimed_status=update.status,
        )
        outcome = PlannerDecisionOutcome(
            decision=decision,
            question_update_reviews=[review],
            raw_question_update_count=1,
        )

        self.assertEqual(
            PlannerDecisionOutcome.from_dict(outcome.to_dict()),
            outcome,
        )

    def test_rejected_question_update_review_cannot_claim_acceptance(self) -> None:
        with self.assertRaisesRegex(
            InvestigationValidationError,
            "rejected QuestionUpdate cannot use reason_code=accepted",
        ):
            QuestionUpdateReview(
                decision_id="DECISION_REJECTED",
                disposition=QuestionUpdateDisposition.REJECTED.value,
                reason_code=QuestionUpdateReasonCode.ACCEPTED.value,
                reason="Invalid mixed disposition.",
                update_index=0,
                question_id="Q_ROOT_CAUSE",
                claimed_status=EvidenceGapStatus.OPEN.value,
            )

    def test_planner_reads_legacy_full_question_update_but_writes_compact_delta(self) -> None:
        question = open_question()
        legacy_update = {
            **question.to_dict(),
            "status": EvidenceGapStatus.CLOSED.value,
            "answer": "EPD endpoint detection failed during Cu CMP.",
            "evidence_ids": ["EV_FDC_EPD_01"],
        }
        payload = PlannerDecision(
            decision_id="DECISION_LEGACY_UPDATE",
            goal_id=question.goal_id,
            decision_type=DecisionType.STOP.value,
            reason="The legacy snapshot closed its evidence gap.",
            goal_status=GoalStatus.SATISFIED.value,
            proposed_conclusion_level=ConclusionLevel.SUPPORTED.value,
            stop_reason=StopReason.GOAL_SATISFIED.value,
        ).to_dict()
        payload["question_updates"] = [legacy_update]

        restored = PlannerDecision.from_dict(payload)
        serialized_update = restored.to_dict()["question_updates"][0]

        self.assertIsInstance(restored.question_updates[0], QuestionUpdate)
        self.assertNotIn("goal_id", serialized_update)
        self.assertNotIn("question", serialized_update)
        self.assertEqual(serialized_update["question_id"], question.question_id)

    def test_planner_reports_the_indexed_path_for_open_question_update(self) -> None:
        payload = PlannerDecision(
            decision_id="DECISION_BAD_UPDATE",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.STOP.value,
            reason="Validate an invalid compact update.",
            goal_status=GoalStatus.BLOCKED.value,
            proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
            stop_reason=StopReason.DATA_UNAVAILABLE.value,
        ).to_dict()
        payload["question_updates"] = [
            {"question_id": "Q_ROOT_CAUSE", "status": "open"}
        ]

        with self.assertRaisesRegex(
            InvestigationValidationError,
            r"question_updates\[0\]\.status must be closed or unavailable",
        ):
            PlannerDecision.from_dict(payload)


class AutonomousReactDecisionContractTest(unittest.TestCase):
    def test_action_scope_produces_a_stable_deduplication_key(self) -> None:
        first = inspect_action(scope={"lot_id": "LOT_01", "operation": "CU_CMP"})
        second = InvestigationAction(
            action_id="ACT_FDC_RETRY",
            kind=first.kind,
            agent=first.agent,
            reason="The model requested the same investigation again.",
            inputs={"parameter_hint": "THK", "lot_ids": ["LOT_01", "LOT_02"]},
            scope={"operation": "CU_CMP", "lot_id": "LOT_01"},
        )
        impact_lot = inspect_action(scope={"lot_id": "LOT_02", "operation": "CU_CMP"})

        self.assertEqual(first.deduplication_key, second.deduplication_key)
        self.assertNotEqual(first.deduplication_key, impact_lot.deduplication_key)
        self.assertEqual(InvestigationAction.from_dict(first.to_dict()), first)

    def test_act_decision_round_trips_and_keeps_new_questions_in_the_same_goal(self) -> None:
        question = open_question()
        decision = PlannerDecision(
            decision_id="DECISION_01",
            goal_id=question.goal_id,
            decision_type=DecisionType.ACT.value,
            reason="The process mechanism gap remains open.",
            goal_status=GoalStatus.IN_PROGRESS.value,
            proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
            next_action=inspect_action(),
            target_question_ids=[question.question_id],
            new_questions=[question],
        )

        restored = PlannerDecision.from_dict(decision.to_dict())

        self.assertEqual(restored, decision)
        self.assertEqual(restored.next_action, decision.next_action)
        json.dumps(restored.to_dict())

    def test_stop_decision_is_explicit_and_mutually_exclusive_with_action(self) -> None:
        decision = PlannerDecision(
            decision_id="DECISION_STOP",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.STOP.value,
            reason="All critical questions are evidence-backed.",
            goal_status=GoalStatus.SATISFIED.value,
            proposed_conclusion_level=ConclusionLevel.SUPPORTED.value,
            stop_reason=StopReason.GOAL_SATISFIED.value,
        )
        self.assertIsNone(decision.next_action)
        self.assertEqual(PlannerDecision.from_dict(decision.to_dict()), decision)

        with self.assertRaises(InvestigationValidationError):
            PlannerDecision(
                decision_id="DECISION_INVALID_STOP",
                goal_id="GOAL_LOT_01",
                decision_type=DecisionType.STOP.value,
                reason="Invalid mixed decision.",
                goal_status=GoalStatus.SATISFIED.value,
                proposed_conclusion_level=ConclusionLevel.SUPPORTED.value,
                next_action=inspect_action(),
                stop_reason=StopReason.GOAL_SATISFIED.value,
            )

    def test_act_cannot_close_and_target_the_same_question(self) -> None:
        with self.assertRaisesRegex(
            InvestigationValidationError,
            (
                "target_question_ids and question_updates overlap for "
                r"\['Q_ROOT_CAUSE'\].*keep it open and omit its QuestionUpdate"
            ),
        ):
            PlannerDecision(
                decision_id="DECISION_CLOSE_AND_TARGET",
                goal_id="GOAL_LOT_01",
                decision_type=DecisionType.ACT.value,
                reason="An action must target a question that remains open.",
                goal_status=GoalStatus.IN_PROGRESS.value,
                proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
                next_action=inspect_action(),
                target_question_ids=["Q_ROOT_CAUSE"],
                question_updates=[
                    QuestionUpdate(
                        question_id="Q_ROOT_CAUSE",
                        status=EvidenceGapStatus.CLOSED.value,
                        answer="EPD failed.",
                        evidence_ids=["EV_FDC_EPD_01"],
                    )
                ],
            )

    def test_decision_rejects_duplicate_question_updates(self) -> None:
        update = QuestionUpdate(
            question_id="Q_ROOT_CAUSE",
            status=EvidenceGapStatus.UNAVAILABLE.value,
            unavailable_reason="The required source is unavailable.",
        )
        with self.assertRaisesRegex(
            InvestigationValidationError,
            "question_updates must not contain duplicate ids",
        ):
            PlannerDecision(
                decision_id="DECISION_DUPLICATE_UPDATES",
                goal_id="GOAL_LOT_01",
                decision_type=DecisionType.STOP.value,
                reason="Duplicate deltas are ambiguous.",
                goal_status=GoalStatus.BLOCKED.value,
                proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
                stop_reason=StopReason.DATA_UNAVAILABLE.value,
                question_updates=[update, update],
            )

    def test_decision_rejects_cross_goal_question_and_unknown_structured_field(self) -> None:
        with self.assertRaises(InvestigationValidationError):
            PlannerDecision(
                decision_id="DECISION_CROSS_GOAL",
                goal_id="GOAL_LOT_01",
                decision_type=DecisionType.ACT.value,
                reason="Invalid objective expansion.",
                goal_status=GoalStatus.IN_PROGRESS.value,
                proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
                next_action=inspect_action(),
                target_question_ids=["Q_OTHER_GOAL"],
                new_questions=[open_question(goal_id="GOAL_LOT_02")],
            )

        payload = PlannerDecision(
            decision_id="DECISION_STRICT",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.STOP.value,
            reason="No legal action remains.",
            goal_status=GoalStatus.BLOCKED.value,
            proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
            stop_reason=StopReason.NO_ALLOWED_ACTION.value,
        ).to_dict()
        payload["unbounded_tool_name"] = "query_everything"
        with self.assertRaises(InvestigationValidationError):
            PlannerDecision.from_dict(payload)


class AutonomousReactEvaluationContractTest(unittest.TestCase):
    def test_five_metrics_round_trip_without_additional_score_system(self) -> None:
        step = DecisionEvaluation(
            decision_id="DECISION_01",
            decision_valid=True,
            evidence_gain=True,
            redundant=False,
            reason="The registered FDC action added endpoint evidence.",
            new_evidence_ids=["EV_FDC_EPD_01"],
        )
        evaluation = RunEvaluation(
            goal_id="GOAL_LOT_01",
            goal_success=True,
            stop_correct=True,
            summary="The run answered the root-cause goal and stopped after gate validation.",
            decision_evaluations=[step],
        )

        restored = RunEvaluation.from_dict(evaluation.to_dict())

        self.assertEqual(restored, evaluation)
        self.assertEqual(
            set(restored.decision_evaluations[0].to_dict()),
            {
                "decision_id",
                "decision_valid",
                "evidence_gain",
                "redundant",
                "reason",
                "new_evidence_ids",
            },
        )

    def test_evaluation_rejects_inconsistent_evidence_gain_or_redundancy(self) -> None:
        with self.assertRaises(InvestigationValidationError):
            DecisionEvaluation(
                decision_id="DECISION_NO_EVIDENCE",
                decision_valid=True,
                evidence_gain=True,
                redundant=False,
                reason="Cannot claim gain without new evidence ids.",
            )
        with self.assertRaises(InvestigationValidationError):
            DecisionEvaluation(
                decision_id="DECISION_REDUNDANT_GAIN",
                decision_valid=True,
                evidence_gain=True,
                redundant=True,
                reason="Gain and redundancy cannot both be true.",
                new_evidence_ids=["EV_01"],
            )
        with self.assertRaises(InvestigationValidationError):
            DecisionEvaluation(
                decision_id="DECISION_NOT_BOOLEAN",
                decision_valid=1,  # type: ignore[arg-type]
                evidence_gain=False,
                redundant=False,
                reason="Metrics must remain booleans.",
            )


if __name__ == "__main__":
    unittest.main()
