from __future__ import annotations

import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    BM25DocumentChunkRetriever,
    DeterministicHashEmbeddingBackend,
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    PythonBM25CandidateSource,
    VectorDocumentChunkRetriever,
)
from yield_rca_core.knowledge_models import (  # noqa: E402
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402

CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"


class RecordingEmbeddingBackend:
    model_name = "recording-hash"
    model_revision = "unit-v1"
    device = "cpu"

    def __init__(self) -> None:
        self.delegate = DeterministicHashEmbeddingBackend(dimensions=128)
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def encode(
        self,
        texts: Sequence[str],
        *,
        kind: Literal["query", "document"],
    ) -> tuple[tuple[float, ...], ...]:
        payload = tuple(texts)
        self.calls.append((kind, payload))
        return self.delegate.encode(payload, kind=kind)


def plan(
    query: str,
    *,
    question_kind: str = "historical_match",
    module: str = "Cu CMP",
    equipment_type: str = "CMP",
    top_k: int = 5,
) -> KnowledgeLookupPlan:
    kind = KnowledgeQuestionKind(question_kind)
    return KnowledgeLookupPlan(
        intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
        question_kind=kind.value,
        query=query,
        allowed_document_types=(kind.document_type,),
        reason="unit test",
        module=module,
        equipment_type=equipment_type,
        top_k=top_k,
    )


class HybridRetrievalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = load_builtin_knowledge_store(CORPUS)
        self.lexical_source = PythonBM25CandidateSource(self.store)
        self.embedding = DeterministicHashEmbeddingBackend(dimensions=128)
        self.vector_source = ExactVectorCandidateSource(self.store, self.embedding)

    def test_bm25_returns_expected_case_and_exposes_lexical_score(self) -> None:
        retriever = BM25DocumentChunkRetriever(self.lexical_source)
        hits = retriever.retrieve(
            plan("repeatable radial scratch worn retaining ring"),
            lookup_id="KLOOK_BM25",
        )

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.evaluation_asset_id, "RCA_SYN_003")
        self.assertEqual(hits[0].retrieval_strategy, "python_okapi_bm25")
        self.assertGreater(hits[0].score_components["lexical"], 0)
        self.assertEqual(hits[0].score_components["vector"], 0)

    def test_vector_search_is_exact_stable_and_hard_scoped(self) -> None:
        retriever = VectorDocumentChunkRetriever(self.vector_source)
        query_plan = plan("slurry filter flow controller endpoint")
        first = retriever.retrieve(query_plan, lookup_id="KLOOK_VECTOR_1")
        second = retriever.retrieve(query_plan, lookup_id="KLOOK_VECTOR_2")

        self.assertEqual(
            [item.document.evaluation_asset_id for item in first],
            [item.document.evaluation_asset_id for item in second],
        )
        self.assertTrue(all(item.document.document_type == "RCA_CASE" for item in first))
        self.assertTrue(all(item.score_components["vector"] >= 0 for item in first))

    def test_hybrid_rrf_preserves_branch_and_fusion_scores(self) -> None:
        retriever = HybridDocumentChunkRetriever(
            self.lexical_source,
            self.vector_source,
        )
        hits = retriever.retrieve(
            plan("repeatable radial scratch retaining ring"),
            lookup_id="KLOOK_HYBRID",
        )

        self.assertTrue(hits)
        self.assertEqual(hits[0].document.evaluation_asset_id, "RCA_SYN_003")
        self.assertEqual(hits[0].retrieval_strategy, "rrf_hybrid")
        self.assertGreater(hits[0].score_components["lexical"], 0)
        self.assertGreaterEqual(hits[0].score_components["vector"], 0)
        self.assertGreater(hits[0].score_components["fusion"], 0)

    def test_scope_mismatch_abstains_instead_of_returning_other_module(self) -> None:
        retriever = HybridDocumentChunkRetriever(
            self.lexical_source,
            self.vector_source,
        )
        hits = retriever.retrieve(
            plan(
                "EUV source collector reflectivity",
                question_kind="procedure_guidance",
                module="EUV Lithography",
                equipment_type="EUV Scanner",
            ),
            lookup_id="KLOOK_NO_SCOPE",
        )

        self.assertEqual(hits, ())

    def test_hash_embedding_is_dependency_free_and_deterministic(self) -> None:
        first = self.embedding.encode(("Cu CMP scratch",), kind="query")
        second = self.embedding.encode(("Cu CMP scratch",), kind="document")

        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), 128)

    def test_exact_vector_source_reuses_query_and_document_embeddings(self) -> None:
        embedding = RecordingEmbeddingBackend()
        source = ExactVectorCandidateSource(self.store, embedding)
        retriever = VectorDocumentChunkRetriever(source)
        query_plan = plan("repeatable radial scratch retaining ring")

        source.prepare_queries((query_plan.query, query_plan.query))
        retriever.retrieve(query_plan, lookup_id="KLOOK_CACHE_1")
        first_call_count = len(embedding.calls)
        retriever.retrieve(query_plan, lookup_id="KLOOK_CACHE_2")

        self.assertEqual(len(embedding.calls), first_call_count)
        self.assertEqual(sum(kind == "query" for kind, _ in embedding.calls), 1)
        self.assertEqual(sum(kind == "document" for kind, _ in embedding.calls), 1)


if __name__ == "__main__":
    unittest.main()
