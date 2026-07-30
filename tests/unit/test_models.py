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
    ToolInput,
    ToolOutput,
    Warning,
)


def make_evidence(evidence_id: str = "ev_mes_001") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type=EvidenceSourceType.MES.value,
        source_id="process_history:1",
        source_table="process_history",
        source_field="equipment_id",
        summary="Affected lots concentrate on CMP_CU03_CH02.",
        metadata={"lot_count": 20},
    )


def make_plan() -> TaskPlan:
    return TaskPlan(
        plan_id="plan_001",
        objective="Analyze 40N_SOC July yield drop.",
        tasks=[
            AgentTask(
                task_id="task_mes",
                agent=AgentKind.MES.value,
                objective="Analyze lot genealogy.",
            ),
            AgentTask(
                task_id="task_fdc",
                agent=AgentKind.FDC.value,
                objective="Analyze FDC feature drift.",
                depends_on=["task_mes"],
            ),
            AgentTask(
                task_id="task_rca",
                agent=AgentKind.RCA_REASONING.value,
                objective="Fuse evidence and produce root cause.",
                depends_on=["task_mes", "task_fdc"],
            ),
        ],
    )


class ModelSerializationTest(unittest.TestCase):
    def test_rca_state_round_trip(self) -> None:
        evidence = make_evidence()
        state = RCAState(
            job=RCAJob(
                job_id="job_001",
                user_query="Analyze July yield drop for 40N_SOC.",
                product_id="40N_SOC",
                time_window={"start": "2026-07-01", "end": "2026-07-31"},
            ),
            task_plan=make_plan(),
            current_task_id="task_rca",
            completed_task_ids=["task_mes", "task_fdc"],
            affected_lots=["LOT001", "LOT002"],
            evidence=[evidence],
            findings=[
                AgentFinding(
                    finding_id="finding_mes_001",
                    agent=AgentKind.MES.value,
                    summary="MES commonality points to CMP_CU03_CH02.",
                    confidence=0.9,
                    evidence_ids=[evidence.evidence_id],
                )
            ],
            hypotheses=[
                Hypothesis(
                    hypothesis_id="hyp_001",
                    root_cause="CMP_CU03_CH02 slurry delivery degradation",
                    confidence=0.88,
                    evidence_ids=[evidence.evidence_id],
                    rationale="MES commonality supports the hypothesis.",
                )
            ],
            report=Report(
                report_id="report_001",
                title="Yield RCA Report",
                markdown="# Yield RCA Report",
                cited_evidence_ids=[evidence.evidence_id],
            ),
        )

        restored = RCAState.from_dict(state.to_dict())

        self.assertEqual(restored.to_dict(), state.to_dict())

    def test_tool_input_and_output_round_trip(self) -> None:
        tool_input = ToolInput(
            tool_name="analyze_lot_genealogy",
            request_id="req_001",
            requested_by=AgentKind.MES.value,
            parameters={"lots": ["LOT001"]},
        )
        tool_output = ToolOutput(
            tool_name=tool_input.tool_name,
            request_id=tool_input.request_id,
            success=True,
            data={"common_chamber": "CMP_CU03_CH02"},
            evidence_ids=["ev_mes_001"],
            warnings=[Warning(warning_id="warn_001", message="Small sample size.")],
        )

        self.assertEqual(ToolInput.from_dict(tool_input.to_dict()).to_dict(), tool_input.to_dict())
        self.assertEqual(
            ToolOutput.from_dict(tool_output.to_dict()).to_dict(),
            tool_output.to_dict(),
        )


class ModelValidationTest(unittest.TestCase):
    def test_invalid_confidence_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            AgentFinding(
                finding_id="finding_bad",
                agent=AgentKind.MES.value,
                summary="Invalid confidence.",
                confidence=1.2,
                evidence_ids=["ev_001"],
            )

    def test_agent_finding_requires_evidence_ids(self) -> None:
        with self.assertRaises(ModelValidationError):
            AgentFinding(
                finding_id="finding_no_evidence",
                agent=AgentKind.MES.value,
                summary="No evidence.",
                confidence=0.5,
                evidence_ids=[],
            )

    def test_unknown_agent_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            AgentTask(
                task_id="task_unknown",
                agent="freeform_agent",
                objective="Do something undefined.",
            )

    def test_failed_tool_output_requires_error_code(self) -> None:
        with self.assertRaises(ModelValidationError):
            ToolOutput(
                tool_name="retrieve_similar_case",
                request_id="req_001",
                success=False,
                data={},
            )


if __name__ == "__main__":
    unittest.main()

