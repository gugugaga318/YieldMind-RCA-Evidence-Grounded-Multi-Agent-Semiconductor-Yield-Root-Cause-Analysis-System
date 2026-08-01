from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.schemas import RCAJobStateResponse  # noqa: E402
from yield_rca_core.evidence_models import Evidence, EvidenceSourceType  # noqa: E402
from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ConclusionLevel,
    DecisionEvaluation,
    DecisionType,
    EvidenceGapStatus,
    GoalStatus,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    PlannerDecision,
    QuestionUpdate,
    RunEvaluation,
    StopReason,
)
from yield_rca_core.models import ModelValidationError, RCAJob, RCAState  # noqa: E402

GOAL_ID = "GOAL_LOT_01"
EVIDENCE_ID = "EV_FDC_EPD_01"


def make_evidence() -> Evidence:
    return Evidence(
        evidence_id=EVIDENCE_ID,
        source_type=EvidenceSourceType.FDC.value,
        source_id="fdc:LOT_01:CU_CMP:EPD",
        summary="EPD endpoint signal was missing during Cu CMP.",
    )


def make_goal() -> InvestigationGoal:
    return InvestigationGoal(
        goal_id=GOAL_ID,
        intent=InvestigationIntent.ROOT_CAUSE.value,
        summary="Identify the Cu CMP scratch mechanism for LOT_01.",
        known_facts={"lot_id": "LOT_01", "operation": "CU_CMP"},
    )


def make_question(
    *,
    question_id: str = "Q_ROOT_CAUSE",
    goal_id: str = GOAL_ID,
    status: str = EvidenceGapStatus.CLOSED.value,
) -> InvestigationQuestion:
    terminal_fields: dict[str, Any]
    if status == EvidenceGapStatus.CLOSED.value:
        terminal_fields = {
            "answer": "The Cu CMP EPD endpoint signal failed.",
            "evidence_ids": [EVIDENCE_ID],
        }
    elif status == EvidenceGapStatus.UNAVAILABLE.value:
        terminal_fields = {"unavailable_reason": "Recipe history is unavailable."}
    else:
        terminal_fields = {}
    return InvestigationQuestion(
        question_id=question_id,
        goal_id=goal_id,
        question="Which process mechanism caused the Cu CMP scratch?",
        rationale="A root-cause claim must be backed by process evidence.",
        scope={"lot_id": "LOT_01", "operation": "CU_CMP"},
        status=status,
        **terminal_fields,
    )


def make_act_decision(
    *,
    decision_id: str = "DECISION_01",
    target_question_id: str = "Q_ROOT_CAUSE",
) -> PlannerDecision:
    return PlannerDecision(
        decision_id=decision_id,
        goal_id=GOAL_ID,
        decision_type=DecisionType.ACT.value,
        reason="Inspect the Cu CMP endpoint signal for the open mechanism question.",
        goal_status=GoalStatus.IN_PROGRESS.value,
        proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
        next_action=InvestigationAction(
            action_id="ACTION_FDC_01",
            kind=ActionKind.INSPECT_FDC_SPC.value,
            agent="fdc",
            reason="Check the endpoint trace after the scratch observation.",
            inputs={"lot_ids": ["LOT_01"], "parameter_hint": "EPD"},
            scope={"lot_id": "LOT_01", "operation": "CU_CMP"},
        ),
        target_question_ids=[target_question_id],
    )


def make_state() -> RCAState:
    question = make_question()
    return RCAState(
        job=RCAJob(job_id="RCA_TRACE_01", user_query="Why did LOT_01 scratch?"),
        evidence=[make_evidence()],
        investigation_goal=make_goal(),
        investigation_questions=[question],
        planner_decisions=[
            make_act_decision(),
            PlannerDecision(
                decision_id="DECISION_STOP",
                goal_id=GOAL_ID,
                decision_type=DecisionType.STOP.value,
                reason="The root-cause question is now evidence-backed.",
                goal_status=GoalStatus.SATISFIED.value,
                proposed_conclusion_level=ConclusionLevel.SUPPORTED.value,
                stop_reason=StopReason.GOAL_SATISFIED.value,
                question_updates=[
                    QuestionUpdate(
                        question_id=question.question_id,
                        status=EvidenceGapStatus.CLOSED.value,
                        answer=question.answer,
                        evidence_ids=list(question.evidence_ids),
                    )
                ],
            ),
        ],
        goal_status=GoalStatus.SATISFIED.value,
        conclusion_level=ConclusionLevel.SUPPORTED.value,
        evidence_gaps=["legacy: no additional SPC parameter gap"],
        stop_reason=StopReason.GOAL_SATISFIED.value,
    )


