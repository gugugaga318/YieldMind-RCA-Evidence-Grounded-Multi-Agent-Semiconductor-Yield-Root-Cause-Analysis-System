from __future__ import annotations

import csv
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


def read_csv(name: str) -> list[dict[str, str]]:
    with (SEED_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GoldenDatasetContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_synthetic_fab_data.py"),
                "--output-dir",
                str(SEED_DIR),
            ],
            check=True,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )

    def test_required_seed_files_exist(self) -> None:
        required = [
            "lot_master.csv",
            "wafer_master.csv",
            "process_route.csv",
            "operation_master.csv",
            "process_history.csv",
            "equipment_master.csv",
            "equipment_capability.csv",
            "chamber_master.csv",
            "recipe_master.csv",
            "recipe_history.csv",
            "hold_history.csv",
            "fdc_feature.csv",
            "ooc_event.csv",
            "defect_summary.csv",
            "wat_result.csv",
            "rca_case.csv",
            "knowledge_document.csv",
            "ground_truth.json",
        ]
        for filename in required:
            self.assertTrue((SEED_DIR / filename).exists(), filename)

    def test_normal_and_affected_lots_exist(self) -> None:
        lots = read_csv("lot_master")
        normal = [row for row in lots if row["lot_id"].startswith("LOT_N_")]
        affected = [row for row in lots if row["lot_id"].startswith("LOT_A_")]

        self.assertGreaterEqual(len(normal), 30)
        self.assertGreaterEqual(len(affected), 20)

    def test_ground_truth_matches_expected_root_cause(self) -> None:
        payload = json.loads((SEED_DIR / "ground_truth.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["root_cause"], "CMP_CU03_CH02 slurry delivery degradation")
        self.assertEqual(payload["affected_operation"], "6400")
        self.assertEqual(payload["affected_equipment"], "CMP_CU03")
        self.assertEqual(payload["affected_chamber"], "CMP_CU03_CH02")
        self.assertGreaterEqual(len(payload["expected_evidence"]), 8)

    def test_route_contains_required_fab_modules(self) -> None:
        route = read_csv("process_route")
        route_modules = {row["module"] for row in route}
        route_operations = {row["operation_no"] for row in route}

        self.assertIn("Wet Clean", route_modules)
        self.assertIn("Diffusion", route_modules)
        self.assertIn("Thin Film", route_modules)
        self.assertIn("W CMP", route_modules)
        self.assertIn("1000", route_operations)
        self.assertIn("1100", route_operations)
        self.assertIn("1300", route_operations)
        self.assertIn("5000", route_operations)
        self.assertIn("5100", route_operations)
        self.assertIn("5300", route_operations)
        self.assertIn("6100", route_operations)
        self.assertIn("6240", route_operations)

    def test_route_preserves_simplified_integration_blocks(self) -> None:
        route = sorted(read_csv("process_route"), key=lambda row: int(row["sequence_no"]))
        ordered_ops = [row["operation_no"] for row in route]

        expected_order = [
            "1000",
            "1100",
            "1200",
            "1300",
            "1400",
            "1450",
            "1500",
            "1510",
            "5000",
            "5100",
            "5110",
            "5200",
            "5210",
            "5220",
            "5230",
            "5240",
            "5300",
            "5310",
            "6000",
            "6100",
            "6110",
            "6200",
            "6210",
            "6220",
            "6230",
            "6240",
            "6400",
            "6410",
            "6500",
            "9000",
        ]

        self.assertEqual(ordered_ops, expected_order)

    def test_each_lot_runs_complete_route(self) -> None:
        route_operations = {row["operation_no"] for row in read_csv("process_route")}
        lot_ids = {row["lot_id"] for row in read_csv("lot_master")}
        process_history = read_csv("process_history")

        for lot_id in lot_ids:
            lot_operations = {
                row["operation_no"]
                for row in process_history
                if row["lot_id"] == lot_id
            }
            self.assertEqual(lot_operations, route_operations, lot_id)

    def test_applied_materials_cmp_tools_have_four_heads(self) -> None:
        equipment = read_csv("equipment_master")
        chambers = read_csv("chamber_master")
        amat_cmp_tools = [
            row["equipment_id"]
            for row in equipment
            if row["vendor"] == "Applied Materials" and row["equipment_type"] == "CMP"
        ]

        self.assertGreaterEqual(set(amat_cmp_tools), {"CMP_STI_01", "CMP_ILD_01", "CMP_IMD_01", "CMP_CU03"})
        for equipment_id in amat_cmp_tools:
            tool_chambers = [
                row["chamber_id"]
                for row in chambers
                if row["equipment_id"] == equipment_id
            ]
            self.assertEqual(len(tool_chambers), 4, equipment_id)
            self.assertEqual(
                sorted(tool_chambers),
                [f"{equipment_id}_CH{head_no:02d}" for head_no in range(1, 5)],
            )

    def test_cmp_capabilities_do_not_cross_modules(self) -> None:
        capability = read_csv("equipment_capability")
        equipment = {row["equipment_id"]: row for row in read_csv("equipment_master")}

        expected_by_operation = {
            "1500": ("STI CMP", "Oxide", "CMP_STI_01"),
            "5100": ("ILD CMP", "Oxide", "CMP_ILD_01"),
            "5300": ("W CMP", "Tungsten", "CMP_W_01"),
            "6100": ("IMD CMP", "Low-k", "CMP_IMD_01"),
            "6400": ("Cu CMP", "Copper", None),
        }
        for row in capability:
            if row["operation_no"] not in expected_by_operation:
                continue
            expected_module, expected_material, expected_equipment = expected_by_operation[row["operation_no"]]
            equipment_row = equipment[row["equipment_id"]]
            self.assertEqual(row["module"], expected_module)
            self.assertEqual(row["material"], expected_material)
            self.assertEqual(equipment_row["module"], expected_module)
            self.assertEqual(equipment_row["material"], expected_material)
            if expected_equipment is not None:
                self.assertEqual(row["equipment_id"], expected_equipment)

    def test_affected_lots_concentrate_on_cmp_cu03_ch02(self) -> None:
        process_history = read_csv("process_history")
        affected_cu_cmp = [
            row
            for row in process_history
            if row["lot_id"].startswith("LOT_A_") and row["operation_no"] == "6400"
        ]

        self.assertTrue(affected_cu_cmp)
        self.assertTrue(all(row["equipment_id"] == "CMP_CU03" for row in affected_cu_cmp))
        self.assertTrue(all(row["chamber_id"] == "CMP_CU03_CH02" for row in affected_cu_cmp))

    def test_cu_cmp_lots_only_use_cu_cmp_capability(self) -> None:
        equipment = {row["equipment_id"]: row for row in read_csv("equipment_master")}
        process_history = read_csv("process_history")
        cu_cmp_rows = [row for row in process_history if row["operation_no"] == "6400"]

        self.assertTrue(cu_cmp_rows)
        for row in cu_cmp_rows:
            equipment_row = equipment[row["equipment_id"]]
            self.assertEqual(equipment_row["module"], "Cu CMP")
            self.assertEqual(equipment_row["material"], "Copper")
            self.assertNotIn("STI", equipment_row["module"])
            self.assertNotIn("W CMP", equipment_row["module"])

    def test_required_agent_evidence_sources_exist(self) -> None:
        self.assertTrue(read_csv("hold_history"))
        self.assertTrue(read_csv("fdc_feature"))
        self.assertTrue(read_csv("ooc_event"))
        self.assertTrue(read_csv("defect_summary"))
        self.assertTrue(read_csv("wat_result"))
        self.assertTrue(read_csv("rca_case"))
        self.assertTrue(read_csv("knowledge_document"))

        hold_comments = " ".join(row["hold_comment"] for row in read_csv("hold_history"))
        self.assertIn("CMP_CU03_CH02", hold_comments)
        self.assertIn("slurry", hold_comments)

    def test_evidence_timestamps_follow_process_timeline(self) -> None:
        process_history = read_csv("process_history")
        fdc_features = read_csv("fdc_feature")
        ooc_events = read_csv("ooc_event")
        defect_summary = read_csv("defect_summary")
        wat_result = read_csv("wat_result")
        hold_history = read_csv("hold_history")

        lot_id = "LOT_A_001"
        lot_steps = {row["operation_no"]: row for row in process_history if row["lot_id"] == lot_id}
        cu_cmp_start = datetime.fromisoformat(lot_steps["6400"]["started_at"])
        cu_cmp_end = datetime.fromisoformat(lot_steps["6400"]["ended_at"])
        inspect_end = datetime.fromisoformat(lot_steps["6500"]["ended_at"])
        wat_end = datetime.fromisoformat(lot_steps["9000"]["ended_at"])

        lot_fdc_times = [
            datetime.fromisoformat(row["measured_at"])
            for row in fdc_features
            if row["lot_id"] == lot_id
        ]
        lot_ooc_times = [
            datetime.fromisoformat(row["triggered_at"])
            for row in ooc_events
            if row["description"].startswith(lot_id)
        ]
        lot_defect_time = datetime.fromisoformat(
            next(row["inspected_at"] for row in defect_summary if row["lot_id"] == lot_id)
        )
        lot_wat_time = datetime.fromisoformat(next(row["tested_at"] for row in wat_result if row["lot_id"] == lot_id))
        lot_hold_time = datetime.fromisoformat(
            next(row["created_at"] for row in hold_history if row["lot_id"] == lot_id)
        )

        self.assertTrue(all(cu_cmp_start <= measured_at <= cu_cmp_end for measured_at in lot_fdc_times))
        self.assertTrue(all(cu_cmp_start <= triggered_at <= cu_cmp_end for triggered_at in lot_ooc_times))
        self.assertGreaterEqual(lot_defect_time, inspect_end)
        self.assertGreaterEqual(lot_wat_time, wat_end)
        self.assertGreater(lot_hold_time, lot_wat_time)

    def test_dataset_generation_is_reproducible(self) -> None:
        first_truth = (SEED_DIR / "ground_truth.json").read_text(encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_synthetic_fab_data.py"),
                "--output-dir",
                str(SEED_DIR),
            ],
            check=True,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
        )
        second_truth = (SEED_DIR / "ground_truth.json").read_text(encoding="utf-8")
        self.assertEqual(first_truth, second_truth)


if __name__ == "__main__":
    unittest.main()
