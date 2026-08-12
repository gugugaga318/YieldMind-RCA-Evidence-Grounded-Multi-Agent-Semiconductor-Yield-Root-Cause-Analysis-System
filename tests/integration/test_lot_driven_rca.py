from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_core.models import InvestigationMode, RCAState  # noqa: E402
from yield_rca_core.supervisor import SupervisorExecutionError  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
SOURCE_LOT = "LOT_A_001"
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


class LotDrivenRCAIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = build_csv_workflow(SEED_DIR)
        cls.state = cls.workflow.run(
            f"Analyze abnormal Lot {SOURCE_LOT} and identify impact Lots.",
            job_id="JOB_LOT_DRIVEN_GOLDEN",
            plan_id="PLAN_LOT_DRIVEN_GOLDEN",
            lot_id=SOURCE_LOT,
        )

    def test_lot_context_and_impact_scope_are_derived(self) -> None:
        self.assertEqual(self.state.job.investigation_mode, InvestigationMode.LOT.value)
        self.assertEqual(self.state.job.source_lot_id, SOURCE_LOT)
        self.assertEqual(self.state.job.product_id, "40N_SOC")
        self.assertEqual(len(self.state.affected_lots), 20)
        self.assertEqual(len(self.state.impact_lots), 19)
        self.assertNotIn(SOURCE_LOT, self.state.impact_lots)
        self.assertEqual(self.state.impact_lots[0], "LOT_A_002")
        self.assertEqual(self.state.impact_lots[-1], "LOT_A_020")
        self.assertEqual(self.state.impact_criteria["operation_no"], "6400")
        self.assertEqual(self.state.impact_criteria["equipment_id"], "CMP_CU03")
        self.assertEqual(self.state.impact_criteria["chamber_id"], "CMP_CU03_CH02")

    def test_lot_driven_rca_generates_traceable_conclusion_and_report(self) -> None:
        self.assertEqual(self.state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)
        evidence_ids = {item.evidence_id for item in self.state.evidence}
        self.assertTrue(
            {
                "EV_MES_SOURCE_LOT_CONTEXT",
                "EV_WAT_SOURCE_LOT_ANOMALY",
                "EV_FDC_EXCURSION_WINDOW",
                "EV_MES_IMPACT_LOTS",
                "EV_FDC_SLURRY_FLOW",
            }
            <= evidence_ids
        )
        self.assertIsNotNone(self.state.report)
        assert self.state.report is not None
        self.assertIn("## Lot Investigation Scope", self.state.report.markdown)
        self.assertIn("Impact Lot Count: 19", self.state.report.markdown)
        self.assertIn(EXPECTED_ROOT_CAUSE, self.state.report.markdown)
        self.assertTrue(set(self.state.report.cited_evidence_ids) <= evidence_ids)

    def test_lot_state_round_trip_preserves_scope(self) -> None:
        self.assertEqual(RCAState.from_dict(self.state.to_dict()), self.state)

    def test_unknown_lot_has_explicit_error_code(self) -> None:
        with self.assertRaises(SupervisorExecutionError) as raised:
            self.workflow.run(
                "Analyze unknown abnormal Lot.",
                job_id="JOB_UNKNOWN_LOT",
                lot_id="LOT_UNKNOWN_999",
            )

        self.assertEqual(raised.exception.error_code, "LOT_NOT_FOUND")

    def test_http_api_accepts_lot_only_request_and_returns_report(self) -> None:
        app = create_app(workflow=build_csv_workflow(SEED_DIR), execute_jobs_inline=True)
        with TestClient(app) as client:
            create_response = client.post(
                "/rca/jobs",
                json={"investigation_mode": "lot", "lot_id": SOURCE_LOT},
            )
            self.assertEqual(create_response.status_code, 201)
            created = create_response.json()
            self.assertEqual(created["investigation_mode"], "lot")
            self.assertEqual(created["source_lot_id"], SOURCE_LOT)

            state_response = client.get(created["state_url"])
            report_response = client.get(created["report_url"])

        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(report_response.status_code, 200)
        state = RCAState.from_dict(state_response.json()["state"])
        self.assertEqual(len(state.impact_lots), 19)
        self.assertIn(EXPECTED_ROOT_CAUSE, report_response.json()["report"]["markdown"])

    def test_http_api_returns_not_found_for_unknown_lot(self) -> None:
        app = create_app(workflow=build_csv_workflow(SEED_DIR), execute_jobs_inline=True)
        with TestClient(app) as client:
            response = client.post(
                "/rca/jobs",
                json={"investigation_mode": "lot", "lot_id": "LOT_UNKNOWN_999"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["error_code"], "LOT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
