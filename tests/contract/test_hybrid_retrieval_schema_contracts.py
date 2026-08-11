from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db/migrations/008_hybrid_retrieval.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db/migrations/008_hybrid_retrieval.down.sql").read_text(
    encoding="utf-8"
)


class HybridRetrievalSchemaContractTest(unittest.TestCase):
    def test_migration_adds_generated_fts_vector_and_gin_index(self) -> None:
        self.assertIn("search_vector tsvector GENERATED ALWAYS", UP_SQL)
        self.assertIn("to_tsvector", UP_SQL)
        self.assertIn("USING gin(search_vector)", UP_SQL)
        self.assertIn("008_hybrid_retrieval", UP_SQL)

    def test_down_migration_removes_only_long_task_three_schema(self) -> None:
        self.assertIn("DROP INDEX IF EXISTS idx_knowledge_chunk_search_vector", DOWN_SQL)
        self.assertIn("DROP COLUMN IF EXISTS search_vector", DOWN_SQL)
        self.assertIn("ALTER TABLE IF EXISTS knowledge_chunk", DOWN_SQL)
        self.assertNotIn("DROP TABLE", DOWN_SQL)


if __name__ == "__main__":
    unittest.main()
