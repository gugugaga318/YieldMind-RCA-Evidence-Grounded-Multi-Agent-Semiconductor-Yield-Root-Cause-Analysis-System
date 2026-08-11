from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import seed_database

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"


class SeedDatabaseTest(unittest.TestCase):
    def test_normalize_value_preserves_non_null_knowledge_filters(self) -> None:
        for field_name in (
            "module",
            "equipment_type",
            "operation",
            "defect_type",
        ):
            self.assertEqual(
                seed_database.normalize_value(
                    "knowledge_document",
                    field_name,
                    "",
                ),
                "",
            )
        self.assertIsNone(
            seed_database.normalize_value("knowledge_document", "case_id", "")
        )

    def test_builtin_knowledge_normalizes_nullable_filter_metadata(self) -> None:
        with patch.object(seed_database, "insert_rows") as insert_rows:
            seed_database.seed_builtin_knowledge(object(), CORPUS)

        knowledge_call = next(
            call
            for call in insert_rows.call_args_list
            if call.args[1] == "knowledge_document"
        )
        documents = knowledge_call.args[2]
        self.assertTrue(documents)
        for document in documents:
            for field_name in (
                "module",
                "equipment_type",
                "operation",
                "defect_type",
            ):
                self.assertIsInstance(document[field_name], str)


if __name__ == "__main__":
    unittest.main()
