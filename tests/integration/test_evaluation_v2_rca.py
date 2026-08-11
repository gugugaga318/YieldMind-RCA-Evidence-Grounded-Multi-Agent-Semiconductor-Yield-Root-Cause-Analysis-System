from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

from scripts.run_evaluation_v2_rca import run  # noqa: E402


class EvaluationV2RCATest(unittest.TestCase):
    evaluation: ClassVar[dict[str, Any]]
    output_dir: ClassVar[Path]
    temporary_directory: ClassVar[tempfile.TemporaryDirectory[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.temporary_directory.name)
        cls.evaluation = run(
            argparse.Namespace(
                scenarios=ROOT / "data" / "evaluation" / "rca_scenarios_v2.json",
                incidents=ROOT / "data" / "evaluation" / "incident_families_v2.json",
                seed_dir=ROOT / "data" / "seeds" / "causal_scope_v2",
                corpus=ROOT / "data" / "knowledge" / "synthetic_v2" / "corpus.json",
                retrieval_results=(
                    ROOT
                    / "outputs"
                    / "evaluation_v2_release"
                    / "retrieval"
                    / "results.json"
                ),
                output_dir=cls.output_dir,
                run_real_qwen=False,
                confirm_paid_qwen=False,
                max_qwen_calls_per_scenario=16,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_fixed_reference_passes_reviewed_test_truth(self) -> None:
        fixed = self.evaluation["modes"]["fixed"]
        test = fixed["partitions"]["test"]

        self.assertEqual(fixed["failed_scenario_ids"], [])
        self.assertEqual(test["root_cause_correctness"], 1.0)
        self.assertEqual(test["evidence_completeness"], 1.0)
        self.assertEqual(test["impact_lot_precision"], 1.0)
        self.assertEqual(test["impact_lot_recall"], 1.0)
        self.assertEqual(test["correct_abstention_rate"], 1.0)
        self.assertEqual(test["required_warning_recall"], 1.0)

    def test_evaluator_matches_semantics_without_runtime_truth_labels(self) -> None:
        for row in self.evaluation["modes"]["fixed"]["results"]:
            for evidence_match in row["evidence_matches"]:
                self.assertTrue(evidence_match["satisfied"])
                self.assertTrue(evidence_match["semantic_type"])
                self.assertTrue(evidence_match["matched_runtime_evidence_ids"])
                self.assertTrue(
                    all(
                        not evidence_id.startswith("EV_V2_")
                        for evidence_id in evidence_match[
                            "matched_runtime_evidence_ids"
                        ]
                    )
                )

    def test_controlled_mode_is_safe_compatibility_not_accuracy_claim(self) -> None:
        controlled = self.evaluation["modes"]["controlled_react"]
        compatibility = self.evaluation["gates"]["rca_quality"][
            "controlled_compatibility"
        ]

        self.assertEqual(compatibility["status"], "PASS")
        self.assertEqual(controlled["metrics"]["completion_rate"], 1.0)
        self.assertEqual(
            controlled["metrics"]["requested_mode_preservation_rate"],
            1.0,
        )
        self.assertEqual(controlled["metrics"]["root_cause_correctness"], 0.0)
        self.assertEqual(compatibility["unsafe_false_support_scenario_ids"], [])

    def test_governance_passes_but_real_qwen_gate_is_honestly_blocked(self) -> None:
        governance = self.evaluation["gates"]["governance"]

        self.assertEqual(governance["status"], "PASS")
        self.assertEqual(governance["unapproved_knowledge_leakage"], 0)
        self.assertEqual(governance["historical_only_root_cause_promotions"], 0)
        self.assertEqual(governance["unsupported_source_explicit_count"], 2)
        self.assertEqual(governance["unsupported_source_recall"], 1.0)
        self.assertEqual(self.evaluation["modes"]["llm_react"]["status"], "NOT_RUN")
        self.assertEqual(self.evaluation["gates"]["rca_quality"]["status"], "BLOCKED")
        self.assertFalse(self.evaluation["passed"])

    def test_artifacts_are_secret_free_and_match_selected_runtime(self) -> None:
        self.assertEqual(
            self.evaluation["selected_runtime"],
            {
                "causal_scope_enabled": True,
                "reranker_enabled": False,
                "retriever": "chunk_keyword",
            },
        )
        results_path = self.output_dir / "results.json"
        report_path = self.output_dir / "report.md"
        failed_path = self.output_dir / "failed_cases.json"
        self.assertTrue(results_path.is_file())
        self.assertTrue(report_path.is_file())
        self.assertTrue(failed_path.is_file())
        self.assertEqual(
            json.loads(results_path.read_text(encoding="utf-8")),
            self.evaluation,
        )
        serialized = results_path.read_text(encoding="utf-8")
        self.assertNotIn("DASHSCOPE_API_KEY=", serialized)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("RCA quality gate: **BLOCKED**", report)
        self.assertIn("Fake LLM output was not used", report)


if __name__ == "__main__":
    unittest.main()
