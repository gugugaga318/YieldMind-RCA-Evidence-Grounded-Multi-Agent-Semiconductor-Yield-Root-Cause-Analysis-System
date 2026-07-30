from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seeds" / "multi_case"
GENERATOR = ROOT / "scripts" / "generate_synthetic_multi_case_data.py"


def read_csv(table_name: str, seed_dir: Path = SEED_DIR) -> list[dict[str, str]]:
    with (seed_dir / f"{table_name}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_catalog(seed_dir: Path = SEED_DIR) -> list[dict[str, object]]:
    payload = json.loads((seed_dir / "case_catalog.json").read_text(encoding="utf-8"))
    return list(payload["cases"])


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.iterdir())
        if item.is_file()
    }


class MultiCaseDatasetContractTest(unittest.TestCase):
    def test_generator_remains_an_offline_seed_utility(self) -> None:
        tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        self.assertFalse(
            any(name.startswith(("fastapi", "uvicorn", "yield_rca_api")) for name in imports)
        )

    def test_lot_ids_are_continuous_product_ids_without_failure_labels(self) -> None:
        lot_ids = [row["lot_id"] for row in read_csv("lot_master")]

        self.assertEqual(lot_ids, [f"LOT_A_{number:03d}" for number in range(1, 76)])
        self.assertTrue(all(re.fullmatch(r"LOT_A_\d{3}", item) for item in lot_ids))
        self.assertFalse(any(token in item for item in lot_ids for token in ("CMP", "CVD", "N_")))

    def test_every_lot_has_25_wafers_and_every_wafer_runs_the_complete_route(self) -> None:
        route = [row["operation_no"] for row in read_csv("process_route")]
        wafers_by_lot: dict[str, set[str]] = defaultdict(set)
        operations_by_wafer: dict[str, list[str]] = defaultdict(list)
        for row in read_csv("wafer_master"):
            wafers_by_lot[row["lot_id"]].add(row["wafer_id"])
        for row in read_csv("process_history"):
            operations_by_wafer[row["wafer_id"]].append(row["operation_no"])

        self.assertEqual(len(wafers_by_lot), 75)
        self.assertTrue(all(len(wafers) == 25 for wafers in wafers_by_lot.values()))
        self.assertEqual(len(operations_by_wafer), 75 * 25)
        for operations in operations_by_wafer.values():
            self.assertEqual(operations, route)
            self.assertTrue({"5000", "5100", "5240", "5300", "6400"} <= set(operations))

    def test_process_and_fdc_assignments_match_qualified_capabilities(self) -> None:
        qualified = {
            (row["equipment_id"], row["chamber_id"], row["operation_no"])
            for row in read_csv("equipment_capability")
            if row["qualification_status"] == "QUALIFIED"
        }
        process_assignment = {
            (row["lot_id"], row["wafer_id"], row["operation_no"]): (
                row["equipment_id"],
                row["chamber_id"],
            )
            for row in read_csv("process_history")
        }

        for row in read_csv("process_history"):
            self.assertIn((row["equipment_id"], row["chamber_id"], row["operation_no"]), qualified)
        for row in read_csv("fdc_feature"):
            key = (row["lot_id"], row["wafer_id"], row["operation_no"])
            self.assertEqual(process_assignment[key], (row["equipment_id"], row["chamber_id"]))

    def test_cu_case_models_drift_threshold_crossing_and_containment(self) -> None:
        fdc_rows = read_csv("fdc_feature")
        ooc_rows = read_csv("ooc_event")
        holds = read_csv("hold_history")
        wat_rows = read_csv("wat_result")

        suspect_lots = {
            row["lot_id"]
            for row in fdc_rows
            if row["operation_no"] == "6400" and row["severity"] != "NORMAL"
        }
        ooc_features = [row for row in fdc_rows if row["ooc_flag"] == "true"]
        failed_lots = {row["lot_id"] for row in wat_rows if row["pass_fail"] == "false"}

        self.assertEqual(suspect_lots, {f"LOT_A_{number:03d}" for number in range(11, 16)})
        self.assertEqual(failed_lots, {f"LOT_A_{number:03d}" for number in range(12, 16)})
        self.assertEqual(len(ooc_rows), 1)
        self.assertEqual(len(ooc_features), 1)
        self.assertEqual(ooc_features[0]["lot_id"], "LOT_A_015")
        self.assertEqual(ooc_features[0]["wafer_id"], "LOT_A_015_W13")

        hold = next(row for row in holds if row["hold_id"] == "HOLD_CU_OOC_001")
        self.assertEqual(hold["hold_type"], "EQUIPMENT")
        self.assertGreaterEqual(
            datetime.fromisoformat(hold["created_at"]),
            datetime.fromisoformat(ooc_rows[0]["triggered_at"]),
        )
        defect_lots = {
            row["lot_id"]
            for row in read_csv("defect_summary")
            if row["defect_type"] == "cu_residue"
        }
        self.assertEqual(defect_lots, failed_lots)
        self.assertTrue(any(row["parameter_name"] == "estimated_removal_rate" for row in fdc_rows))

    def test_isolated_scratch_is_one_wafer_without_fdc_ooc_or_equipment_hold(self) -> None:
        scratch_rows = [
            row for row in read_csv("defect_summary") if row["defect_type"] == "scratch"
        ]
        source_fdc = [row for row in read_csv("fdc_feature") if row["lot_id"] == "LOT_A_038"]
        source_holds = [row for row in read_csv("hold_history") if row["lot_id"] == "LOT_A_038"]

        self.assertEqual(len(scratch_rows), 1)
        self.assertEqual(scratch_rows[0]["wafer_id"], "LOT_A_038_W07")
        self.assertEqual(scratch_rows[0]["defect_count"], "1")
        self.assertTrue(all(row["severity"] == "NORMAL" for row in source_fdc))
        self.assertTrue(all(row["ooc_flag"] == "false" for row in source_fdc))
        self.assertEqual([row["hold_type"] for row in source_holds], ["QUALITY"])
        self.assertGreaterEqual(
            datetime.fromisoformat(source_holds[0]["created_at"]),
            datetime.fromisoformat(scratch_rows[0]["inspected_at"]),
        )

    def test_thin_film_parity_maps_to_cvd_chamber_not_cmp_head(self) -> None:
        source_process = [
            row for row in read_csv("process_history") if row["lot_id"] == "LOT_A_063"
        ]
        dep_rows = [row for row in source_process if row["operation_no"] == "5000"]
        cmp_rows = [row for row in source_process if row["operation_no"] == "5100"]
        post_metrology = [
            row
            for row in read_csv("metrology_result")
            if row["lot_id"] == "LOT_A_063" and row["measurement_stage"] == "POST_CMP"
        ]

        for row in dep_rows:
            wafer_no = int(row["wafer_id"].rsplit("W", 1)[-1])
            expected = "CVD_ILD_01_CH01" if wafer_no % 2 else "CVD_ILD_01_CH02"
            self.assertEqual(row["chamber_id"], expected)
        cmp_heads_by_parity: dict[int, set[str]] = defaultdict(set)
        for row in cmp_rows:
            wafer_no = int(row["wafer_id"].rsplit("W", 1)[-1])
            cmp_heads_by_parity[wafer_no % 2].add(row["chamber_id"])
        expected_heads = {f"CMP_ILD_01_CH{number:02d}" for number in range(1, 5)}
        self.assertEqual(cmp_heads_by_parity[0], expected_heads)
        self.assertEqual(cmp_heads_by_parity[1], expected_heads)

        for row in post_metrology:
            wafer_no = int(row["wafer_id"].rsplit("W", 1)[-1])
            self.assertEqual(row["pass_fail"], "true" if wafer_no % 2 else "false")
        source_cmp_fdc = [
            row
            for row in read_csv("fdc_feature")
            if row["lot_id"] == "LOT_A_063" and row["operation_no"] == "6400"
        ]
        self.assertTrue(source_cmp_fdc)
        self.assertTrue(all(row["severity"] == "NORMAL" for row in source_cmp_fdc))

    def test_hold_timing_follows_detection(self) -> None:
        holds = {row["hold_id"]: row for row in read_csv("hold_history")}
        scratch = next(row for row in read_csv("defect_summary") if row["defect_type"] == "scratch")
        failed_metrology = [
            row
            for row in read_csv("metrology_result")
            if row["lot_id"] == "LOT_A_063" and row["pass_fail"] == "false"
        ]

        self.assertGreaterEqual(
            datetime.fromisoformat(holds["HOLD_SCRATCH_W07_001"]["created_at"]),
            datetime.fromisoformat(scratch["inspected_at"]),
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(holds["HOLD_ILD_PARITY_001"]["created_at"]),
            max(datetime.fromisoformat(row["measured_at"]) for row in failed_metrology),
        )

    def test_catalog_describes_supported_and_inconclusive_cases(self) -> None:
        catalog = read_catalog()

        self.assertEqual(len(catalog), 3)
        self.assertEqual(
            {case["affected_operation"] for case in catalog},
            {"5000", "6400"},
        )
        self.assertEqual(
            [case["expected_status"] for case in catalog],
            ["supported", "inconclusive", "supported"],
        )
        self.assertEqual(len({case["source_lot_id"] for case in catalog}), 3)

    def test_generation_is_deterministic_and_does_not_touch_golden_case(self) -> None:
        golden_dir = ROOT / "data" / "seeds" / "golden_case"
        golden_before = directory_hashes(golden_dir)
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = Path(temporary_directory) / "first"
            second = Path(temporary_directory) / "second"
            for output_dir in (first, second):
                subprocess.run(
                    [sys.executable, str(GENERATOR), "--output-dir", str(output_dir)],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            self.assertEqual(directory_hashes(first), directory_hashes(second))
        self.assertEqual(directory_hashes(golden_dir), golden_before)


if __name__ == "__main__":
    unittest.main()
