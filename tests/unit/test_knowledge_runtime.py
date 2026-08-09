from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    DeterministicHashEmbeddingBackend,
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    HybridRetrievalConfigurationError,
    PythonBM25CandidateSource,
)
from yield_rca_core.knowledge_lookup import DocumentChunkKeywordRetriever  # noqa: E402
from yield_rca_core.knowledge_retrieval import (  # noqa: E402
    RetrievalQuery,
    TypedKnowledgeRetrieverAdapter,
)
from yield_rca_core.knowledge_runtime import (  # noqa: E402
    KnowledgeRetrievalSettings,
    build_knowledge_retriever,
)
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.reranking import RerankedKnowledgeRetriever  # noqa: E402
from yield_rca_core.specialist_agents import KnowledgeAgent  # noqa: E402
from yield_rca_core.tool_layer import RetrieveSimilarCaseTool  # noqa: E402

CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"


class KnowledgeRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = load_builtin_knowledge_store(CORPUS_DIR / "corpus.json")

    def test_keyword_is_safe_default(self) -> None:
        retriever = build_knowledge_retriever(
            self.store,
            settings=KnowledgeRetrievalSettings(),
        )

        self.assertIsInstance(retriever, DocumentChunkKeywordRetriever)

    def test_hybrid_factory_is_lazy_and_reranker_is_separately_flagged(self) -> None:
        hybrid = build_knowledge_retriever(
            self.store,
            settings=KnowledgeRetrievalSettings(mode="hybrid"),
        )
        reranked = build_knowledge_retriever(
            self.store,
            settings=KnowledgeRetrievalSettings(
                mode="hybrid",
                reranker_enabled=True,
            ),
        )

        self.assertIsInstance(hybrid, HybridDocumentChunkRetriever)
        self.assertIsInstance(reranked, RerankedKnowledgeRetriever)
        assert isinstance(hybrid, HybridDocumentChunkRetriever)
        self.assertIsNone(hybrid.vector_source.embedding_backend._model)

    def test_reranker_cannot_be_enabled_on_keyword_mode(self) -> None:
        with self.assertRaisesRegex(
            HybridRetrievalConfigurationError,
            "requires.*hybrid",
        ):
            KnowledgeRetrievalSettings(reranker_enabled=True)

    def test_rca_knowledge_agent_consumes_typed_logical_asset_scores(self) -> None:
        hybrid = HybridDocumentChunkRetriever(
            PythonBM25CandidateSource(self.store),
            ExactVectorCandidateSource(
                self.store,
                DeterministicHashEmbeddingBackend(),
            ),
        )
        repository = CsvFabRepository(CORPUS_DIR)
        adapter = TypedKnowledgeRetrieverAdapter(repository, hybrid)
        direct = adapter.retrieve(
            RetrievalQuery(
                query="random scratch burst after Cu CMP polish",
                module="Cu CMP",
                equipment_type="CMP",
            )
        )
        finding = KnowledgeAgent(
            RetrieveSimilarCaseTool(repository, retriever=adapter)
        ).analyze(
            request_id="REQ_TYPED_KNOWLEDGE",
            query="random scratch burst after Cu CMP polish",
            module="Cu CMP",
            equipment_type="CMP",
        )

        self.assertTrue(direct.hits)
        self.assertEqual(direct.hits[0].retrieval_strategy, "rrf_hybrid")
        self.assertIn("fusion", direct.hits[0].score_components)
        self.assertEqual(finding.details["retrieval_strategy"], "rrf_hybrid")
        self.assertIn("fusion", finding.details["score_components"])
        self.assertIsNone(finding.details["calibrated_relevance"])
        self.assertEqual(finding.details["source_confidence"], 0.8)


if __name__ == "__main__":
    unittest.main()
