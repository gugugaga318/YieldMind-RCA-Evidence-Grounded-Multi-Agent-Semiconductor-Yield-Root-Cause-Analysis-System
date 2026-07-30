from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db/migrations/004_advanced_spc_analytics.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db/migrations/004_advanced_spc_analytics.down.sql").read_text(
    encoding="utf-8"
)


class AdvancedSpcSchemaContractTest(unittest.TestCase):
    def test_versioned_baseline_and_excursion_tables_are_reversible(self) -> None:
        for table_name in (
            "spc_baseline_profile",
            "spc_excursion",
            "spc_excursion_lot",
        ):
            self.assertIn(f"CREATE TABLE {table_name}", UP_SQL)
            self.assertIn(f"DROP TABLE IF EXISTS {table_name}", DOWN_SQL)

    def test_spc_ooc_requires_trigger_lot_hold_excursion_and_rules(self) -> None:
        self.assertIn("ADD CONSTRAINT ck_spc_ooc_trigger_context", UP_SQL)
        self.assertIn("trigger_lot_id IS NOT NULL", UP_SQL)
        self.assertIn("trigger_hold_id IS NOT NULL", UP_SQL)
        self.assertIn("excursion_id IS NOT NULL", UP_SQL)
        self.assertIn("cardinality(spc_rule_codes) > 0", UP_SQL)
        self.assertIn("DROP CONSTRAINT IF EXISTS ck_spc_ooc_trigger_context", DOWN_SQL)

    def test_excursion_scope_distinguishes_trigger_and_impact_lots(self) -> None:
        self.assertIn("scope_role IN ('TRIGGER', 'IMPACT')", UP_SQL)
        self.assertIn("hold_id text NOT NULL REFERENCES hold_history(hold_id)", UP_SQL)
        self.assertIn("PRIMARY KEY (excursion_id, lot_id)", UP_SQL)
        self.assertIn("CREATE UNIQUE INDEX uq_spc_excursion_single_trigger", UP_SQL)


if __name__ == "__main__":
    unittest.main()
