from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

from yield_rca_core.autonomous_evaluation import (  # noqa: E402
    evaluate_autonomous_qwen_react,
    render_autonomous_qwen_report,
)
from yield_rca_core.evaluation import EvaluationScenario  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

from scripts.run_autonomous_qwen_evaluation import (  # noqa: E402
    run_autonomous_evaluation,
)

AUTONOMOUS_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
FIXED_CATALOG = ROOT / "data" / "evaluation" / "scenarios.json"
FIXED_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def _fixed_scenarios() -> list[EvaluationScenario]:
    payload = json.loads(FIXED_CATALOG.read_text(encoding="utf-8"))
    return [
        EvaluationScenario.from_dict(item)
        for item in payload["scenarios"]
    ]


class AutonomousQwenEvaluationSuiteTest(unittest.TestCase):
    evaluation: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.evaluation = evaluate_autonomous_qwen_react(
            CsvFabRepository(AUTONOMOUS_SEED_DIR),
            fixed_repository=CsvFabRepository(FIXED_SEED_DIR),
            fixed_scenarios=_fixed_scenarios(),
        )

    def test_deterministic_lanes_pass_and_real_qwen_is_separate(self) -> None:
        lanes = self.evaluation["lanes"]

        self.assertTrue(self.evaluation["passed"])
        self.assertEqual(lanes["autonomous_fake"]["status"], "PASS")
        self.assertEqual(lanes["autonomous_fake"]["scenario_count"], 10)
        self.assertEqual(lanes["autonomous_fake"]["scenario_pass_count"], 10)
        self.assertEqual(lanes["fixed_workflow"]["status"], "PASS")
        self.assertEqual(lanes["fixed_workflow"]["scenario_count"], 10)
        self.assertEqual(lanes["fixed_workflow"]["scenario_pass_count"], 10)
        self.assertEqual(lanes["real_qwen_smoke"]["status"], "SKIPPED")
        self.assertFalse(
            lanes["real_qwen_smoke"]["required_for_deterministic_acceptance"]
        )

    def test_five_metrics_preserve_reasoning_and_stop_semantics(self) -> None:
        metrics = self.evaluation["metrics"]

        self.assertEqual(
            set(metrics),
            {
                "decision_valid",
                "evidence_gain",
                "redundant",
                "goal_success",
                "stop_correct",
            },
        )
        self.assertEqual(metrics["decision_valid"]["rate"], 1.0)
        self.assertEqual(metrics["decision_valid"]["decision_count"], 28)
        self.assertEqual(metrics["evidence_gain"]["gain_count"], 14)
        self.assertEqual(metrics["evidence_gain"]["act_decision_count"], 20)
        self.assertEqual(metrics["redundant"]["redundant_count"], 0)
        self.assertEqual(metrics["goal_success"]["rate"], 1.0)
        self.assertEqual(metrics["stop_correct"]["rate"], 1.0)
        self.assertLess(metrics["evidence_gain"]["rate"], 1.0)

        scenarios = {
            item["scenario_id"]: item
            for item in self.evaluation["autonomous_scenarios"]
        }
        root = scenarios["AUTONOMOUS_SCRATCH_CU_CMP_ROOT_CAUSE"]
        reasoning = next(
            item
            for item in root["decision_metrics"]
            if item["action_kind"] == "run_rca_reasoning"
        )
        self.assertFalse(reasoning["evidence_gain"])
        self.assertFalse(reasoning["redundant"])

    def test_intents_replanning_gate_and_fallback_attribution(self) -> None:
        scenarios = {
            item["scenario_id"]: item
            for item in self.evaluation["autonomous_scenarios"]
        }
        impact = scenarios["AUTONOMOUS_LOT_IMPACT"]
        root = scenarios["AUTONOMOUS_SCRATCH_CU_CMP_ROOT_CAUSE"]
        product_root = scenarios["AUTONOMOUS_PRODUCT_ROOT_CAUSE"]
        premature = scenarios["AUTONOMOUS_PREMATURE_STOP_GATE"]
        partial = scenarios["AUTONOMOUS_PARTIAL_EVIDENCE_STOP_GATE"]

        self.assertEqual(impact["action_chain"], ["find_shared_exposure"])
        self.assertEqual(
            root["action_chain"],
            [
                "inspect_defect_pattern",
                "find_shared_exposure",
                "validate_shared_defect_pattern",
                "inspect_fdc_spc",
                "run_rca_reasoning",
            ],
        )
        self.assertNotEqual(impact["action_chain"], root["action_chain"])
        self.assertTrue(root["checks"]["scratch_replanning"])
        self.assertEqual(product_root["action_chain"][0], "find_shared_exposure")
        self.assertEqual(premature["conclusion_level"], "inconclusive")
        self.assertFalse(premature["goal_success"])
        self.assertFalse(premature["stop_correct"])
        self.assertTrue(premature["checks"]["evidence_gate_downgraded"])
        self.assertEqual(partial["action_chain"], ["inspect_defect_pattern"])
        self.assertGreater(len(partial["action_trace"][0]["produced_evidence_ids"]), 0)
        self.assertEqual(partial["conclusion_level"], "signal")
        self.assertFalse(partial["goal_success"])
        self.assertFalse(partial["stop_correct"])
        self.assertTrue(partial["checks"]["no_supported_hypothesis"])
        self.assertTrue(partial["checks"]["hypothesis_gate_downgraded"])

        self.assertTrue(root["checks"]["supported_hypothesis_gate"])
        for trace in root["action_trace"]:
            self.assertTrue(trace["execution_reason"])
            self.assertTrue(trace["inputs"])
            self.assertTrue(trace["scope"])
            self.assertTrue(trace["observation"])
            self.assertTrue(trace["produced_evidence_ids"])
        self.assertTrue(root["stop_trace"]["planner_reason"])
        self.assertEqual(root["stop_trace"]["stop_reason"], "goal_satisfied")

        self.assertTrue(all(self.evaluation["acceptance"].values()))
        for fallback in self.evaluation["fallback_scenarios"]:
            self.assertTrue(fallback["passed"])
            self.assertIsNone(fallback["run_evaluation"])
            self.assertTrue(fallback["checks"]["not_attributed_to_qwen"])

    def test_report_and_runner_write_secret_free_stable_artifacts(self) -> None:
        report = render_autonomous_qwen_report(self.evaluation)

        self.assertIn("Deterministic acceptance: **PASS**", report)
        self.assertIn("| Autonomous Fake | PASS | 10/10 scenarios |", report)
        self.assertIn("| Fixed workflow | PASS | 10/10 scenarios |", report)
        self.assertIn("| Real Qwen smoke | SKIPPED |", report)
        self.assertIn("`evidence_gain=false` and `redundant=false`", report)
        self.assertIn("## Scratch + Cu CMP action audit", report)

        tracked_output_dir = (
            ROOT / "outputs" / "autonomous_qwen_react_evaluation"
        )
        self.assertEqual(
            json.loads(
                (tracked_output_dir / "results.json").read_text(encoding="utf-8")
            ),
            self.evaluation,
        )
        self.assertEqual(
            (tracked_output_dir / "report.md").read_text(encoding="utf-8"),
            report,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            written = run_autonomous_evaluation(
                autonomous_seed_dir=AUTONOMOUS_SEED_DIR,
                fixed_catalog=FIXED_CATALOG,
                fixed_seed_dir=FIXED_SEED_DIR,
                output_dir=output_dir,
            )
            results_path = output_dir / "results.json"
            report_path = output_dir / "report.md"
            self.assertTrue(results_path.is_file())
            self.assertTrue(report_path.is_file())
            self.assertEqual(
                json.loads(results_path.read_text(encoding="utf-8")),
                written,
            )
            serialized = results_path.read_text(encoding="utf-8")
            self.assertNotIn("DASHSCOPE_API_KEY=", serialized)
            self.assertNotIn(str(ROOT), serialized)

    def test_runtime_configuration_exposes_orchestration_mode(self) -> None:
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("YIELD_RCA_ORCHESTRATION_MODE=fixed", env_example)
        self.assertIn(
            "YIELD_RCA_ORCHESTRATION_MODE: "
            "${YIELD_RCA_ORCHESTRATION_MODE:-fixed}",
            compose,
        )


if __name__ == "__main__":
    unittest.main()
