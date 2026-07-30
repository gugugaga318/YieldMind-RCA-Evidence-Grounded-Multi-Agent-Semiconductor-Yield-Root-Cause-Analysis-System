from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import (  # noqa: E402
    InvestigationGoal,
    InvestigationIntent,
)
from yield_rca_core.models import InvestigationMode, RCAJob  # noqa: E402
from yield_rca_core.tool_layer import capture_tool_latencies  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402


class ControlledReactWorkflowIntegrationTest(unittest.TestCase):
    def test_chinese_scratch_and_cu_cmp_clues_are_preserved_in_the_goal(self) -> None:
        workflow = build_csv_workflow(ROOT / "data" / "seeds" / "golden_case")
        goal = workflow.planner.plan_investigation_goal(
            "调查 LOT_A_001 在铜 CMP 发现的划伤，分析根因和影响批次。",
            lot_id="LOT_A_001",
        )

        self.assertEqual(goal.intent, InvestigationIntent.ROOT_CAUSE.value)
        self.assertEqual(goal.known_facts["lot_id"], "LOT_A_001")
        self.assertEqual(goal.known_facts["defect"], "scratch")
        self.assertEqual(goal.known_facts["module"], "CU_CMP")

    def test_known_scratch_lot_follows_bounded_defect_mes_fdc_rca_path(self) -> None:
        workflow = build_csv_workflow(ROOT / "data" / "seeds" / "golden_case")
        state = workflow.supervisor.execute_controlled(
            RCAJob(
                job_id="JOB_CONTROLLED_REACT_SCRATCH",
                user_query="Investigate LOT_A_001 scratch in Cu CMP.",
                investigation_mode=InvestigationMode.LOT.value,
                source_lot_id="LOT_A_001",
            ),
            InvestigationGoal(
                goal_id="GOAL_CONTROLLED_REACT_SCRATCH",
                intent=InvestigationIntent.ROOT_CAUSE.value,
                summary="Investigate the known Cu CMP scratch and root cause.",
                known_facts={"lot_id": "LOT_A_001", "defect": "scratch", "module": "CU"},
            ),
        )
        self.assertEqual(
            [record.action.kind for record in state.action_history],
            [
                "inspect_defect_pattern",
                "find_shared_exposure",
                "validate_shared_defect_pattern",
                "inspect_fdc_spc",
                "run_rca_reasoning",
            ],
        )
        self.assertEqual(state.goal_status, "satisfied")
        self.assertEqual(state.conclusion_level, "supported")
        self.assertEqual(state.stop_reason, "goal_satisfied")
        self.assertEqual(state.impact_criteria["operation_no"], "6400")
        self.assertEqual(state.impact_criteria["equipment_id"], "CMP_CU03")
        self.assertEqual(state.impact_criteria["chamber_id"], "CMP_CU03_CH02")
        self.assertEqual(len(state.impact_wafers), 19)
        self.assertIn(
            "EV_DEFECT_SCRATCH_SHARED_EXPOSURE",
            {item.evidence_id for item in state.evidence},
        )
        self.assertEqual(state.execution_metadata, {})
        self.assertIsNotNone(state.report)

    def test_controlled_fallback_stops_before_an_action_would_cross_tool_budget(
        self,
    ) -> None:
        workflow = build_csv_workflow(ROOT / "data" / "seeds" / "golden_case")
        with capture_tool_latencies() as tool_latencies:
            state = workflow.supervisor.execute_controlled(
                RCAJob(
                    job_id="JOB_CONTROLLED_REACT_BUDGET",
                    user_query="Investigate LOT_A_001 scratch in Cu CMP.",
                    investigation_mode=InvestigationMode.LOT.value,
                    source_lot_id="LOT_A_001",
                ),
                InvestigationGoal(
                    goal_id="GOAL_CONTROLLED_REACT_BUDGET",
                    intent=InvestigationIntent.ROOT_CAUSE.value,
                    summary="Respect the global Tool budget while investigating.",
                    known_facts={"lot_id": "LOT_A_001", "defect": "scratch"},
                    max_tool_calls=2,
                ),
                tool_latencies=tool_latencies,
            )

        self.assertEqual(
            [record.action.kind for record in state.action_history],
            ["inspect_defect_pattern"],
        )
        self.assertEqual(len(tool_latencies), 1)
        self.assertEqual(state.goal_status, "budget_exhausted")
        self.assertEqual(state.stop_reason, "budget_exhausted")


if __name__ == "__main__":
    unittest.main()
