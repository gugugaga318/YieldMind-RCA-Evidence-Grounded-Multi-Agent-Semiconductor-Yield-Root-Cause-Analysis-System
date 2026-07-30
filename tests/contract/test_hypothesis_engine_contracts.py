from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentKind, FindingKind, Hypothesis  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


class HypothesisEngineContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = build_csv_workflow(SEED_DIR).run(QUERY, job_id="JOB_BATCH_19")
        cls.rca = cls.state.findings_for_kind(
            FindingKind.HYPOTHESIS_RANKING.value,
            agent=AgentKind.RCA_REASONING.value,
        )[0]

    def test_hypothesis_v1_is_the_only_active_engine(self) -> None:
        self.assertEqual(self.state.execution_metadata["reasoning_engine"], "hypothesis_v1")
        self.assertEqual(self.state.execution_metadata["hypothesis_engine_mode"], "active")
        self.assertEqual(self.rca.details["reasoning_engine"], "hypothesis_v1")
        self.assertNotIn("hypothesis_engine_shadow", self.rca.details)
        self.assertNotIn("legacy_ranked_candidates", self.rca.details)
        self.assertEqual(self.state.hypotheses[0].root_cause, EXPECTED_ROOT_CAUSE)

    def test_active_hypothesis_retains_evidence_roles_and_validation(self) -> None:
        hypothesis = self.state.hypotheses[0]
        self.assertEqual(hypothesis.rank, 1)
        self.assertIn("EV_KNOWLEDGE_VALIDATION_MATCH", hypothesis.supporting_evidence_ids)
        known_evidence_ids = {item.evidence_id for item in self.state.evidence}
        self.assertTrue(set(hypothesis.evidence_ids) <= known_evidence_ids)

    def test_legacy_hypothesis_snapshot_remains_readable(self) -> None:
        legacy = Hypothesis.from_dict(
            {
                "hypothesis_id": "HISTORICAL_LEGACY_HYPOTHESIS",
                "root_cause": "Historical recorded root cause",
                "confidence": 0.82,
                "evidence_ids": ["EV_HISTORICAL_001"],
                "status": "supported",
                "rationale": "Retained snapshot.",
            }
        )
        self.assertEqual(legacy.root_cause, "Historical recorded root cause")
        self.assertEqual(legacy.supporting_evidence_ids, [])


if __name__ == "__main__":
    unittest.main()
