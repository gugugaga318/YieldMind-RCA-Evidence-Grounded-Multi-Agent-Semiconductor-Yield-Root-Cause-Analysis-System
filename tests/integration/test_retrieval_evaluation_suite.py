from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_retrieval_evaluation import (  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
    run_retrieval_evaluation,
)

GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
RCA_SCENARIOS = ROOT / "data" / "evaluation" / "scenarios.json"


class RetrievalEvaluationIntegrationTest(unittest.TestCase):
    def test_keyword_baseline_is_repeatable_and_exposes_known_gaps(self) -> None:
        scenarios_before = RCA_SCENARIOS.read_bytes()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_dir = Path(first)
            second_dir = Path(second)
            first_result = run_retrieval_evaluation(GROUND_TRUTH, CORPUS_DIR, first_dir)
            second_result = run_retrieval_evaluation(GROUND_TRUTH, CORPUS_DIR, second_dir)

            self.assertTrue(first_result["passed"])
            self.assertTrue(first_result["acceptance"]["unapproved_knowledge_leakage_gate"])
            self.assertTrue(first_result["acceptance"]["quality_metrics_are_baseline_only"])
            metrics = first_result["metrics"]
            self.assertEqual(metrics["query_count"], 114)
            self.assertEqual(metrics["answerable_query_count"], 96)
            self.assertEqual(metrics["no_answer_query_count"], 18)
            self.assertEqual(metrics["recall_at_5"], 0.411458)
            self.assertEqual(metrics["candidate_recall_at_20"], 0.552083)
            self.assertEqual(metrics["cross_language_recall_at_5"], 0.104167)
            self.assertEqual(metrics["no_answer_accuracy"], 0.0)
            self.assertEqual(metrics["no_answer_false_positive_rate"], 1.0)
            self.assertEqual(metrics["unapproved_hit_count"], 0)
            self.assertEqual(
                metrics["by_question_kind"]["procedure_guidance"]["recall_at_5"],
                0.0,
            )
            self.assertEqual(
                metrics["by_question_kind"]["engineering_note_lookup"]["recall_at_5"],
                0.0,
            )
            self.assertEqual(first_result, second_result)
            self.assertEqual(
                (first_dir / "results.json").read_bytes(),
                (second_dir / "results.json").read_bytes(),
            )
            self.assertEqual(
                (first_dir / "report.md").read_bytes(),
                (second_dir / "report.md").read_bytes(),
            )
            serialized = json.dumps(first_result)
            self.assertNotIn("DASHSCOPE_API_KEY", serialized)
            self.assertNotIn(str(ROOT), serialized)

        self.assertEqual(RCA_SCENARIOS.read_bytes(), scenarios_before)


if __name__ == "__main__":
    unittest.main()
