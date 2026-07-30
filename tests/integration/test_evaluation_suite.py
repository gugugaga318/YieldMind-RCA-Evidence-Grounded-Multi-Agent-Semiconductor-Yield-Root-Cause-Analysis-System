from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evaluation import (  # noqa: E402
    EvaluationScenario,
    evaluate_scenarios,
    render_evaluation_report,
)
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

CATALOG = ROOT / "data" / "evaluation" / "scenarios.json"
SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


class EvaluationSuiteIntegrationTest(unittest.TestCase):
    evaluation: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        scenarios = [EvaluationScenario.from_dict(item) for item in payload["scenarios"]]
        cls.evaluation = evaluate_scenarios(CsvFabRepository(SEED_DIR), scenarios)

    def test_acceptance_metrics_pass(self) -> None:
        metrics = self.evaluation["metrics"]

        self.assertTrue(self.evaluation["passed"])
        self.assertTrue(all(self.evaluation["acceptance"].values()))
        self.assertEqual(metrics["scenario_count"], 10)
        self.assertEqual(metrics["scenario_pass_rate"], 1.0)
        self.assertEqual(metrics["top1_root_cause_accuracy"], 1.0)
        self.assertEqual(metrics["top3_recall"], 1.0)
        self.assertEqual(metrics["inconclusive_handling_rate"], 1.0)
        self.assertEqual(metrics["false_positive_rate"], 0.0)
        self.assertEqual(metrics["evidence_traceability_rate"], 1.0)
        self.assertEqual(metrics["hallucinated_citation_rate"], 0.0)
        self.assertEqual(metrics["hallucinated_citation_count"], 0)
        self.assertLessEqual(metrics["confidence_calibration"]["ece"], 0.15)
        self.assertGreater(metrics["tool_latency_ms"]["count"], 0)
        self.assertGreater(metrics["end_to_end_latency_ms"]["count"], 0)
        self.assertEqual(metrics["scope_accuracy"], 1.0)
        self.assertEqual(metrics["warning_requirement_rate"], 1.0)

    def test_recipe_conflict_and_history_behavior(self) -> None:
        results = {item["scenario_id"]: item for item in self.evaluation["results"]}

        recipe = results["EVAL_RECIPE_VERSION_CHANGE"]
        self.assertEqual(recipe["actual_status"], "supported")
        self.assertEqual(recipe["actual_root_cause"], "CU_CMP_40N R19 recipe version change")

        conflict = results["EVAL_CONFLICTING_EVIDENCE"]
        self.assertEqual(conflict["actual_status"], "inconclusive")
        self.assertIn("WARN_RCA_CONFLICTING_EVIDENCE", conflict["warning_ids"])

        history = results["EVAL_HIGH_HISTORY_MATCH"]
        self.assertGreaterEqual(history["historical_similarity"], 0.95)
        self.assertEqual(history["actual_status"], "inconclusive")

    def test_report_contains_metrics_and_all_scenarios(self) -> None:
        report = render_evaluation_report(self.evaluation)

        self.assertIn("Overall status: **PASS**", report)
        self.assertIn("False-positive rate: 0.0%", report)
        self.assertIn("Top-3 recall: 100.0%", report)
        self.assertIn("Hallucinated citation rate: 0.0%", report)
        self.assertIn("Tool latency:", report)
        self.assertIn("End-to-end latency:", report)
        self.assertIn("Required Warning recall: 100.0%", report)
        for result in self.evaluation["results"]:
            self.assertIn(result["scenario_id"], report)


if __name__ == "__main__":
    unittest.main()
