from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ActionRecord,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationValidationError,
)


class ControlledReactModelContractTest(unittest.TestCase):
    def test_goal_preserves_bounded_intent_and_budget(self) -> None:
        goal = InvestigationGoal(
            goal_id="GOAL_SCRATCH_001",
            intent=InvestigationIntent.ROOT_CAUSE.value,
            summary="Investigate Cu CMP scratch and its impact scope.",
            known_facts={"lot_id": "LOT_01", "module": "CU", "defect": "scratch"},
            required_evidence=["shared_exposure", "process_mechanism", "product_outcome"],
        )
        self.assertEqual(goal.max_steps, 8)
        self.assertEqual(goal.to_dict()["intent"], "root_cause")

    def test_action_is_bounded_to_registered_kind_and_auditable_reason(self) -> None:
        action = InvestigationAction(
            action_id="ACT_001",
            kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
            agent="defect_wat",
            reason="Known scratch requires a defect-pattern observation before RCA.",
            inputs={"lot_id": "LOT_01"},
        )
        record = ActionRecord(
            action=action,
            status="completed",
            produced_finding_ids=["FINDING_001"],
            produced_evidence_ids=["EV_DEFECT_SCRATCH"],
            decision_summary="The defect pattern is now available for exposure analysis.",
        )
        self.assertEqual(record.to_dict()["action"]["kind"], "inspect_defect_pattern")

    def test_contract_rejects_unbounded_action_and_duplicate_evidence_requirements(self) -> None:
        with self.assertRaises(InvestigationValidationError):
            InvestigationAction(
                action_id="ACT_BAD",
                kind="query_any_database_table",
                agent="planner",
                reason="Free-form action.",
            )
        with self.assertRaises(InvestigationValidationError):
            InvestigationGoal(
                goal_id="GOAL_BAD",
                intent=InvestigationIntent.ROOT_CAUSE.value,
                summary="Invalid duplicate requirements.",
                required_evidence=["shared_exposure", "shared_exposure"],
            )


if __name__ == "__main__":
    unittest.main()