def make_run_evaluation(
    *,
    goal_id: str = GOAL_ID,
    new_evidence_id: str = EVIDENCE_ID,
) -> RunEvaluation:
    return RunEvaluation(
        goal_id=goal_id,
        goal_success=True,
        stop_correct=True,
        summary="The evidence-backed root-cause goal stopped at the correct boundary.",
        decision_evaluations=[
            DecisionEvaluation(
                decision_id="DECISION_01",
                decision_valid=True,
                evidence_gain=True,
                redundant=False,
                reason="The FDC action added the endpoint Evidence.",
                new_evidence_ids=[new_evidence_id],
            ),
            DecisionEvaluation(
                decision_id="DECISION_STOP",
                decision_valid=True,
                evidence_gain=False,
                redundant=False,
                reason="The stop decision added no Evidence and used the satisfied boundary.",
            ),
        ],
    )


class RCAStatePlannerTraceSerializationTest(unittest.TestCase):
    def test_questions_and_decisions_round_trip_as_typed_state(self) -> None:
        state = make_state()

        restored = RCAState.from_dict(state.to_dict())

        self.assertEqual(restored, state)
        self.assertIsInstance(restored.investigation_questions[0], InvestigationQuestion)
        self.assertIsInstance(restored.planner_decisions[0], PlannerDecision)
        self.assertEqual(
            restored.planner_decisions[1].question_updates[0].evidence_ids,
            [EVIDENCE_ID],
        )
        self.assertEqual(restored.evidence_gaps, state.evidence_gaps)

    def test_legacy_state_without_trace_fields_keeps_evidence_gaps_compatible(self) -> None:
        legacy_payload = RCAState(
            job=RCAJob(job_id="RCA_LEGACY", user_query="Analyze legacy snapshot."),
            evidence_gaps=["legacy free-text gap"],
        ).to_dict()
        legacy_payload.pop("investigation_questions")
        legacy_payload.pop("planner_decisions")

        restored = RCAState.from_dict(legacy_payload)

        self.assertEqual(restored.investigation_questions, [])
        self.assertEqual(restored.planner_decisions, [])
        self.assertEqual(restored.evidence_gaps, ["legacy free-text gap"])
        self.assertEqual(
            RCAState.from_dict(restored.to_dict()),
            restored,
        )

    def test_api_response_exposes_typed_trace_and_defaults_legacy_fields(self) -> None:
        response = RCAJobStateResponse.model_validate(make_state().to_dict())
        payload = response.model_dump()

        self.assertEqual(
            payload["investigation_questions"][0]["status"],
            EvidenceGapStatus.CLOSED.value,
        )
        self.assertEqual(
            payload["planner_decisions"][0]["next_action"]["kind"],
            ActionKind.INSPECT_FDC_SPC.value,
        )
        self.assertEqual(
            payload["planner_decisions"][1]["question_updates"][0]["evidence_ids"],
            [EVIDENCE_ID],
        )

        legacy_payload = RCAState(
            job=RCAJob(job_id="RCA_API_LEGACY", user_query="Read old API state.")
        ).to_dict()
        legacy_payload.pop("investigation_questions")
        legacy_payload.pop("planner_decisions")
        legacy_response = RCAJobStateResponse.model_validate(legacy_payload)
        self.assertEqual(legacy_response.investigation_questions, [])
        self.assertEqual(legacy_response.planner_decisions, [])


