"""pgvector persistence and exact search for governed Knowledge Chunks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from yield_rca_core.hybrid_retrieval import (
    EmbeddingBackend,
    HybridRetrievalConfigurationError,
    RankedKnowledgeChunk,
    knowledge_chunk_text,
    tokenize_knowledge_text,
)
from yield_rca_core.knowledge_models import KnowledgeDocument, KnowledgeLookupPlan
from yield_rca_core.knowledge_store import _chunk_from_row, _document_from_row

PGVECTOR_DIMENSIONS = 1024
PGVECTOR_MIGRATION = "009_pgvector_knowledge_index"


def _vector_literal(values: tuple[float, ...]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _row_document(
    document_row: dict[str, Any],
    module: str,
    equipment_type: str,
) -> KnowledgeDocument:
    document_data = dict(document_row)
    document_data["resolved_module"] = module
    document_data["resolved_equipment_type"] = equipment_type
    return _document_from_row(document_data)


@dataclass(frozen=True)
class KnowledgeEmbeddingIndexResult:
    scanned_chunks: int
    indexed_chunks: int
    unchanged_chunks: int
    model_name: str
    model_revision: str
    dimensions: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "scanned_chunks": self.scanned_chunks,
            "indexed_chunks": self.indexed_chunks,
            "unchanged_chunks": self.unchanged_chunks,
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "dimensions": self.dimensions,
        }


class PostgresKnowledgeEmbeddingIndexer:
    """Persist derived vectors only for approved Active-Index Chunks."""

    def __init__(
        self,
        database_url: str,
        embedding_backend: EmbeddingBackend,
        *,
        dimensions: int = PGVECTOR_DIMENSIONS,
    ) -> None:
        self.database_url = database_url
        self.embedding_backend = embedding_backend
        self.dimensions = dimensions

    def sync(self) -> KnowledgeEmbeddingIndexResult:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - required project dependency
            raise HybridRetrievalConfigurationError(
                "psycopg is required for pgvector indexing"
            ) from exc

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                self._check_ready(cursor)
                cursor.execute(
                    """
                    SELECT row_to_json(kc), row_to_json(kd),
                           COALESCE(NULLIF(kd.module, ''), rc.module, '') AS resolved_module,
                           COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, '')
                               AS resolved_equipment_type,
                           kc.embedding_model, kc.embedding_revision,
                           kc.embedding_input_sha256
                    FROM active_knowledge_chunk kc
                    JOIN knowledge_document kd ON kd.document_id = kc.document_id
                    LEFT JOIN rca_case rc ON rc.case_id = kd.case_id
                    ORDER BY kd.document_id, kc.chunk_index
                    """
                )
                rows = cursor.fetchall()
                pending: list[tuple[str, str]] = []
                for (
                    chunk_row,
                    document_row,
                    module,
                    equipment_type,
                    indexed_model,
                    indexed_revision,
                    indexed_fingerprint,
                ) in rows:
                    document = _row_document(dict(document_row), module, equipment_type)
                    chunk = _chunk_from_row(dict(chunk_row))
                    text = knowledge_chunk_text(document, chunk)
                    fingerprint = sha256(text.encode("utf-8")).hexdigest()
                    if (
                        indexed_model == self.embedding_backend.model_name
                        and indexed_revision == self.embedding_backend.model_revision
                        and indexed_fingerprint == fingerprint
                    ):
                        continue
                    pending.append((chunk.chunk_id, text))
                if pending:
                    vectors = self.embedding_backend.encode(
                        [text for _, text in pending],
                        kind="document",
                    )
                    updates: list[tuple[str, str, str, str, str]] = []
                    for (chunk_id, text), vector in zip(pending, vectors, strict=True):
                        self._validate_vector(vector)
                        updates.append(
                            (
                                _vector_literal(vector),
                                self.embedding_backend.model_name,
                                self.embedding_backend.model_revision,
                                sha256(text.encode("utf-8")).hexdigest(),
                                chunk_id,
                            )
                        )
                    cursor.executemany(
                        """
                        UPDATE knowledge_chunk
                        SET embedding = %s::vector,
                            embedding_model = %s,
                            embedding_revision = %s,
                            embedding_input_sha256 = %s,
                            embedded_at = now(),
                            embedding_status = 'completed'
                        WHERE chunk_id = %s AND validation_status = 'CONFIRMED'
                        """,
                        updates,
                    )
        return KnowledgeEmbeddingIndexResult(
            scanned_chunks=len(rows),
            indexed_chunks=len(pending),
            unchanged_chunks=len(rows) - len(pending),
            model_name=self.embedding_backend.model_name,
            model_revision=self.embedding_backend.model_revision,
            dimensions=self.dimensions,
        )

    def _check_ready(self, cursor: Any) -> None:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM schema_migrations WHERE version = %s
            ), to_regtype('vector') IS NOT NULL
            """,
            (PGVECTOR_MIGRATION,),
        )
        row = cursor.fetchone()
        if row is None or not bool(row[0]) or not bool(row[1]):
            raise HybridRetrievalConfigurationError(
                "PostgreSQL pgvector migration 009 is not applied"
            )

    def _validate_vector(self, vector: tuple[float, ...]) -> None:
        if len(vector) != self.dimensions:
            raise HybridRetrievalConfigurationError(
                f"Embedding dimension {len(vector)} does not match pgvector({self.dimensions})"
            )


