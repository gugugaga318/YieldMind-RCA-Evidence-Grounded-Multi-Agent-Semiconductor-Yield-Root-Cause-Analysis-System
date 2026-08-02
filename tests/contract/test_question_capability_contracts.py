from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    QUESTION_CAPABILITY_REGISTRY,
    CapabilityNotice,
    InvestigationAction,
    InvestigationQuestion,
    QuestionKind,
    action_scope_matches_question,
    capability_notice_for,
    requested_capability_notices,
    validate_action_for_questions,
)
from yield_rca_core.investigation_models import ActionKind  # noqa: E402
from yield_rca_core.question_capability import QuestionCapabilityError  # noqa: E402


def question(kind: QuestionKind, *, question_id: str = "Q_QUESTION") -> InvestigationQuestion:
    return InvestigationQuestion(
        question_id=question_id,
        goal_id="GOAL_LOT_01",
        question="Which bounded observation answers this gap?",
        rationale="The question is required by the investigation goal.",
        question_kind=kind.value,
        scope={"lot_id": "LOT_01", "module": "CU_CMP"},
    )


def action(kind: ActionKind, *, lot_id: str = "LOT_01") -> InvestigationAction:
    return InvestigationAction(
        action_id=f"ACT_{kind.value}",
        kind=kind.value,
        agent={
            ActionKind.INSPECT_DEFECT_PATTERN: "defect_wat",
            ActionKind.FIND_SHARED_EXPOSURE: "mes",
            ActionKind.INSPECT_FDC_SPC: "fdc",
        }.get(kind, "defect_wat"),
        reason="Collect the evidence group required by the open Question.",
        inputs={"lot_id": lot_id},
        scope={"lot_id": lot_id, "module": "CU_CMP"},
    )


class QuestionCapabilityContractTest(unittest.TestCase):
    def test_new_questions_round_trip_kind_and_legacy_questions_are_migrated(self) -> None:
        typed = question(QuestionKind.SPC_SIGNAL)
        self.assertEqual(InvestigationQuestion.from_dict(typed.to_dict()), typed)
        self.assertEqual(typed.to_dict()["question_kind"], "spc_signal")

        legacy = InvestigationQuestion.from_dict(
            {
                "question_id": "GOAL_LOT_01:q:process_mechanism",
                "goal_id": "GOAL_LOT_01",
                "question": "Which mechanism caused the scratch?",
                "rationale": "Legacy snapshots predate QuestionKind.",
            }
        )
        self.assertEqual(legacy.question_kind, QuestionKind.PROCESS_MECHANISM.value)
        unknown = InvestigationQuestion.from_dict(
            {
                "question_id": "Q_UNKNOWN_LEGACY",
                "goal_id": "GOAL_LOT_01",
                "question": "An unclassified legacy gap",
                "rationale": "It needs explicit migration.",
            }
        )
        self.assertEqual(unknown.question_kind, QuestionKind.UNSUPPORTED.value)
        json.dumps(unknown.to_dict())

    def test_registry_is_the_single_source_for_supported_and_material_capabilities(self) -> None:
        mechanism = QUESTION_CAPABILITY_REGISTRY[QuestionKind.PROCESS_MECHANISM.value]
        self.assertIn(ActionKind.INSPECT_FDC_SPC.value, mechanism.direct_actions)
        self.assertIn(ActionKind.INSPECT_DEFECT_PATTERN.value, mechanism.supporting_actions)
        self.assertIn("process_anomaly", mechanism.closure_evidence_groups)

        notice = capability_notice_for(QuestionKind.MATERIAL_TRACE)
        self.assertIsInstance(notice, CapabilityNotice)
        self.assertFalse(notice.supported)
        self.assertEqual(notice.request_source, "user")
        self.assertTrue(
            requested_capability_notices("Trace the supplier material batch genealogy.")
        )
        self.assertFalse(requested_capability_notices("Check the Cu CMP SPC excursion."))

    def test_incompatible_action_is_rejected_before_scope_or_tool_dispatch(self) -> None:
        with self.assertRaises(QuestionCapabilityError) as caught:
            validate_action_for_questions(
                action(ActionKind.INSPECT_FDC_SPC),
                [question(QuestionKind.IMPACT_SCOPE)],
            )
        self.assertEqual(caught.exception.reason_code, "action_question_mismatch")

        with self.assertRaises(QuestionCapabilityError) as caught:
            validate_action_for_questions(
                action(ActionKind.INSPECT_FDC_SPC),
                [question(QuestionKind.MATERIAL_TRACE)],
            )
        self.assertEqual(caught.exception.reason_code, "unsupported_question_kind")

    def test_scope_mismatch_is_hard_rejected_and_multi_target_validation_is_atomic(self) -> None:
        mismatched = action(ActionKind.INSPECT_FDC_SPC, lot_id="LOT_99")
        self.assertFalse(
            action_scope_matches_question(
                mismatched,
                question(QuestionKind.SPC_SIGNAL),
            )
        )
        with self.assertRaises(QuestionCapabilityError) as caught:
            validate_action_for_questions(
                mismatched,
                [question(QuestionKind.SPC_SIGNAL)],
            )
        self.assertEqual(caught.exception.reason_code, "action_scope_mismatch")

        compatible = action(ActionKind.INSPECT_FDC_SPC)
        with self.assertRaises(QuestionCapabilityError) as caught:
            validate_action_for_questions(
                compatible,
                [
                    question(QuestionKind.SPC_SIGNAL, question_id="Q_SPC"),
                    question(QuestionKind.IMPACT_SCOPE, question_id="Q_IMPACT"),
                ],
            )
        self.assertEqual(caught.exception.reason_code, "action_question_mismatch")

    def test_action_must_contribute_to_a_currently_missing_evidence_group(self) -> None:
        mechanism = question(QuestionKind.PROCESS_MECHANISM)
        with self.assertRaises(QuestionCapabilityError) as caught:
            validate_action_for_questions(
                action(ActionKind.INSPECT_FDC_SPC),
                [mechanism],
                missing_evidence_groups={
                    mechanism.question_id: frozenset(
                        {"product_signal", "shared_exposure"}
                    )
                },
            )

        self.assertEqual(caught.exception.reason_code, "no_expected_evidence_gain")


if __name__ == "__main__":
    unittest.main()
