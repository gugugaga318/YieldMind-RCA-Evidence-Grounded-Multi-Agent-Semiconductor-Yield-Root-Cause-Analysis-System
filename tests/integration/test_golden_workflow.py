from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentKind, RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


def seed_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(SEED_DIR.iterdir())
        if path.is_file()
    }


class GoldenWorkflowIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = build_csv_workflow(SEED_DIR).run(
            QUERY,
            job_id="JOB_GOLDEN_INTEGRATION",
            plan_id="PLAN_GOLDEN_INTEGRATION",
        )

    def test_workflow_runs_golden_case_end_to_end(self) -> None:
        self.assertEqual(self.state.job.status, TaskStatus.COMPLETED.value)
        self.assertEqual(len(self.state.completed_task_ids), 7)
        self.assertEqual(len(self.state.findings), 7)
        self.assertEqual(self.state.findings[-1].agent, AgentKind.IMPROVEMENT.value)
        self.assertEqual(self.state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)
        self.assertEqual(self.state.hypotheses[-1].confidence, 0.95)
        self.assertIsNotNone(self.state.report)
        self.assertIn(EXPECTED_ROOT_CAUSE, self.state.report.markdown)
        self.assertIn("## Minimal SPC Analysis", self.state.report.markdown)
        self.assertIn("Control limits could not be calculated", self.state.report.markdown)
        self.assertIn("EV_SPC_BASELINE_STATUS", self.state.report.markdown)
        self.assertIn("## Engineering Improvement Summary", self.state.report.markdown)
        self.assertIn("## Recipe Optimization Recommendations", self.state.report.markdown)
        self.assertIn("## Memory Status", self.state.report.markdown)
        self.assertIn("use the Memory Approval API", self.state.report.markdown)

    def test_final_state_round_trip_preserves_report_and_evidence(self) -> None:
        restored = RCAState.from_dict(self.state.to_dict())

        self.assertEqual(restored, self.state)
        self.assertTrue(restored.evidence)
        self.assertTrue(restored.report.cited_evidence_ids)

    def test_workflow_reads_but_does_not_modify_offline_seed(self) -> None:
        before = seed_hashes()

        build_csv_workflow(SEED_DIR).run(QUERY, job_id="JOB_SEED_IMMUTABILITY")

        self.assertEqual(seed_hashes(), before)

    def test_single_command_writes_state_and_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "golden_output"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_golden_rca.py"),
                    "--output-dir",
                    str(output_dir),
                    "--no-print-report",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            state_path = output_dir / "rca_state.json"
            report_path = output_dir / "rca_report.md"
            self.assertTrue(state_path.is_file())
            self.assertTrue(report_path.is_file())
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            state = RCAState.from_dict(payload)
            self.assertEqual(state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)
            self.assertIn(EXPECTED_ROOT_CAUSE, report_path.read_text(encoding="utf-8"))
            self.assertIn("Golden RCA workflow completed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
