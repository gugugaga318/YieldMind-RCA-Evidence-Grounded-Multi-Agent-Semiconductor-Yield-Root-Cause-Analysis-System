from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentKind, FindingKind  # noqa: E402
from yield_rca_core.planner_agent import PlannerAgent  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


class TwoStageRAGContractTest(unittest.TestCase):
    def test_planner_builds_discovery_then_validation_knowledge_tasks(self) -> None:
        plan = PlannerAgent().plan(QUERY, plan_id="PLAN_BATCH_14")
        tasks = {task.task_id: task for task in plan.tasks}

        self.assertEqual(
            tasks["task_knowledge_discovery"].finding_kind,
            FindingKind.KNOWLEDGE_DISCOVERY.value,
        )
        self.assertEqual(
            tasks["task_knowledge_validation"].finding_kind,
            FindingKind.KNOWLEDGE_VALIDATION.value,
        )
        self.assertEqual(
            tasks["task_knowledge_validation"].depends_on,
            ["task_knowledge_discovery"],
        )
        self.assertIn("task_knowledge_validation", tasks["task_rca"].depends_on)

    def test_supervisor_records_two_stage_rag_without_changing_final_rca(self) -> None:
        state = build_csv_workflow(SEED_DIR).run(QUERY, job_id="JOB_BATCH_14")

        discovery = state.finding_for_task("task_knowledge_discovery")
        validation = state.finding_for_task("task_knowledge_validation")
        self.assertIsNotNone(discovery)
        self.assertIsNotNone(validation)
        assert discovery is not None
        assert validation is not None

        self.assertEqual(discovery.finding_kind, FindingKind.KNOWLEDGE_DISCOVERY.value)
        self.assertEqual(validation.finding_kind, FindingKind.KNOWLEDGE_VALIDATION.value)
        self.assertEqual(discovery.evidence_ids, ["EV_KNOWLEDGE_MATCH"])
        self.assertEqual(validation.evidence_ids, ["EV_KNOWLEDGE_VALIDATION_MATCH"])
        self.assertTrue(validation.details["preliminary_candidates"])
        self.assertEqual(
            validation.details["preliminary_candidates"][0]["root_cause"],
            EXPECTED_ROOT_CAUSE,
        )
        self.assertEqual(
            validation.details["validation_results"][0]["validation"],
            "supporting",
        )

        rca = state.findings_for_kind(
            FindingKind.HYPOTHESIS_RANKING.value,
            agent=AgentKind.RCA_REASONING.value,
        )[0]
        self.assertEqual(rca.details["root_cause"], EXPECTED_ROOT_CAUSE)
        self.assertIn("EV_KNOWLEDGE_VALIDATION_MATCH", rca.evidence_ids)
        self.assertEqual(state.hypotheses[0].root_cause, EXPECTED_ROOT_CAUSE)


if __name__ == "__main__":
    unittest.main()
