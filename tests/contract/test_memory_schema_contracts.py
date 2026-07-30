from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db" / "migrations" / "003_memory_approval.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db" / "migrations" / "003_memory_approval.down.sql").read_text(
    encoding="utf-8"
)


class MemorySchemaContractTest(unittest.TestCase):
    def test_candidate_and_dual_approval_tables_are_migrated(self) -> None:
        self.assertIn("CREATE TABLE memory_candidate", UP_SQL)
        self.assertIn("CREATE TABLE memory_approval", UP_SQL)
        self.assertIn("UNIQUE (candidate_id, engineer_id)", UP_SQL)
        self.assertIn("requires_process_engineer_approval", UP_SQL)
        self.assertIn("validation_status", UP_SQL)
        self.assertIn("MEMORY_CANDIDATE_PUBLISHED", UP_SQL)

    def test_memory_migration_is_reversible(self) -> None:
        self.assertIn("DROP TABLE IF EXISTS memory_approval", DOWN_SQL)
        self.assertIn("DROP TABLE IF EXISTS memory_candidate", DOWN_SQL)
        self.assertIn("DROP COLUMN IF EXISTS validation_status", DOWN_SQL)


if __name__ == "__main__":
    unittest.main()
