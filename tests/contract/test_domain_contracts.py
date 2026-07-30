from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    AgentFinding,
    AgentKind,
    AgentTask,
    Evidence,
    EvidenceSourceType,
    Hypothesis,
    ModelValidationError,
    RCAJob,
    RCAState,
    Report,
    TaskPlan,
)


class TaskPlanContractTest(unittest.TestCase):
    def test_task_plan_rejects_duplicate_task_ids(self) -> None:
        with self.assertRaises(ModelValidationError):
            TaskPlan(
                plan_id="plan_duplicate",
                objective="duplicate",
                tasks=[
                    AgentTask(task_id="task_a", agent=AgentKind.MES.value, objective="A"),
                    AgentTask(task_id="task_a", agent=AgentKind.FDC.value, objective="B"),
                ],
            )

    def test_task_plan_rejects_unknown_dependencies(self) -> None:
        with self.assertRaises(ModelValidationError):
            TaskPlan(
                plan_id="plan_missing_dep",
                objective="missing dep",
                tasks=[
                    AgentTask(
                        task_id="task_a",
                        agent=AgentKind.MES.value,
                        objective="A",
                        depends_on=["task_missing"],
                    )
                ],
            )

    def test_task_plan_rejects_cycles(self) -> None:
        with self.assertRaises(ModelValidationError):
            TaskPlan(
                plan_id="plan_cycle",
                objective="cycle",
                tasks=[
                    AgentTask(
                        task_id="task_a",
                        agent=AgentKind.MES.value,
                        objective="A",
                        depends_on=["task_b"],
                    ),
                    AgentTask(
                        task_id="task_b",
                        agent=AgentKind.FDC.value,
                        objective="B",
                        depends_on=["task_a"],
                    ),
                ],
            )


class EvidenceReferenceContractTest(unittest.TestCase):
    def test_rca_state_rejects_duplicate_evidence_ids(self) -> None:
        evidence = Evidence(
            evidence_id="ev_001",
            source_type=EvidenceSourceType.MES.value,
            source_id="process_history:1",
            summary="Evidence",
        )

        with self.assertRaises(ModelValidationError):
            RCAState(
                job=RCAJob(job_id="job_001", user_query="Analyze yield drop."),
                evidence=[evidence, evidence],
            )

    def test_rca_state_rejects_unknown_finding_evidence(self) -> None:
        with self.assertRaises(ModelValidationError):
            RCAState(
                job=RCAJob(job_id="job_001", user_query="Analyze yield drop."),
                findings=[
                    AgentFinding(
                        finding_id="finding_001",
                        agent=AgentKind.MES.value,
                        summary="Finding references missing evidence.",
                        confidence=0.5,
                        evidence_ids=["missing_evidence"],
                    )
                ],
            )

    def test_rca_state_rejects_unknown_hypothesis_evidence(self) -> None:
        evidence = Evidence(
            evidence_id="ev_001",
            source_type=EvidenceSourceType.FDC.value,
            source_id="fdc_feature:1",
            summary="FDC drift.",
        )

        with self.assertRaises(ModelValidationError):
            RCAState(
                job=RCAJob(job_id="job_001", user_query="Analyze yield drop."),
                evidence=[evidence],
                hypotheses=[
                    Hypothesis(
                        hypothesis_id="hyp_001",
                        root_cause="CMP issue",
                        confidence=0.8,
                        evidence_ids=["ev_missing"],
                    )
                ],
            )

    def test_rca_state_rejects_unknown_report_evidence(self) -> None:
        with self.assertRaises(ModelValidationError):
            RCAState(
                job=RCAJob(job_id="job_001", user_query="Analyze yield drop."),
                report=Report(
                    report_id="report_001",
                    title="Report",
                    markdown="# Report",
                    cited_evidence_ids=["missing"],
                ),
            )


if __name__ == "__main__":
    unittest.main()

