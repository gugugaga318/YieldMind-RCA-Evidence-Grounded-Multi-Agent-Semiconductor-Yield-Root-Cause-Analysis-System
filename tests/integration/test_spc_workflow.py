from __future__ import annotations

import unittest
from pathlib import Path
from typing import ClassVar

from yield_rca_core.models import HypothesisStatus, RCAState
from yield_rca_core.workflow import build_csv_workflow

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seeds" / "spc_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


class SpcWorkflowIntegrationTest(unittest.TestCase):
    state: ClassVar[RCAState]
    lot_state: ClassVar[RCAState]

    @classmethod
    def setUpClass(cls) -> None:
        workflow = build_csv_workflow(SEED_DIR)
        cls.state = workflow.run(QUERY, job_id="JOB_SPC_INTEGRATION")
        cls.lot_state = workflow.run(
            "Analyze abnormal Lot LOT_A_015 and identify impact Lots.",
            job_id="JOB_SPC_LOT_INTEGRATION",
            lot_id="LOT_A_015",
        )

    def test_advanced_spc_evidence_reaches_rca_and_report(self) -> None:
        fdc = next(item for item in self.state.findings if item.agent == "fdc")
        evidence_ids = {item.evidence_id for item in self.state.evidence}

        self.assertEqual(fdc.details["spc_method"]["engine"], "deterministic_advanced_spc")
        self.assertEqual(fdc.details["spc_analyzed_parameter_count"], 5)
        self.assertGreaterEqual(fdc.details["spc_ooc_parameter_count"], 1)
        self.assertIn("EV_SPC_OOC_CONTEXT", evidence_ids)
        self.assertEqual(
            self.state.hypotheses[-1].root_cause,
            "CMP_CU03_CH02 slurry delivery degradation",
        )
        assert self.state.report is not None
        self.assertIn("## SPC Evidence", self.state.report.markdown)
        self.assertIn("Trigger Lot: `LOT_A_015`", self.state.report.markdown)
        self.assertIn("`LOT_A_011` -> `HOLD_CU_IMPACT_011`", self.state.report.markdown)

        slurry = next(
            item
            for item in fdc.details["spc_results"]
            if item["parameter_name"] == "slurry_flow"
        )
        self.assertEqual(
            {item["rule_code"] for item in slurry["violations"]},
            {"NELSON_1", "NELSON_5", "NELSON_6"},
        )
        self.assertTrue(
            all(
                item["sample_ids"][-1] == "FDC:LOT_A_015:LOT_MEAN:slurry_flow"
                for item in slurry["violations"]
            )
        )

    def test_ooc_context_distinguishes_trigger_and_impact_lots(self) -> None:
        fdc = next(item for item in self.state.findings if item.agent == "fdc")
        context = fdc.details["spc_ooc_contexts"][0]

        self.assertEqual(context["trigger_lot_id"], "LOT_A_015")
        self.assertIsNone(context["trigger_wafer_id"])
        self.assertEqual(context["trigger_hold"]["hold_id"], "HOLD_CU_OOC_001")
        self.assertEqual(
            [item["lot_id"] for item in context["impact_scopes"]],
            [f"LOT_A_{number:03d}" for number in range(11, 15)],
        )

    def test_trigger_lot_resolves_to_supported_rca(self) -> None:
        hypothesis = self.lot_state.hypotheses[-1]

        self.assertEqual(self.lot_state.job.source_lot_id, "LOT_A_015")
        self.assertEqual(
            self.lot_state.impact_lots,
            [f"LOT_A_{number:03d}" for number in range(11, 15)],
        )
        self.assertEqual(hypothesis.status, HypothesisStatus.SUPPORTED.value)
        self.assertEqual(
            hypothesis.root_cause,
            "CMP_CU03_CH02 slurry delivery degradation",
        )
        self.assertNotIn(
            "WARN_RCA_INCONCLUSIVE",
            {warning.warning_id for warning in self.lot_state.warnings},
        )


if __name__ == "__main__":
    unittest.main()
