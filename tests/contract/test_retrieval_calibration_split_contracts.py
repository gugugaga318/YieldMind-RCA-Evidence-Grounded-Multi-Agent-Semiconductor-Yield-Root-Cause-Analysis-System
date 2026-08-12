from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.retrieval_evaluation import RetrievalGroundTruth  # noqa: E402

GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
SPLIT = ROOT / "data" / "evaluation" / "retrieval_calibration_split.json"


class RetrievalCalibrationSplitContractTest(unittest.TestCase):
    def test_fixed_calibration_partition_is_disjoint_and_stratified(self) -> None:
        ground_truth = RetrievalGroundTruth.load(GROUND_TRUTH)
        payload = json.loads(SPLIT.read_text(encoding="utf-8"))
        calibration_ids = set(payload["calibration_query_ids"])
        all_ids = {item.query_id for item in ground_truth.queries}
        calibration = ground_truth.subset(calibration_ids)
        test = ground_truth.subset(all_ids - calibration_ids)

        self.assertEqual(len(calibration.queries), 18)
        self.assertEqual(len(test.queries), 96)
        self.assertFalse(
            {item.query_id for item in calibration.queries}
            & {item.query_id for item in test.queries}
        )
        self.assertEqual(
            {item.question_kind for item in calibration.queries},
            {
                "historical_match",
                "procedure_guidance",
                "engineering_note_lookup",
            },
        )
        self.assertTrue(any(item.no_answer for item in calibration.queries))
        self.assertTrue(any(item.cross_language for item in calibration.queries))


if __name__ == "__main__":
    unittest.main()
