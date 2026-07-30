from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import Evidence, EvidenceSourceType, RCAJob, RCAState  # noqa: E402


class ModelPerformanceBaselineTest(unittest.TestCase):
    def test_state_with_one_thousand_evidence_serializes_quickly(self) -> None:
        evidence = [
            Evidence(
                evidence_id=f"ev_{index:04d}",
                source_type=EvidenceSourceType.MES.value,
                source_id=f"process_history:{index}",
                summary=f"Evidence {index}",
            )
            for index in range(1000)
        ]
        state = RCAState(
            job=RCAJob(job_id="job_perf", user_query="Analyze yield drop."),
            evidence=evidence,
        )

        started = time.perf_counter()
        payload = state.to_dict()
        restored = RCAState.from_dict(payload)
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.assertEqual(len(restored.evidence), 1000)
        self.assertLess(elapsed_ms, 250)


if __name__ == "__main__":
    unittest.main()

