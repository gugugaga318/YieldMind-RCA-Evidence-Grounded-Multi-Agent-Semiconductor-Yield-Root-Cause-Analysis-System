from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.synthetic_knowledge import (  # noqa: E402
    QwenSyntheticKnowledgeTextProvider,
    SyntheticKnowledgeError,
)


class FakeBatchClient:
    provider = "fake"
    model = "fake-qwen"

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def complete_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if request.prompt_name == "synthetic_knowledge_document":
            data = {
                "documents": [
                    {
                        "generation_key": item["generation_key"],
                        "title": f"Generated {item['generation_key']}",
                        "content": f"Synthetic content for {item['generation_key']}",
                    }
                    for item in request.payload["documents"]
                ]
            }
        else:
            data = {
                "queries": [
                    {
                        "query_key": item["query_key"],
                        "text": f"Query for {item['observable_context_en']}",
                    }
                    for item in request.payload["queries"]
                ]
            }
        return SimpleNamespace(data=data)


class QwenSyntheticKnowledgeProviderTest(unittest.TestCase):
    def test_qwen_provider_batches_documents_and_queries_with_separate_prompts(self) -> None:
        client = FakeBatchClient()
        provider = QwenSyntheticKnowledgeTextProvider(
            client,
            batch_size=2,
            max_paid_calls=2,
        )
        documents = provider.generate_documents(
            [
                {"generation_key": "D1"},
                {"generation_key": "D2"},
            ]
        )
        queries = provider.generate_queries(
            [
                {"query_key": "Q1", "observable_context_en": "scratch"},
                {"query_key": "Q2", "observable_context_en": "residue"},
            ]
        )

        self.assertEqual([item.generation_key for item in documents], ["D1", "D2"])
        self.assertEqual([item.query_key for item in queries], ["Q1", "Q2"])
        self.assertEqual(provider.paid_call_count, 2)
        self.assertEqual(
            [request.prompt_name for request in client.requests],
            ["synthetic_knowledge_document", "synthetic_knowledge_query"],
        )

    def test_qwen_provider_enforces_visible_paid_call_cap(self) -> None:
        provider = QwenSyntheticKnowledgeTextProvider(
            FakeBatchClient(),
            batch_size=1,
            max_paid_calls=1,
        )

        with self.assertRaisesRegex(SyntheticKnowledgeError, "paid LLM-call cap"):
            provider.generate_documents(
                [
                    {"generation_key": "D1"},
                    {"generation_key": "D2"},
                ]
            )


if __name__ == "__main__":
    unittest.main()
