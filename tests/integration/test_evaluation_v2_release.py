from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run_evaluation_v2_release import run  # noqa: E402


class EvaluationV2ReleaseTest(unittest.TestCase):
    def test_four_gates_and_runtime_decision_remain_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            result = run(
                argparse.Namespace(
                    data_results=(
                        ROOT
                        / "outputs"
                        / "evaluation_v2_data_quality"
                        / "results.json"
                    ),
                    retrieval_results=(
                        ROOT
                        / "outputs"
                        / "evaluation_v2_release"
                        / "retrieval"
                        / "results.json"
                    ),
                    rca_results=(
                        ROOT
                        / "outputs"
                        / "evaluation_v2_release"
                        / "rca"
                        / "results.json"
                    ),
                    output_dir=output_dir,
                )
            )

            self.assertEqual(
                set(result["gates"]),
                {
                    "data_quality",
                    "governance",
                    "retrieval_quality",
                    "rca_quality",
                },
            )
            self.assertEqual(result["gates"]["data_quality"]["status"], "PASS")
            self.assertEqual(result["gates"]["governance"]["status"], "PASS")
            self.assertEqual(result["gates"]["retrieval_quality"]["status"], "FAIL")
            self.assertEqual(result["gates"]["rca_quality"]["status"], "BLOCKED")
            self.assertEqual(result["release_status"], "NOT_READY")
            self.assertFalse(result["passed"])

            decision = result["release_decision"]
            self.assertEqual(
                decision["selected_runtime"],
                {
                    "causal_scope_enabled": True,
                    "reranker_enabled": False,
                    "retriever": "chunk_keyword",
                },
            )
            self.assertFalse(decision["hybrid_promoted"])
            self.assertFalse(decision["reranker"]["evaluated"])
            self.assertFalse(decision["reranker"]["enabled"])

            serialized = (output_dir / "results.json").read_text(encoding="utf-8")
            self.assertEqual(json.loads(serialized), result)
            self.assertNotIn("DASHSCOPE_API_KEY=", serialized)
            report = (output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Release status: **NOT_READY**", report)
            self.assertIn("Hybrid-RRF is implemented but not promoted", report)
            self.assertIn("real-Qwen", report)


if __name__ == "__main__":
    unittest.main()