class PostgresExactVectorCandidateSource:
    """Exact pgvector cosine search over approved, model-matched Chunks."""

    name = "postgres_pgvector_exact"

    def __init__(
        self,
        database_url: str,
        embedding_backend: EmbeddingBackend,
        *,
        dimensions: int = PGVECTOR_DIMENSIONS,
        minimum_similarity: float = 0.0,
    ) -> None:
        if not 0 <= minimum_similarity <= 1:
            raise ValueError("minimum vector similarity must be between 0 and 1")
        self.database_url = database_url
        self.embedding_backend = embedding_backend
        self.dimensions = dimensions
        self.minimum_similarity = minimum_similarity
        self._query_cache: dict[str, tuple[float, ...]] = {}

    def rank(
        self,
        plan: KnowledgeLookupPlan,
        *,
        limit: int,
    ) -> tuple[RankedKnowledgeChunk, ...]:
        if limit < 1:
            return ()
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - required project dependency
            raise HybridRetrievalConfigurationError(
                "psycopg is required for pgvector search"
            ) from exc
        vector = self._query_cache.get(plan.query)
        if vector is None:
            vector = self.embedding_backend.encode((plan.query,), kind="query")[0]
            self._query_cache[plan.query] = vector
        if len(vector) != self.dimensions:
            raise HybridRetrievalConfigurationError(
                f"Embedding dimension {len(vector)} does not match pgvector({self.dimensions})"
            )
        literal = _vector_literal(vector)
        sql = """
            SELECT row_to_json(kc), row_to_json(kd),
                   COALESCE(NULLIF(kd.module, ''), rc.module, '') AS resolved_module,
                   COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, '')
                       AS resolved_equipment_type,
                   1 - (kc.embedding <=> %s::vector) AS vector_score
            FROM active_knowledge_chunk kc
            JOIN knowledge_document kd ON kd.document_id = kc.document_id
            LEFT JOIN rca_case rc ON rc.case_id = kd.case_id
            WHERE kc.embedding IS NOT NULL
              AND kc.embedding_model = %s
              AND kc.embedding_revision = %s
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
            ORDER BY kc.embedding <=> %s::vector, kd.document_id, kc.chunk_id
            LIMIT %s
        """
        parameters = (
            literal,
            self.embedding_backend.model_name,
            self.embedding_backend.model_revision,
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
            literal,
            limit,
        )
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                rows = cursor.fetchall()
        query_terms = set(tokenize_knowledge_text(plan.query))
        results: list[RankedKnowledgeChunk] = []
        for chunk_row, document_row, module, equipment_type, raw_score in rows:
            document = _row_document(dict(document_row), module, equipment_type)
            chunk = _chunk_from_row(dict(chunk_row))
            similarity = max(0.0, min(1.0, float(raw_score)))
            if similarity < self.minimum_similarity:
                continue
            results.append(
                RankedKnowledgeChunk(
                    document=document,
                    chunk=chunk,
                    score=round(similarity, 6),
                    vector_score=round(similarity, 6),
                    matched_tokens=tuple(
                        sorted(
                            query_terms
                            & set(tokenize_knowledge_text(knowledge_chunk_text(document, chunk)))
                        )
                    ),
                )
            )
        return tuple(results)
