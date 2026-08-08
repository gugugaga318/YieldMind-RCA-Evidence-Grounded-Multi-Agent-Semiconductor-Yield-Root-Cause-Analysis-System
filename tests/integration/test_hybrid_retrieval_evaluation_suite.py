from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_hybrid_retrieval_evaluation import (  # type: ignore[import-not-found]  # noqa: E402
    run_hybrid_retrieval_evaluation,
)
from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    DeterministicHashEmbeddingBackend,
)

GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"


class HybridRetrievalEvaluationIntegrationTest(unittest.TestCase):
    def test_four_way_ablation_is_repeatable_and_leak_free(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_result = run_hybrid_retrieval_evaluation(
                GROUND_TRUTH,
                CORPUS_DIR,
                Path(first),
                embedding_backend=DeterministicHashEmbeddingBackend(),
                requested_device="cpu",
            )
            second_result = run_hybrid_retrieval_evaluation(
                GROUND_TRUTH,
                CORPUS_DIR,
                Path(second),
                embedding_backend=DeterministicHashEmbeddingBackend(),
                requested_device="cpu",
            )

            self.assertEqual(
                first_result["order"],
                [
                    "Legacy-Case-Keyword",
                    "Chunk-Keyword",
                    "BM25-only",
                    "Vector-only",
                    "Hybrid-RRF",
                ],
            )
            self.assertTrue(first_result["passed"])
            self.assertTrue(
                first_result["acceptance"]["unapproved_knowledge_leakage_gate"]
            )
            self.assertFalse(first_result["acceptance"]["online_retriever_cutover"])
            for evaluation in first_result["evaluations"].values():
                self.assertEqual(evaluation["metrics"]["query_count"], 114)
                self.assertEqual(evaluation["metrics"]["unapproved_hit_count"], 0)
            self.assertEqual(first_result, second_result)
            self.assertEqual(
                (Path(first) / "results.json").read_bytes(),
                (Path(second) / "results.json").read_bytes(),
            )
            self.assertEqual(
                (Path(first) / "report.md").read_bytes(),
                (Path(second) / "report.md").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
