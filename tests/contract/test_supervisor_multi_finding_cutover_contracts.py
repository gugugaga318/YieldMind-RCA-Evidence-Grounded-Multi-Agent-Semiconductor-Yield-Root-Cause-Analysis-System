from __future__ import annotations

import inspect
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import (  # noqa: E402
    AgentFinding,
    AgentKind,
    AgentTask,
    FindingKind,
    ModelValidationError,
    RCAJob,
    RCAState,
    TaskPlan,
)
from yield_rca_core.planner_agent import PlannerAgent  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


class SupervisorMultiFindingCutoverContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = build_csv_workflow(SEED_DIR)

    def test_supervisor_records_task_identity_and_finding_kind(self) -> None:
        state = self.workflow.run(QUERY, job_id="JOB_BATCH_11_IDENTITIES")

        self.assertEqual(
            [finding.task_id for finding in state.findings],
            state.completed_task_ids,
        )
        for finding in state.findings:
            self.assertIs(state.finding_for_task(str(finding.task_id)), finding)
            task = next(
                item for item in state.task_plan.tasks if item.task_id == finding.task_id
            )
            self.assertEqual(finding.agent, task.agent)
            self.assertEqual(finding.finding_kind, task.finding_kind)
        rca_finding = next(
            finding for finding in state.findings if finding.agent == AgentKind.RCA_REASONING.value
        )
        self.assertEqual(state.authoritative_rca_finding_id, rca_finding.finding_id)
        self.assertEqual(
            state.authoritative_hypothesis_id,
            state.hypotheses[0].hypothesis_id,
        )

    def test_same_agent_can_record_findings_for_multiple_tasks(self) -> None:
        original = PlannerAgent().plan(QUERY, plan_id="PLAN_BATCH_11_MULTI")
        job = RCAJob(
            job_id="JOB_BATCH_11_MULTI",
            user_query=QUERY,
            product_id="40N_SOC",
            time_window={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )

        state = self.workflow.supervisor.execute(job, original)

        knowledge_findings = state.findings_for_agent(AgentKind.KNOWLEDGE.value)
        self.assertEqual(len(knowledge_findings), 2)
        self.assertEqual(
            {item.task_id for item in knowledge_findings},
            {"task_knowledge_discovery", "task_knowledge_validation"},
        )
        self.assertEqual(
            state.findings_for_kind(
                FindingKind.KNOWLEDGE_VALIDATION.value,
                agent=AgentKind.KNOWLEDGE.value,
            ),
            [state.finding_for_task("task_knowledge_validation")],
        )
        self.assertEqual(
            state.hypotheses[0].root_cause,
            "CMP_CU03_CH02 slurry delivery degradation",
        )

    def test_supervisor_merges_only_first_class_finding_evidence(self) -> None:
        task = AgentTask(
            task_id="task_mes",
            agent=AgentKind.MES.value,
            objective="Collect MES evidence.",
            inputs={"product_id": "40N_SOC"},
        )
        plan = TaskPlan(
            plan_id="PLAN_BATCH_11_FIRST_CLASS",
            objective=task.objective,
            tasks=[task],
        )
        state = RCAState(
            job=RCAJob(
                job_id="JOB_BATCH_11_FIRST_CLASS",
                user_query=QUERY,
                product_id="40N_SOC",
            ),
            task_plan=plan,
        )
        finding = self.workflow.supervisor.mes_agent.analyze(
            request_id="REQ_BATCH_11_FIRST_CLASS",
            product_id="40N_SOC",
        )
        original_evidence = list(finding.evidence)
        finding.details.pop("evidence")

        recorded = self.workflow.supervisor._record_finding(state, task, finding)

        self.assertEqual(recorded.evidence, original_evidence)
        self.assertTrue(
            all(
                actual is expected
                for actual, expected in zip(recorded.evidence, original_evidence, strict=True)
            )
        )

    def test_rca_state_indexes_are_derived_and_not_serialized(self) -> None:
        state = self.workflow.run(QUERY, job_id="JOB_BATCH_11_INDEXES")

        self.assertEqual(list(state.evidence_by_id), [item.evidence_id for item in state.evidence])
        for evidence in state.evidence:
            self.assertIn(evidence, state.evidence_by_type[evidence.evidence_type])
            for entity in evidence.entities:
                self.assertIn(
                    evidence,
                    state.evidence_by_entity[(entity.entity_type, entity.entity_id)],
                )
        serialized = state.to_dict()
        self.assertNotIn("evidence_by_id", serialized)
        self.assertNotIn("evidence_by_type", serialized)
        self.assertNotIn("evidence_by_entity", serialized)

    def test_legacy_finding_and_task_payloads_remain_readable(self) -> None:
        legacy_task = AgentTask.from_dict(
            {
                "task_id": "task_knowledge",
                "agent": AgentKind.KNOWLEDGE.value,
                "objective": "Retrieve knowledge.",
            }
        )
        legacy_finding = AgentFinding.from_dict(
            {
                "finding_id": "finding_knowledge",
                "agent": AgentKind.KNOWLEDGE.value,
                "summary": "Historical match.",
                "confidence": 0.5,
                "evidence_ids": ["EV_KNOWLEDGE"],
            }
        )

        self.assertEqual(legacy_task.finding_kind, FindingKind.KNOWLEDGE_DISCOVERY.value)
        self.assertIsNone(legacy_finding.task_id)
        self.assertEqual(
            legacy_finding.finding_kind,
            FindingKind.KNOWLEDGE_DISCOVERY.value,
        )

    def test_rca_state_rejects_more_than_one_finding_for_the_same_task(self) -> None:
        task = AgentTask(
            task_id="task_mes",
            agent=AgentKind.MES.value,
            objective="Collect MES evidence.",
        )
        finding = AgentFinding(
            finding_id="finding_one",
            task_id=task.task_id,
            agent=task.agent,
            finding_kind=task.finding_kind,
            summary="First finding.",
            confidence=0.5,
            evidence_ids=["EV_MES"],
        )

        with self.assertRaisesRegex(
            ModelValidationError,
            "multiple findings reference the same task_id",
        ):
            RCAState(
                job=RCAJob(job_id="JOB_DUPLICATE_TASK", user_query=QUERY),
                task_plan=TaskPlan(
                    plan_id="PLAN_DUPLICATE_TASK",
                    objective=task.objective,
                    tasks=[task],
                ),
                findings=[finding, replace(finding, finding_id="finding_two")],
            )

    def test_supervisor_has_no_legacy_evidence_or_agent_unique_lookup(self) -> None:
        import yield_rca_core.supervisor as supervisor

        source = inspect.getsource(supervisor)
        self.assertNotIn('details.get("evidence"', source)
        self.assertNotIn('details["evidence"]', source)
        self.assertNotIn("Evidence.from_dict", source)
        self.assertNotIn("def _finding_for(state", source)


if __name__ == "__main__":
    unittest.main()
