from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

from scripts.run_evaluation_v2_retrieval import (  # noqa: E402
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    run,
)


class EvaluationV2RetrievalTest(unittest.TestCase):
    def test_fair_deterministic_ablation_preserves_partition_and_scope_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run(
                argparse.Namespace(
                    ground_truth=(
                        ROOT
                        / "data"
                        / "evaluation"
                        / "retrieval_ground_truth_v2.json"
                    ),
                    partitions=(
                        ROOT
                        / "data"
                        / "evaluation"
                        / "retrieval_partitions_v2.json"
                    ),
                    catalog=(
                        ROOT / "data" / "evaluation" / "incident_families_v2.json"
                    ),
                    corpus_dir=ROOT / "data" / "knowledge" / "synthetic_v2",
                    seed_dir=ROOT / "data" / "seeds" / "causal_scope_v2",
                    output_dir=Path(temporary_directory),
                    embedding_backend="deterministic",
                    embedding_model=DEFAULT_EMBEDDING_MODEL,
                    embedding_revision=DEFAULT_EMBEDDING_REVISION,
                    embedding_batch_size=32,
                    device="cpu",
                    evaluate_reranker=False,
                    reranker_model=DEFAULT_RERANKER_MODEL,
                    reranker_revision=DEFAULT_RERANKER_REVISION,
                    reranker_local_path=None,
                    reranker_batch_size=16,
                )
            )

            self.assertEqual(result["partitions"]["calibration_query_count"], 16)
            self.assertEqual(result["partitions"]["test_query_count"], 16)
            for evaluation in result["retrieval"]["evaluations"].values():
                self.assertEqual(evaluation["metrics"]["query_count"], 16)
                self.assertEqual(evaluation["metrics"]["unapproved_hit_count"], 0)

            ablation = result["retrieval"]["scope_ablation"]
            self.assertGreater(
                ablation["causal_wide"]["cross_module"],
                ablation["legacy_observed_module"]["cross_module"],
            )
            scope_decision = result["retrieval"]["release_decision"]["causal_scope"]
            self.assertEqual(
                scope_decision["same_module_non_regressing"],
                ablation["causal_wide"]["same_module"]
                >= ablation["legacy_observed_module"]["same_module"],
            )
            self.assertEqual(
                scope_decision["promoted"],
                scope_decision["same_module_non_regressing"]
                and scope_decision["cross_module_strictly_improved"],
            )
            self.assertEqual(result["gates"]["data_quality"]["status"], "PASS")
            self.assertEqual(result["gates"]["governance"]["status"], "PASS")
            self.assertFalse(
                result["retrieval"]["release_decision"]["reranker"]["evaluated"]
            )
            self.assertTrue((Path(temporary_directory) / "failed_cases.json").is_file())


if __name__ == "__main__":
    unittest.main()
