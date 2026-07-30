from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import (  # noqa: E402
    HypothesisStatus,
    InvestigationMode,
    RCAState,
    TaskStatus,
)
from yield_rca_core.workflow import PurePythonRCAWorkflow, build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def case_catalog() -> list[dict[str, object]]:
    payload = json.loads((SEED_DIR / "case_catalog.json").read_text(encoding="utf-8"))
    return list(payload["cases"])


class MultiCaseWorkflowIntegrationTest(unittest.TestCase):
    workflow: ClassVar[PurePythonRCAWorkflow]
    cases: ClassVar[list[dict[str, object]]]
    states: ClassVar[dict[str, RCAState]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = build_csv_workflow(SEED_DIR)
        cls.cases = case_catalog()
        cls.states = {
            str(case["case_id"]): cls.workflow.run(
                str(case["query"]),
                job_id=f"JOB_LOT_{case['case_id']}",
                lot_id=str(case["source_lot_id"]),
            )
            for case in cls.cases
        }

    def test_all_cases_complete_with_expected_scope_and_traceability(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                state = self.states[str(case["case_id"])]
                hypothesis = state.hypotheses[-1]
                evidence_ids = {item.evidence_id for item in state.evidence}

                self.assertEqual(state.job.status, TaskStatus.COMPLETED.value)
                self.assertEqual(state.job.investigation_mode, InvestigationMode.LOT.value)
                self.assertEqual(state.job.source_lot_id, case["source_lot_id"])
                self.assertEqual(state.affected_lots, case["expected_scope_lots"])
                self.assertEqual(state.impact_lots, case["expected_impact_lots"])
                self.assertEqual(state.scope_level, case["expected_scope_level"])
                self.assertEqual(hypothesis.status, case["expected_status"])
                self.assertEqual(hypothesis.root_cause, case["root_cause"])
                expected_evidence_ids = {
                    str(item) for item in cast(list[object], case["expected_evidence_ids"])
                }
                self.assertTrue(expected_evidence_ids <= evidence_ids)
                self.assertIsNotNone(state.report)
                assert state.report is not None
                self.assertIn(str(case["root_cause"]), state.report.markdown)
                self.assertTrue(set(state.report.cited_evidence_ids) <= evidence_ids)

    def test_cu_window_has_one_ooc_and_four_impact_lots(self) -> None:
        state = self.states["CASE_CU_SLURRY_WINDOW"]
        fdc = next(item for item in state.findings if item.agent == "fdc")

        self.assertEqual(state.impact_lots, [f"LOT_A_{number:03d}" for number in range(11, 15)])
        self.assertEqual(fdc.details["event_count"], 1)
        self.assertEqual(fdc.details["spc_ooc_parameter_count"], 3)
        self.assertGreater(fdc.details["spc_point_violation_count"], 0)
        self.assertEqual(state.hypotheses[-1].confidence, 0.95)
        assert state.report is not None
        self.assertIn("Impact Lot Count: 4", state.report.markdown)
        self.assertIn("## Minimal SPC Analysis", state.report.markdown)
        self.assertIn("EV_SPC_SLURRY_FLOW", state.report.markdown)

    def test_isolated_scratch_remains_inconclusive_without_impact_lots(self) -> None:
        state = self.states["CASE_ISOLATED_WAFER_SCRATCH"]

        self.assertEqual(state.affected_wafers, ["LOT_A_038_W07"])
        self.assertEqual(state.impact_wafers, [])
        self.assertEqual(state.impact_lots, [])
        self.assertLessEqual(state.hypotheses[-1].confidence, 0.60)
        self.assertEqual(state.hypotheses[-1].status, HypothesisStatus.INCONCLUSIVE.value)
        fdc = next(item for item in state.findings if item.agent == "fdc")
        self.assertEqual(fdc.details["event_count"], 0)
        self.assertEqual(fdc.details["spc_ooc_parameter_count"], 0)
        self.assertTrue(
            all(item["status"] == "IN_CONTROL" for item in fdc.details["spc_results"])
        )
        self.assertTrue(
            all(item["avg_delta_percent"] > -5.0 for item in fdc.details["parameter_summary"])
        )

    def test_thin_film_case_resolves_even_wafers_and_excludes_cmp(self) -> None:
        case = next(item for item in self.cases if item["case_id"] == "CASE_ILD_ODD_EVEN_THICKNESS")
        state = self.states["CASE_ILD_ODD_EVEN_THICKNESS"]
        mes = next(item for item in state.findings if item.agent == "mes")

        self.assertEqual(state.affected_wafers, case["expected_affected_wafers"])
        self.assertEqual(state.impact_wafers, case["expected_impact_wafers"])
        self.assertEqual(mes.details["target_operation_no"], "5000")
        self.assertEqual(mes.details["target_commonality"]["chamber_id"], "CVD_ILD_01_CH02")
        self.assertGreaterEqual(state.hypotheses[-1].confidence, 0.85)
        evidence_ids = {item.evidence_id for item in state.evidence}
        self.assertIn("EV_FDC_CMP_NORMAL_EXCLUSION", evidence_ids)
        fdc = next(item for item in state.findings if item.agent == "fdc")
        self.assertEqual(fdc.details["spc_ooc_parameter_count"], 2)
        assert state.report is not None
        self.assertIn("Affected Wafer Count: 12", state.report.markdown)

    def test_product_window_still_resolves_cu_yield_excursion(self) -> None:
        state = self.workflow.run(
            "Analyze 40N_SOC yield drop from 2026-07-01 to 2026-07-07.",
            job_id="JOB_PRODUCT_CU_WINDOW",
        )
        mes = next(item for item in state.findings if item.agent == "mes")

        self.assertEqual(state.job.investigation_mode, InvestigationMode.PRODUCT_WINDOW.value)
        self.assertEqual(state.affected_lots, [f"LOT_A_{number:03d}" for number in range(12, 16)])
        self.assertEqual(
            mes.details["passing_suspect_lots"],
            ["LOT_A_011"],
        )
        self.assertEqual(
            state.hypotheses[-1].root_cause,
            "CMP_CU03_CH02 slurry delivery degradation",
        )


if __name__ == "__main__":
    unittest.main()
