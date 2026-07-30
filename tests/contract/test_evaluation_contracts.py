from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evaluation import EvaluationScenario, ScenarioFabRepository  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

CATALOG = ROOT / "data" / "evaluation" / "scenarios.json"
SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def scenarios() -> list[EvaluationScenario]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    return [EvaluationScenario.from_dict(item) for item in payload["scenarios"]]


class EvaluationContractTest(unittest.TestCase):
    def test_catalog_covers_all_step_14_scenarios(self) -> None:
        items = scenarios()

        self.assertEqual(len(items), 10)
        self.assertEqual(
            {item.scenario_id for item in items},
            {
                "EVAL_CMP_SLURRY_DECLINE",
                "EVAL_RECIPE_VERSION_CHANGE",
                "EVAL_SINGLE_CHAMBER",
                "EVAL_SCRATCH_WAT_FAIL",
                "EVAL_MES_NO_FDC",
                "EVAL_FDC_NO_YIELD",
                "EVAL_CONFLICTING_EVIDENCE",
                "EVAL_MISSING_DATA",
                "EVAL_HIGH_HISTORY_MATCH",
                "EVAL_INCONCLUSIVE_ROOT_CAUSE",
            },
        )
        self.assertEqual(sum(item.expected_status == "supported" for item in items), 3)
        self.assertEqual(sum(item.expected_status == "inconclusive" for item in items), 7)

    def test_recipe_scenario_changes_all_source_wafers_to_r19(self) -> None:
        repository = ScenarioFabRepository(CsvFabRepository(SEED_DIR), "EVAL_RECIPE_VERSION_CHANGE")
        source_rows = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] == "LOT_A_038" and row["operation_no"] == "6400"
        ]

        self.assertEqual(len(source_rows), 25)
        self.assertEqual({row["recipe_version"] for row in source_rows}, {"R19"})
        recipe_keys = {
            (row["recipe_id"], row["recipe_version"]) for row in repository.rows("recipe_master")
        }
        self.assertIn(("CU_CMP_40N", "R19"), recipe_keys)

    def test_negative_and_conflicting_variants_have_expected_data_signals(self) -> None:
        base = CsvFabRepository(SEED_DIR)
        no_fdc = ScenarioFabRepository(base, "EVAL_MES_NO_FDC")
        source_fdc = [row for row in no_fdc.rows("fdc_feature") if row["lot_id"] == "LOT_A_015"]
        self.assertTrue(source_fdc)
        self.assertTrue(all(row["severity"] == "NORMAL" for row in source_fdc))
        self.assertFalse(no_fdc.rows("ooc_event"))

        no_yield = ScenarioFabRepository(base, "EVAL_FDC_NO_YIELD")
        source_wat = [row for row in no_yield.rows("wat_result") if row["lot_id"] == "LOT_A_015"]
        self.assertTrue(all(row["pass_fail"] == "true" for row in source_wat))
        self.assertFalse(
            any(
                row["lot_id"] == "LOT_A_015" and row["defect_type"] == "cu_residue"
                for row in no_yield.rows("defect_summary")
            )
        )

        conflict = ScenarioFabRepository(base, "EVAL_CONFLICTING_EVIDENCE")
        removal = [
            row
            for row in conflict.rows("fdc_feature")
            if row["lot_id"] == "LOT_A_015" and row["parameter_name"] == "estimated_removal_rate"
        ]
        self.assertTrue(removal)
        self.assertTrue(all(float(row["delta_percent"]) >= 5.0 for row in removal))

        missing = ScenarioFabRepository(base, "EVAL_MISSING_DATA")
        self.assertFalse(any(row["lot_id"] == "LOT_A_038" for row in missing.rows("fdc_feature")))

    def test_evaluation_runtime_has_no_fastapi_or_frontend_dependency(self) -> None:
        paths = [
            ROOT / "core" / "yield_rca_core" / "evaluation.py",
            ROOT / "scripts" / "run_evaluation.py",
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import | ast.ImportFrom)
                for alias in node.names
            }
            self.assertFalse(
                any(name.startswith(("fastapi", "uvicorn", "yield_rca_api")) for name in imports)
            )


if __name__ == "__main__":
    unittest.main()
