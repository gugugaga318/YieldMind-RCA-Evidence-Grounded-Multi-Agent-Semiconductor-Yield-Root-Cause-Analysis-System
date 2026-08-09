from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db" / "migrations" / "001_initial_schema.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db" / "migrations" / "001_initial_schema.down.sql").read_text(
    encoding="utf-8"
)
WAT_PROVENANCE_UP_SQL = (
    ROOT / "db" / "migrations" / "010_wat_test_equipment_provenance.up.sql"
).read_text(encoding="utf-8")
WAT_PROVENANCE_DOWN_SQL = (
    ROOT / "db" / "migrations" / "010_wat_test_equipment_provenance.down.sql"
).read_text(encoding="utf-8")


REQUIRED_TABLES = [
    "lot_master",
    "wafer_master",
    "process_route",
    "operation_master",
    "process_history",
    "equipment_master",
    "equipment_capability",
    "chamber_master",
    "recipe_master",
    "recipe_history",
    "hold_history",
    "fdc_feature",
    "ooc_event",
    "defect_summary",
    "wat_result",
    "rca_case",
    "knowledge_document",
]


def table_block(table_name: str) -> str:
    match = re.search(
        rf"CREATE TABLE {table_name} \((.*?)\);\n",
        UP_SQL,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        raise AssertionError(f"table block not found: {table_name}")
    return match.group(1)


class SchemaContractTest(unittest.TestCase):
    def test_wat_test_equipment_provenance_has_reversible_foreign_key_migration(self) -> None:
        self.assertIn("ADD COLUMN test_equipment_id text", WAT_PROVENANCE_UP_SQL)
        self.assertIn("REFERENCES equipment_master(equipment_id)", WAT_PROVENANCE_UP_SQL)
        self.assertIn("idx_wat_result_test_equipment", WAT_PROVENANCE_UP_SQL)
        self.assertIn("DROP COLUMN IF EXISTS test_equipment_id", WAT_PROVENANCE_DOWN_SQL)

    def test_required_tables_exist_in_up_and_down_migrations(self) -> None:
        for table_name in REQUIRED_TABLES:
            self.assertIn(f"CREATE TABLE {table_name}", UP_SQL)
            self.assertIn(f"DROP TABLE IF EXISTS {table_name}", DOWN_SQL)

    def test_process_history_links_operation_equipment_chamber_recipe_version(self) -> None:
        block = table_block("process_history")

        self.assertIn("operation_no text NOT NULL", block)
        self.assertIn("equipment_id text NOT NULL", block)
        self.assertIn("chamber_id text", block)
        self.assertIn("recipe_id text NOT NULL", block)
        self.assertIn("recipe_version text NOT NULL", block)
        self.assertIn("REFERENCES process_route(route_id, operation_no)", block)
        self.assertIn("REFERENCES chamber_master(equipment_id, chamber_id)", block)
        self.assertIn("REFERENCES recipe_master(recipe_id, recipe_version)", block)

    def test_equipment_capability_prevents_unconstrained_equipment_assignment(self) -> None:
        block = table_block("equipment_capability")

        self.assertIn("operation_no text NOT NULL REFERENCES operation_master(operation_no)", block)
        self.assertIn("recipe_family text NOT NULL", block)
        self.assertIn("qualification_status text NOT NULL", block)
        self.assertIn("UNIQUE (equipment_id, chamber_id, operation_no, recipe_family)", block)

    def test_recipe_master_uses_recipe_id_and_version_as_key(self) -> None:
        block = table_block("recipe_master")

        self.assertIn("recipe_id text NOT NULL", block)
        self.assertIn("recipe_version text NOT NULL", block)
        self.assertIn("PRIMARY KEY (recipe_id, recipe_version)", block)

    def test_fdc_feature_is_summary_only_not_raw_stream(self) -> None:
        self.assertIn("CREATE TABLE fdc_feature", UP_SQL)
        self.assertNotIn("fdc_raw", UP_SQL.lower())
        self.assertNotIn("sensor_stream", UP_SQL.lower())

        block = table_block("fdc_feature")
        for field_name in [
            "baseline_value",
            "observed_value",
            "delta_percent",
            "trend_slope",
            "ooc_flag",
            "severity",
        ]:
            self.assertIn(field_name, block)

    def test_indexes_cover_key_tool_queries(self) -> None:
        required_indexes = [
            "idx_process_history_lot_operation",
            "idx_process_history_equipment_chamber",
            "idx_process_history_recipe",
            "idx_fdc_feature_equipment_parameter",
            "idx_defect_summary_lot_type",
            "idx_wat_result_lot_fail_mode",
        ]

        for index_name in required_indexes:
            self.assertIn(index_name, UP_SQL)
            self.assertIn(f"DROP INDEX IF EXISTS {index_name}", DOWN_SQL)


if __name__ == "__main__":
    unittest.main()
