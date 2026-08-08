"""Approval-gated BM25, exact-vector, and RRF knowledge retrieval.

Long Task 3 deliberately keeps these retrievers independent from the online
cutover.  They consume only the Active Index exposed by ``KnowledgeStore`` and
therefore cannot make staged or rejected knowledge visible.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from math import log, sqrt
from typing import Literal, Protocol

from yield_rca_core.knowledge_ingestion import KnowledgeStore
from yield_rca_core.knowledge_models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeLookupHit,
    KnowledgeLookupPlan,
)

_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_+-]+|[\u3400-\u9fff]+")
_CJK_PATTERN = re.compile(r"^[\u3400-\u9fff]+$")
_STOP_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "case",
        "find",
        "for",
        "from",
        "in",
        "of",
        "or",
        "please",
        "retrieve",
        "show",
        "the",
        "to",
        "what",
        "which",
        "with",
        "一个",
        "什么",
        "可以",
        "如何",
        "查找",
        "检索",
        "相关",
        "请",
    }
)


class HybridRetrievalConfigurationError(RuntimeError):
    """Raised when an optional retrieval runtime is not installed or usable."""


def tokenize_knowledge_text(value: str) -> tuple[str, ...]:
    """Tokenize English and CJK text without a network model.

    CJK runs emit unigrams and bigrams.  This is intentionally deterministic;
    cross-language semantics remains the responsibility of the Embedding
    backend rather than a hand-written translation table.
    """

    tokens: list[str] = []
    for match in _SEGMENT_PATTERN.finditer(value.casefold()):
        segment = match.group(0)
        if _CJK_PATTERN.fullmatch(segment):
            tokens.extend(segment)
            tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        else:
            tokens.append(segment)
    return tuple(token for token in tokens if token and token not in _STOP_TOKENS)


def document_in_scope(document: KnowledgeDocument, plan: KnowledgeLookupPlan) -> bool:
    """Apply Python-owned document type and metadata filters."""

    if document.validation_status != "CONFIRMED":
        return False
    if document.document_type not in plan.allowed_document_types:
        return False
    for actual, expected in (
        (document.module, plan.module),
        (document.equipment_type, plan.equipment_type),
        (document.operation, plan.operation),
        (document.defect_type, plan.defect_type),
    ):
        if expected and actual.strip().casefold() != expected.strip().casefold():
            return False
    expected_tags = {item.casefold() for item in plan.tags}
    actual_tags = {item.casefold() for item in document.tags}
    return expected_tags <= actual_tags


def _metadata_bonus(document: KnowledgeDocument, plan: KnowledgeLookupPlan) -> float:
    matched = sum(
        1
        for actual, expected in (
            (document.module, plan.module),
            (document.equipment_type, plan.equipment_type),
            (document.operation, plan.operation),
            (document.defect_type, plan.defect_type),
        )
        if expected and actual.strip().casefold() == expected.strip().casefold()
    )
    return min(0.12, matched * 0.025 + len(plan.tags) * 0.01)


def _searchable_text(document: KnowledgeDocument, chunk: KnowledgeChunk) -> str:
    return " ".join(
        (
            document.title,
            document.module,
            document.equipment_type,
            document.operation,
            document.defect_type,
            " ".join(document.tags),
            chunk.heading,
            chunk.content,
        )
    )


@dataclass(frozen=True)
class RankedKnowledgeChunk:
    """One ranked Active-Index chunk with stage-specific scores."""

    document: KnowledgeDocument
    chunk: KnowledgeChunk
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0
    fusion_score: float = 0.0
    matched_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.score, self.lexical_score, self.vector_score, self.fusion_score):
            if not 0.0 <= value <= 1.0:
                raise ValueError("retrieval scores must be between 0 and 1")


class KnowledgeLookupRetriever(Protocol):
    """Retriever contract accepted by the independent Knowledge service."""

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]: ...


class ChunkCandidateSource(Protocol):
    """Internal chunk candidate source used before logical-asset aggregation."""

    name: str

    def rank(
        self,
        plan: KnowledgeLookupPlan,
        *,
        limit: int,
    ) -> tuple[RankedKnowledgeChunk, ...]: ...


class PythonBM25CandidateSource:
    """Exact Okapi BM25 over the small in-memory Active Index."""

    name = "python_okapi_bm25"

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 requires k1 > 0 and 0 <= b <= 1")
        self.store = store
        self.k1 = k1
        self.b = b

    def rank(
        self,
        plan: KnowledgeLookupPlan,
        *,
        limit: int,
    ) -> tuple[RankedKnowledgeChunk, ...]:
        query_terms = tokenize_knowledge_text(plan.query)
        if not query_terms or limit < 1:
            return ()
        documents = {
            item.document_id: item
            for item in self.store.active_documents()
            if document_in_scope(item, plan)
        }
        rows: list[tuple[KnowledgeDocument, KnowledgeChunk, Counter[str]]] = []
        document_frequency: Counter[str] = Counter()
        for chunk in self.store.active_chunks():
            if (
                chunk.validation_status != "CONFIRMED"
                or chunk.document_id is None
                or chunk.document_id not in documents
            ):
                continue
            frequencies = Counter(
                tokenize_knowledge_text(_searchable_text(documents[chunk.document_id], chunk))
            )
            rows.append((documents[chunk.document_id], chunk, frequencies))
            document_frequency.update(set(frequencies))
        if not rows:
            return ()

        average_length = sum(sum(item.values()) for _, _, item in rows) / len(rows)
        raw_rankings: list[
            tuple[KnowledgeDocument, KnowledgeChunk, float, tuple[str, ...]]
        ] = []
        query_frequency = Counter(query_terms)
        corpus_size = len(rows)
        for document, chunk, frequencies in rows:
            matched = tuple(sorted(set(query_terms) & set(frequencies)))
            if not matched:
                continue
            document_length = sum(frequencies.values())
            score = 0.0
            for term, query_count in query_frequency.items():
                term_frequency = frequencies.get(term, 0)
                if term_frequency == 0:
                    continue
                frequency = document_frequency[term]
                inverse_document_frequency = log(
                    1.0 + (corpus_size - frequency + 0.5) / (frequency + 0.5)
                )
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * document_length / max(average_length, 1.0)
                )
                score += (
                    inverse_document_frequency
                    * term_frequency
                    * (self.k1 + 1.0)
                    / denominator
                    * query_count
                )
            score *= 1.0 + _metadata_bonus(document, plan)
            raw_rankings.append((document, chunk, score, matched))
        if not raw_rankings:
            return ()

        maximum = max(item[2] for item in raw_rankings)
        normalized = [
            RankedKnowledgeChunk(
                document=document,
                chunk=chunk,
                score=round(score / maximum, 6),
                lexical_score=round(score / maximum, 6),
                matched_tokens=matched,
            )
            for document, chunk, score, matched in raw_rankings
        ]
        return tuple(
            sorted(
                normalized,
                key=lambda item: (
                    -item.score,
                    item.document.evaluation_asset_id,
                    item.chunk.chunk_id,
                ),
            )[:limit]
        )


class PostgresBM25CandidateSource:
    """PostgreSQL FTS candidate source using ``ts_rank_cd``.

    PostgreSQL does not expose native Okapi BM25 in core.  The implementation is
    therefore named BM25-style and uses migration 008's generated ``tsvector``
    plus GIN index for production candidate generation.
    """

    name = "postgres_bm25_style"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def rank(
        self,
        plan: KnowledgeLookupPlan,
        *,
        limit: int,
    ) -> tuple[RankedKnowledgeChunk, ...]:
        if limit < 1:
            return ()
        query_terms = tokenize_knowledge_text(plan.query)
        if not query_terms:
            return ()
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - project dependency in runtime
            raise HybridRetrievalConfigurationError(
                "psycopg is required for PostgreSQL FTS"
            ) from exc

        from yield_rca_core.knowledge_store import _chunk_from_row, _document_from_row

        sql = """
            WITH search_query AS (
                SELECT plainto_tsquery('simple', %s) AS value
            )
            SELECT row_to_json(kc), row_to_json(kd),
                   COALESCE(NULLIF(kd.module, ''), rc.module, '') AS resolved_module,
                   COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, '')
                       AS resolved_equipment_type,
                   ts_rank_cd(kc.search_vector, search_query.value, 32) AS lexical_score
            FROM active_knowledge_chunk kc
            JOIN knowledge_document kd ON kd.document_id = kc.document_id
            LEFT JOIN rca_case rc ON rc.case_id = kd.case_id
            CROSS JOIN search_query
            WHERE kc.search_vector @@ search_query.value
              AND kd.document_type = ANY(%s)
              AND (%s = '' OR lower(COALESCE(NULLIF(kd.module, ''), rc.module, '')) = lower(%s))
              AND (
                  %s = ''
                  OR lower(COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, ''))
                     = lower(%s)
              )
              AND (%s = '' OR lower(kd.operation) = lower(%s))
              AND (%s = '' OR lower(kd.defect_type) = lower(%s))
              AND (%s::text[] = ARRAY[]::text[] OR kd.tags @> %s::text[])
            ORDER BY lexical_score DESC, kd.document_id, kc.chunk_id
            LIMIT %s
        """
        parameters = (
            " ".join(dict.fromkeys(query_terms)),
            list(plan.allowed_document_types),
            plan.module,
            plan.module,
            plan.equipment_type,
            plan.equipment_type,
            plan.operation,
            plan.operation,
            plan.defect_type,
            plan.defect_type,
            list(plan.tags),
            list(plan.tags),
            limit,
        )
        try:
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, parameters)
                    raw_rows = cursor.fetchall()
        except Exception as exc:
            if "search_vector" in str(exc) or "008_hybrid_retrieval" in str(exc):
                raise HybridRetrievalConfigurationError(
                    "PostgreSQL hybrid retrieval migration 008 is not applied"
                ) from exc
            raise
        if not raw_rows:
            return ()
        maximum = max(float(row[4]) for row in raw_rows)
        results: list[RankedKnowledgeChunk] = []
        for chunk_row, document_row, module, equipment_type, raw_score in raw_rows:
            document_data = dict(document_row)
            document_data["resolved_module"] = module
            document_data["resolved_equipment_type"] = equipment_type
            document = _document_from_row(document_data)
            chunk = _chunk_from_row(dict(chunk_row))
            normalized = float(raw_score) / max(maximum, 1e-12)
            results.append(
                RankedKnowledgeChunk(
                    document=document,
                    chunk=chunk,
                    score=round(normalized, 6),
                    lexical_score=round(normalized, 6),
                    matched_tokens=tuple(
                        sorted(
                            set(query_terms)
                            & set(tokenize_knowledge_text(_searchable_text(document, chunk)))
                        )
                    ),
                )
            )
        return tuple(results)


EmbeddingInputKind = Literal["query", "document"]


class EmbeddingBackend(Protocol):
    """Multilingual Embedding contract independent of vector storage."""

    model_name: str
    model_revision: str
    device: str

    def encode(
        self,
        texts: Sequence[str],
        *,
        kind: EmbeddingInputKind,
    ) -> tuple[tuple[float, ...], ...]: ...


class SentenceTransformerEmbeddingBackend:
    """Lazy sentence-transformers backend with CUDA-first ``device=auto``."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        device: str = "auto",
        batch_size: int = 32,
        query_prefix: str = "",
        document_prefix: str = "",
        revision: str | None = None,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"} and not device.startswith("cuda:"):
            raise ValueError("embedding device must be auto, cpu, cuda, or cuda:<index>")
        if batch_size < 1:
            raise ValueError("embedding batch_size must be positive")
        self.model_name = model_name.strip()
        self.model_revision = revision or "main"
        self.requested_device = device
        self.device = device
        self.batch_size = batch_size
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self._model: object | None = None

    def _load_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            torch = import_module("torch")
            SentenceTransformer = import_module(
                "sentence_transformers"
            ).SentenceTransformer
        except ImportError as exc:
            raise HybridRetrievalConfigurationError(
                "install the retrieval extra to use sentence-transformers: "
                "pip install -e '.[retrieval]'"
            ) from exc
        if self.requested_device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        elif self.requested_device.startswith("cuda") and not torch.cuda.is_available():
            raise HybridRetrievalConfigurationError(
                f"embedding device {self.requested_device!r} requested but CUDA is unavailable"
            )
        else:
            self.device = self.requested_device
        self._model = SentenceTransformer(
            self.model_name,
            device=self.device,
            revision=self.model_revision,
        )
        return self._model

    def encode(
        self,
        texts: Sequence[str],
        *,
        kind: EmbeddingInputKind,
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        prefix = self.query_prefix if kind == "query" else self.document_prefix
        payload = [f"{prefix}{text}" for text in texts]
        model = self._load_model()
        encoded = model.encode(  # type: ignore[attr-defined]
            payload,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return tuple(tuple(float(value) for value in row) for row in encoded.tolist())


class DeterministicHashEmbeddingBackend:
    """Dependency-free CI backend; not a substitute for semantic Embeddings."""

    model_name = "deterministic-token-hashing-v1"
    model_revision = "builtin-v1"
    device = "cpu"

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32:
            raise ValueError("hash embedding dimensions must be at least 32")
        self.dimensions = dimensions

    def encode(
        self,
        texts: Sequence[str],
        *,
        kind: EmbeddingInputKind,
    ) -> tuple[tuple[float, ...], ...]:
        del kind
        return tuple(self._encode_one(text) for text in texts)

    def _encode_one(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in tokenize_knowledge_text(text):
            digest = sha256(token.encode("utf-8")).digest()
            for offset in range(0, 12, 4):
                index = int.from_bytes(digest[offset : offset + 2], "big") % self.dimensions
                sign = 1.0 if digest[offset + 2] & 1 else -1.0
                vector[index] += sign
        norm = sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return tuple(vector)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise HybridRetrievalConfigurationError("query and document embedding sizes differ")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class ExactVectorCandidateSource:
    """Exact cosine search over all in-scope Active-Index chunks."""

    name = "exact_vector"

    def __init__(
        self,
        store: KnowledgeStore,
        embedding_backend: EmbeddingBackend,
        *,
        minimum_similarity: float = 0.0,
    ) -> None:
        if not 0.0 <= minimum_similarity <= 1.0:
            raise ValueError("minimum vector similarity must be between 0 and 1")
        self.store = store
        self.embedding_backend = embedding_backend
        self.minimum_similarity = minimum_similarity
        self._embedding_cache: dict[str, tuple[str, tuple[float, ...]]] = {}
        self._query_embedding_cache: dict[str, tuple[float, ...]] = {}

    def prepare_queries(self, queries: Sequence[str]) -> None:
        """Batch and cache query vectors for repeatable offline evaluation."""

        missing = tuple(
            query
            for query in dict.fromkeys(item.strip() for item in queries if item.strip())
            if query not in self._query_embedding_cache
        )
        if not missing:
            return
        encoded = self.embedding_backend.encode(missing, kind="query")
        self._query_embedding_cache.update(zip(missing, encoded, strict=True))

    def rank(
        self,
        plan: KnowledgeLookupPlan,
        *,
        limit: int,
    ) -> tuple[RankedKnowledgeChunk, ...]:
        if limit < 1:
            return ()
        documents = {
            item.document_id: item
            for item in self.store.active_documents()
            if document_in_scope(item, plan)
        }
        rows = [
            (documents[item.document_id], item)
            for item in self.store.active_chunks()
            if item.document_id in documents and item.validation_status == "CONFIRMED"
        ]
        if not rows:
            return ()
        query_vector = self._query_embedding_cache.get(plan.query)
        if query_vector is None:
            query_vector = self.embedding_backend.encode((plan.query,), kind="query")[0]
            self._query_embedding_cache[plan.query] = query_vector
        missing: list[tuple[KnowledgeDocument, KnowledgeChunk, str]] = []
        vectors: dict[str, tuple[float, ...]] = {}
        for document, chunk in rows:
            text = _searchable_text(document, chunk)
            fingerprint = sha256(text.encode("utf-8")).hexdigest()
            cached = self._embedding_cache.get(chunk.chunk_id)
            if cached is not None and cached[0] == fingerprint:
                vectors[chunk.chunk_id] = cached[1]
            else:
                missing.append((document, chunk, text))
        if missing:
            encoded = self.embedding_backend.encode(
                [text for _, _, text in missing],
                kind="document",
            )
            for (_, chunk, text), vector in zip(missing, encoded, strict=True):
                fingerprint = sha256(text.encode("utf-8")).hexdigest()
                self._embedding_cache[chunk.chunk_id] = (fingerprint, vector)
                vectors[chunk.chunk_id] = vector

        query_terms = set(tokenize_knowledge_text(plan.query))
        ranked: list[RankedKnowledgeChunk] = []
        for document, chunk in rows:
            similarity = max(0.0, min(1.0, _cosine(query_vector, vectors[chunk.chunk_id])))
            if similarity < self.minimum_similarity:
                continue
            ranked.append(
                RankedKnowledgeChunk(
                    document=document,
                    chunk=chunk,
                    score=round(similarity, 6),
                    vector_score=round(similarity, 6),
                    matched_tokens=tuple(
                        sorted(
                            query_terms
                            & set(tokenize_knowledge_text(_searchable_text(document, chunk)))
                        )
                    ),
                )
            )
        return tuple(
            sorted(
                ranked,
                key=lambda item: (
                    -item.score,
                    item.document.evaluation_asset_id,
                    item.chunk.chunk_id,
                ),
            )[:limit]
        )


def reciprocal_rank_fusion(
    lexical: Sequence[RankedKnowledgeChunk],
    vector: Sequence[RankedKnowledgeChunk],
    *,
    rrf_k: int = 60,
    lexical_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> tuple[RankedKnowledgeChunk, ...]:
    """Fuse two chunk rankings while retaining every stage score."""

    if rrf_k < 1 or lexical_weight <= 0 or vector_weight <= 0:
        raise ValueError("RRF requires positive k and branch weights")
    by_id: dict[str, RankedKnowledgeChunk] = {}
    contributions: defaultdict[str, float] = defaultdict(float)
    for rank, item in enumerate(lexical, start=1):
        by_id[item.chunk.chunk_id] = item
        contributions[item.chunk.chunk_id] += lexical_weight / (rrf_k + rank)
    for rank, item in enumerate(vector, start=1):
        current = by_id.get(item.chunk.chunk_id)
        if current is None:
            by_id[item.chunk.chunk_id] = item
        else:
            by_id[item.chunk.chunk_id] = RankedKnowledgeChunk(
                document=current.document,
                chunk=current.chunk,
                score=current.score,
                lexical_score=current.lexical_score,
                vector_score=item.vector_score,
                matched_tokens=tuple(
                    sorted(set(current.matched_tokens) | set(item.matched_tokens))
                ),
            )
        contributions[item.chunk.chunk_id] += vector_weight / (rrf_k + rank)
    maximum = (lexical_weight + vector_weight) / (rrf_k + 1)
    fused = [
        RankedKnowledgeChunk(
            document=item.document,
            chunk=item.chunk,
            score=round(contributions[chunk_id] / maximum, 6),
            lexical_score=item.lexical_score,
            vector_score=item.vector_score,
            fusion_score=round(contributions[chunk_id] / maximum, 6),
            matched_tokens=item.matched_tokens,
        )
        for chunk_id, item in by_id.items()
    ]
    return tuple(
        sorted(
            fused,
            key=lambda item: (
                -item.fusion_score,
                item.document.evaluation_asset_id,
                item.chunk.chunk_id,
            ),
        )
    )


def _logical_asset_id(document: KnowledgeDocument) -> str:
    if document.document_type == KnowledgeDocumentType.RCA_CASE.value and document.case_id:
        return str(document.case_id)
    return str(document.document_id)


def _aggregate_hits(
    rankings: Sequence[RankedKnowledgeChunk],
    plan: KnowledgeLookupPlan,
    *,
    lookup_id: str,
    strategy: str,
) -> tuple[KnowledgeLookupHit, ...]:
    grouped: defaultdict[str, list[RankedKnowledgeChunk]] = defaultdict(list)
    for item in rankings:
        grouped[_logical_asset_id(item.document)].append(item)
    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (-max(row.score for row in item[1]), item[0]),
    )[: plan.top_k]
    hits: list[KnowledgeLookupHit] = []
    for rank, (_, group) in enumerate(ordered_groups, start=1):
        ordered = sorted(group, key=lambda item: (-item.score, item.chunk.chunk_id))
        best = ordered[0]
        components = {
            "lexical": round(max(item.lexical_score for item in group), 6),
            "vector": round(max(item.vector_score for item in group), 6),
            "fusion": round(max(item.fusion_score for item in group), 6),
        }
        matched = sorted({token for item in ordered[:3] for token in item.matched_tokens})
        explanation = f"Python {strategy} ranking over approved, in-scope Knowledge Chunks"
        if matched:
            explanation += "; lexical overlap: " + ", ".join(matched[:12])
        hits.append(
            KnowledgeLookupHit(
                rank=rank,
                document=best.document,
                score=best.score,
                matched_chunk_ids=tuple(item.chunk.chunk_id for item in ordered[:3]),
                excerpt=best.chunk.content[:600],
                evidence_id=f"KEV_{lookup_id.removeprefix('KLOOK_')}_{rank:03d}",
                relevance_reason=explanation,
                retrieval_strategy=strategy,
                score_components=components,
            )
        )
    return tuple(hits)


class BM25DocumentChunkRetriever:
    """Logical-asset Retriever backed by a lexical chunk candidate source."""

    def __init__(self, source: ChunkCandidateSource, *, candidate_k: int = 80) -> None:
        self.source = source
        self.candidate_k = candidate_k

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        rankings = self.source.rank(plan, limit=max(self.candidate_k, plan.top_k * 4))
        return _aggregate_hits(
            rankings,
            plan,
            lookup_id=lookup_id,
            strategy=self.source.name,
        )


class VectorDocumentChunkRetriever:
    """Logical-asset Retriever using exact cosine search."""

    def __init__(self, source: ExactVectorCandidateSource, *, candidate_k: int = 80) -> None:
        self.source = source
        self.candidate_k = candidate_k

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        rankings = self.source.rank(plan, limit=max(self.candidate_k, plan.top_k * 4))
        return _aggregate_hits(
            rankings,
            plan,
            lookup_id=lookup_id,
            strategy=self.source.name,
        )


class HybridDocumentChunkRetriever:
    """Two-branch candidate retrieval followed by deterministic RRF."""

    def __init__(
        self,
        lexical_source: ChunkCandidateSource,
        vector_source: ExactVectorCandidateSource,
        *,
        candidate_k: int = 80,
        rrf_k: int = 60,
        lexical_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> None:
        self.lexical_source = lexical_source
        self.vector_source = vector_source
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.lexical_weight = lexical_weight
        self.vector_weight = vector_weight

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        limit = max(self.candidate_k, plan.top_k * 4)
        lexical = self.lexical_source.rank(plan, limit=limit)
        vector = self.vector_source.rank(plan, limit=limit)
        fused = reciprocal_rank_fusion(
            lexical,
            vector,
            rrf_k=self.rrf_k,
            lexical_weight=self.lexical_weight,
            vector_weight=self.vector_weight,
        )
        return _aggregate_hits(
            fused,
            plan,
            lookup_id=lookup_id,
            strategy="rrf_hybrid",
        )
