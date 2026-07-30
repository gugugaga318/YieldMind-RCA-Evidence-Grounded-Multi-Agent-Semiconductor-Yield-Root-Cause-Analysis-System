from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    AgentKind,
    KeywordRetriever,
    KnowledgeAssetRepository,
    RetrievalQuery,
    ToolInput,
)
from yield_rca_core.knowledge_retrieval import RetrievalResult  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository, Row  # noqa: E402
from yield_rca_core.tool_layer import RetrieveSimilarCaseTool  # noqa: E402

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


def knowledge_input(request_id: str) -> ToolInput:
    return ToolInput(
        tool_name="retrieve_similar_case",
        request_id=request_id,
        parameters={
            "query": "Cu CMP slurry flow scratch leakage",
            "module": "Cu CMP",
            "equipment_type": "CMP",
        },
        requested_by=AgentKind.KNOWLEDGE.value,
    )


class MixedApprovalRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "rca_case": [
                {
                    "case_id": "RCA_CONFIRMED_001",
                    "title": "Confirmed Cu CMP slurry issue",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                    "symptom": "slurry flow decline and scratch",
                    "root_cause": "slurry delivery degradation",
                    "solution": "inspect slurry delivery",
                    "confidence": "0.90",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "validation_status": "CONFIRMED",
                },
                {
                    "case_id": "RCA_DRAFT_001",
                    "title": "Draft Cu CMP slurry issue",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                    "symptom": "slurry flow decline and scratch",
                    "root_cause": "draft root cause",
                    "solution": "draft action",
                    "confidence": "0.99",
                    "created_at": "2026-06-02T00:00:00+00:00",
                    "validation_status": "DRAFT",
                },
            ],
            "knowledge_document": [
                {
                    "document_id": "DOC_CONFIRMED_001",
                    "case_id": "RCA_CONFIRMED_001",
                    "document_type": "RCA_CASE",
                    "title": "Approved RCA",
                    "content": "Approved engineering RCA record.",
                    "tags": "CMP;slurry",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "validation_status": "CONFIRMED",
                },
                {
                    "document_id": "DOC_DRAFT_001",
                    "case_id": "RCA_CONFIRMED_001",
                    "document_type": "ENGINEERING_NOTE",
                    "title": "Draft note",
                    "content": "Unapproved draft content.",
                    "tags": "draft",
                    "created_at": "2026-06-02T00:00:00+00:00",
                    "validation_status": "DRAFT",
                },
            ],
        }
        return [dict(row) for row in tables[table_name]]


class RecordingRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.queries: list[RetrievalQuery] = []

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        return self.result


class KnowledgeRetrieverContractTest(unittest.TestCase):
    def test_knowledge_asset_repository_adapts_only_confirmed_legacy_rows(self) -> None:
        assets = KnowledgeAssetRepository(MixedApprovalRepository()).confirmed_assets()

        self.assertEqual([asset.asset_id for asset in assets], ["RCA_CONFIRMED_001"])
        self.assertEqual(
            [document.document_id for document in assets[0].documents],
            ["DOC_CONFIRMED_001"],
        )

    def test_keyword_retriever_preserves_existing_top_case_ranking(self) -> None:
        retriever = KeywordRetriever(KnowledgeAssetRepository(CsvFabRepository(GOLDEN_SEED_DIR)))

        result = retriever.retrieve(
            RetrievalQuery(
                query="Cu CMP slurry flow scratch leakage",
                module="Cu CMP",
                equipment_type="CMP",
            )
        )

        self.assertIsNotNone(result.top_hit)
        assert result.top_hit is not None
        self.assertEqual(result.top_hit.asset.asset_id, "RCA_CMP_2025_032")
        self.assertGreaterEqual(result.top_hit.score, 0.9)

    def test_retrieve_similar_case_tool_delegates_ranking_to_retriever(self) -> None:
        asset = KnowledgeAssetRepository(MixedApprovalRepository()).confirmed_assets()[0]
        result = RetrievalResult(
            query=RetrievalQuery(query="ignored"),
            hits=[],
        )
        retriever = RecordingRetriever(result)

        output = RetrieveSimilarCaseTool(MixedApprovalRepository(), retriever=retriever).run(
            knowledge_input("REQ_BATCH_13_DELEGATE")
        )

        self.assertEqual(retriever.queries[0].query, "cu cmp slurry flow scratch leakage")
        self.assertIsNone(output.data["top_case"])
        self.assertEqual(output.data["cases"], [])
        self.assertEqual(asset.asset_id, "RCA_CONFIRMED_001")

    def test_tool_layer_no_longer_contains_keyword_ranking_logic(self) -> None:
        source = inspect.getsource(RetrieveSimilarCaseTool)

        self.assertNotIn('repository.rows("rca_case")', source)
        self.assertNotIn("token in searchable", source)
        self.assertNotIn("* 0.8", source)


if __name__ == "__main__":
    unittest.main()