class RCAStatePlannerTraceValidationTest(unittest.TestCase):
    def test_trace_collections_require_lists_of_typed_contracts(self) -> None:
        with self.assertRaisesRegex(
            ModelValidationError,
            "investigation_questions must be a list",
        ):
            RCAState(
                job=RCAJob(job_id="RCA_BAD_QUESTIONS", user_query="Invalid trace."),
                investigation_goal=make_goal(),
                investigation_questions=cast(Any, (make_question(),)),
            )

        with self.assertRaisesRegex(
            ModelValidationError,
            "planner_decisions must contain PlannerDecision",
        ):
            RCAState(
                job=RCAJob(job_id="RCA_BAD_DECISIONS", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[make_question()],
                planner_decisions=cast(Any, [object()]),
            )

        payload = make_state().to_dict()
        payload["planner_decisions"] = {"decision_id": "NOT_A_LIST"}
        with self.assertRaisesRegex(
            ModelValidationError,
            "planner_decisions must be a list",
        ):
            RCAState.from_dict(payload)

    def test_question_and_decision_ids_must_be_unique(self) -> None:
        question = make_question()
        with self.assertRaisesRegex(
            ModelValidationError,
            "duplicate investigation question_id",
        ):
            RCAState(
                job=RCAJob(job_id="RCA_DUP_QUESTION", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[question, question],
            )

        decision = make_act_decision()
        with self.assertRaisesRegex(
            ModelValidationError,
            "duplicate planner decision_id",
        ):
            RCAState(
                job=RCAJob(job_id="RCA_DUP_DECISION", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[question],
                planner_decisions=[decision, decision],
            )

    def test_trace_must_remain_in_goal_and_reference_known_evidence(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "question goal_id"):
            RCAState(
                job=RCAJob(job_id="RCA_CROSS_GOAL_Q", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[make_question(goal_id="GOAL_OTHER")],
            )

        cross_goal_decision = PlannerDecision(
            decision_id="DECISION_OTHER_GOAL",
            goal_id="GOAL_OTHER",
            decision_type=DecisionType.STOP.value,
            reason="Invalid cross-goal stop.",
            goal_status=GoalStatus.BLOCKED.value,
            proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
            stop_reason=StopReason.NO_ALLOWED_ACTION.value,
        )
        with self.assertRaisesRegex(ModelValidationError, "decision goal_id"):
            RCAState(
                job=RCAJob(job_id="RCA_CROSS_GOAL_D", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[make_question()],
                planner_decisions=[cross_goal_decision],
            )

        unknown_evidence_question = InvestigationQuestion(
            question_id="Q_UNKNOWN_EVIDENCE",
            goal_id=GOAL_ID,
            question="What caused the scratch?",
            rationale="Validate state-level evidence references.",
            status=EvidenceGapStatus.CLOSED.value,
            answer="EPD failed.",
            evidence_ids=["EV_UNKNOWN"],
        )
        with self.assertRaisesRegex(ModelValidationError, "unknown evidence_ids"):
            RCAState(
                job=RCAJob(job_id="RCA_UNKNOWN_EV", user_query="Invalid trace."),
                evidence=[make_evidence()],
                investigation_goal=make_goal(),
                investigation_questions=[unknown_evidence_question],
            )


class RCAStateRunEvaluationContractTest(unittest.TestCase):
    def test_run_evaluation_round_trips_as_typed_first_class_state(self) -> None:
        payload = make_state().to_dict()
        payload["run_evaluation"] = make_run_evaluation().to_dict()

        restored = RCAState.from_dict(payload)

        self.assertIsInstance(restored.run_evaluation, RunEvaluation)
        self.assertEqual(restored.run_evaluation, make_run_evaluation())
        self.assertEqual(
            RCAState.from_dict(restored.to_dict()),
            restored,
        )

    def test_legacy_state_without_run_evaluation_defaults_to_none(self) -> None:
        payload = make_state().to_dict()
        payload.pop("run_evaluation")

        restored = RCAState.from_dict(payload)

        self.assertIsNone(restored.run_evaluation)
        self.assertIsNone(restored.to_dict()["run_evaluation"])

    def test_run_evaluation_requires_the_typed_contract_and_matching_goal(self) -> None:
        with self.assertRaisesRegex(
            ModelValidationError,
            "run_evaluation must be a RunEvaluation",
        ):
            RCAState(
                job=RCAJob(
                    job_id="RCA_BAD_EVALUATION_TYPE",
                    user_query="Invalid evaluation.",
                ),
                run_evaluation=cast(Any, object()),
            )

        payload = make_state().to_dict()
        payload["run_evaluation"] = make_run_evaluation(
            goal_id="GOAL_OTHER"
        ).to_dict()
        with self.assertRaisesRegex(
            ModelValidationError,
            "run_evaluation goal_id must match investigation_goal",
        ):
            RCAState.from_dict(payload)

    def test_evaluation_ids_must_match_planner_decisions_in_number_and_order(
        self,
    ) -> None:
        complete = make_run_evaluation()
        cases = {
            "missing": RunEvaluation(
                goal_id=GOAL_ID,
                goal_success=True,
                stop_correct=True,
                summary="Only one decision was evaluated.",
                decision_evaluations=[complete.decision_evaluations[0]],
            ),
            "reordered": RunEvaluation(
                goal_id=GOAL_ID,
                goal_success=True,
                stop_correct=True,
                summary="The evaluations were placed in the wrong order.",
                decision_evaluations=list(reversed(complete.decision_evaluations)),
            ),
        }
        for label, evaluation in cases.items():
            with self.subTest(label=label):
                payload = make_state().to_dict()
                payload["run_evaluation"] = evaluation.to_dict()
                with self.assertRaisesRegex(
                    ModelValidationError,
                    "must match planner_decisions in number and order",
                ):
                    RCAState.from_dict(payload)

    def test_evaluation_new_evidence_ids_must_exist_in_final_evidence(self) -> None:
        payload = make_state().to_dict()
        payload["run_evaluation"] = make_run_evaluation(
            new_evidence_id="EV_UNKNOWN"
        ).to_dict()

        with self.assertRaisesRegex(
            ModelValidationError,
            "decision evaluation references unknown evidence_ids",
        ):
            RCAState.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
