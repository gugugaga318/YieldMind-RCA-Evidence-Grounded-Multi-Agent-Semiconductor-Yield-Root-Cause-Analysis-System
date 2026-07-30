from __future__ import annotations

import ast
import csv
import unittest
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seeds" / "spc_case"
GENERATOR = ROOT / "scripts" / "generate_synthetic_spc_data.py"


def read_csv(table_name: str) -> list[dict[str, str]]:
    with (SEED_DIR / f"{table_name}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class SpcDatasetContractTest(unittest.TestCase):
    def test_generator_is_offline_and_lot_names_remain_product_based(self) -> None:
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
        self.assertEqual(
            [row["lot_id"] for row in read_csv("lot_master")],
            [f"LOT_A_{number:03d}" for number in range(1, 106)],
        )

    def test_baseline_lots_run_complete_route_on_qualified_equipment(self) -> None:
        route = [row["operation_no"] for row in read_csv("process_route")]
        qualified = {
            (row["equipment_id"], row["chamber_id"], row["operation_no"])
            for row in read_csv("equipment_capability")
            if row["qualification_status"] == "QUALIFIED"
        }
        operations_by_wafer: dict[str, list[str]] = defaultdict(list)
        for row in read_csv("process_history"):
            if 76 <= int(row["lot_id"].rsplit("_", 1)[-1]) <= 105:
                operations_by_wafer[row["wafer_id"]].append(row["operation_no"])
                self.assertIn(
                    (row["equipment_id"], row["chamber_id"], row["operation_no"]),
                    qualified,
                )
        self.assertEqual(len(operations_by_wafer), 30 * 25)
        self.assertTrue(all(operations == route for operations in operations_by_wafer.values()))

    def test_versioned_baselines_use_strict_cu_cmp_group(self) -> None:
        profiles = read_csv("spc_baseline_profile")
        self.assertEqual({row["chart_type"] for row in profiles}, {"I_MR", "XBAR_S", "P"})
        for row in profiles:
            self.assertEqual(row["product_id"], "40N_SOC")
            self.assertEqual(row["operation_no"], "6400")
            self.assertEqual(row["equipment_id"], "CMP_CU03")
            self.assertEqual(row["chamber_id"], "CMP_CU03_CH02")
            self.assertEqual((row["recipe_id"], row["recipe_version"]), ("CU_CMP_40N", "R18"))
            self.assertLess(
                datetime.fromisoformat(row["baseline_end"]),
                datetime.fromisoformat("2026-07-01T00:00:00+00:00"),
            )

    def test_one_spc_ooc_has_one_trigger_lot_and_all_scoped_lots_have_holds(self) -> None:
        events = [row for row in read_csv("ooc_event") if row["event_source"] == "SPC"]
        scopes = read_csv("spc_excursion_lot")
        holds = {row["hold_id"]: row for row in read_csv("hold_history")}

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["trigger_lot_id"], "LOT_A_015")
        self.assertEqual(event["trigger_wafer_id"], "")
        self.assertIn(event["trigger_hold_id"], holds)
        trigger_scopes = [row for row in scopes if row["scope_role"] == "TRIGGER"]
        impact_scopes = [row for row in scopes if row["scope_role"] == "IMPACT"]
        self.assertEqual(len(trigger_scopes), 1)
        self.assertEqual(trigger_scopes[0]["lot_id"], event["trigger_lot_id"])
        self.assertEqual(len(impact_scopes), 4)
        self.assertNotIn(event["trigger_lot_id"], {row["lot_id"] for row in impact_scopes})
        self.assertTrue(all(row["hold_id"] in holds for row in scopes))
        self.assertTrue(
            all(holds[row["hold_id"]]["lot_id"] == row["lot_id"] for row in scopes)
        )
        self.assertTrue(all(holds[row["hold_id"]]["wafer_id"] == "" for row in scopes))


if __name__ == "__main__":
    unittest.main()
