from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db/migrations/009_pgvector_knowledge_index.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db/migrations/009_pgvector_knowledge_index.down.sql").read_text(
    encoding="utf-8"
)


class PgvectorKnowledgeSchemaContractTest(unittest.TestCase):
    def test_exact_vector_schema_is_versioned_without_ann_index(self) -> None:
        normalized = UP_SQL.casefold()
        self.assertIn("create extension if not exists vector", normalized)
        self.assertIn("embedding vector(1024)", normalized)
        self.assertIn("embedding_model", normalized)
        self.assertIn("embedding_revision", normalized)
        self.assertIn("embedding_input_sha256", normalized)
        self.assertIn("009_pgvector_knowledge_index", normalized)
        self.assertNotIn("ivfflat", normalized.replace("does not create ivfflat", ""))
        self.assertNotIn("using hnsw", normalized)

    def test_downgrade_retains_shared_vector_extension(self) -> None:
        normalized = DOWN_SQL.casefold()
        self.assertIn("drop column if exists embedding", normalized)
        self.assertNotIn("drop extension", normalized)
        self.assertIn("create view active_knowledge_chunk", normalized)


if __name__ == "__main__":
    unittest.main()
