"""Fail-closed staging and Active Index stores for governed knowledge."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from yield_rca_core.knowledge_ingestion import (
    KnowledgeCandidateNotFoundError,
    KnowledgeChunker,
    KnowledgeIngestionConflictError,
)
from yield_rca_core.knowledge_models import (
    KnowledgeCandidateStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionApproval,
    KnowledgeIngestionCandidate,
    KnowledgeSourceFormat,
    KnowledgeValidationStatus,
)
from yield_rca_core.memory_models import ApprovalDecision


def _publication(
    candidate: KnowledgeIngestionCandidate,
    approval: KnowledgeIngestionApproval,
) -> tuple[KnowledgeIngestionCandidate, KnowledgeDocument | None, tuple[KnowledgeChunk, ...]]:
    if candidate.status != KnowledgeCandidateStatus.PENDING_APPROVAL.value:
        raise KnowledgeIngestionConflictError(
            "KNOWLEDGE_CANDIDATE_TERMINAL", "knowledge candidate is already terminal"
        )
    if approval.engineer_id.casefold() in {
        item.engineer_id.casefold() for item in candidate.approvals
    }:
        raise KnowledgeIngestionConflictError(
            "ENGINEER_ALREADY_DECIDED", "the same engineer cannot decide twice"
        )

    approvals = (*candidate.approvals, approval)
    status = KnowledgeCandidateStatus.PENDING_APPROVAL.value
    if approval.decision == ApprovalDecision.REJECT.value:
        status = KnowledgeCandidateStatus.REJECTED.value
    elif sum(item.decision == ApprovalDecision.APPROVE.value for item in approvals) >= 2:
        status = KnowledgeCandidateStatus.PUBLISHED.value

    document: KnowledgeDocument | None = None
    active_chunks: tuple[KnowledgeChunk, ...] = ()
    published_document_id: str | None = None
    if status == KnowledgeCandidateStatus.PUBLISHED.value:
        suffix = candidate.candidate_id.removeprefix("KING_")
        published_document_id = f"KDOC_{suffix}"
        document = KnowledgeDocument(
            document_id=published_document_id,
            case_id=candidate.case_id,
            document_type=candidate.document_type,
            title=candidate.title,
            content=candidate.parsed_content,
            module=candidate.module,
            equipment_type=candidate.equipment_type,
            operation=candidate.operation,
            defect_type=candidate.defect_type,
            tags=candidate.tags,
            source_format=candidate.source_format,
            content_sha256=candidate.content_sha256,
            validation_status=KnowledgeValidationStatus.CONFIRMED.value,
            publication_policy=candidate.publication_policy,
            source_candidate_id=candidate.candidate_id,
            created_at=approval.decided_at,
        )
        active_chunks = tuple(
            replace(
                item,
                chunk_id=f"KCHK_{suffix}_{item.chunk_index:04d}",
                candidate_id=None,
                document_id=published_document_id,
                validation_status=KnowledgeValidationStatus.CONFIRMED.value,
            )
            for item in candidate.chunks
        )

    updated = replace(
        candidate,
        status=status,
        approvals=approvals,
        published_document_id=published_document_id,
        updated_at=approval.decided_at,
    )
    return updated, document, active_chunks


class InMemoryKnowledgeStore:
    """Shared mutable catalog used by local CSV/demo mode."""

    def __init__(
        self,
        *,
        documents: tuple[KnowledgeDocument, ...] = (),
        chunks: tuple[KnowledgeChunk, ...] = (),
        case_ids: set[str] | None = None,
    ) -> None:
        self._candidates: dict[str, KnowledgeIngestionCandidate] = {}
        self._documents = {item.document_id: item for item in documents}
        self._chunks = {item.chunk_id: item for item in chunks}
        self._case_ids = set(case_ids or ()) | {
            item.case_id for item in documents if item.case_id is not None
        }
        self._lock = RLock()

    def check_ready(self) -> None:
        return None

    def case_exists(self, case_id: str) -> bool:
        with self._lock:
            return case_id in self._case_ids

    def create_candidate(
        self, candidate: KnowledgeIngestionCandidate
    ) -> KnowledgeIngestionCandidate:
        with self._lock:
            duplicate_candidate = next(
                (
                    item
                    for item in self._candidates.values()
                    if item.content_sha256 == candidate.content_sha256
                    and item.status == KnowledgeCandidateStatus.PENDING_APPROVAL.value
                ),
                None,
            )
            duplicate_document = next(
                (
                    item
                    for item in self._documents.values()
                    if item.content_sha256 == candidate.content_sha256
                ),
                None,
            )
            if duplicate_candidate or duplicate_document:
                duplicate_id = (
                    duplicate_candidate.candidate_id
                    if duplicate_candidate
                    else duplicate_document.document_id
                    if duplicate_document
                    else "unknown"
                )
                raise KnowledgeIngestionConflictError(
                    "DUPLICATE_KNOWLEDGE_DOCUMENT",
                    f"the same content already exists: {duplicate_id}",
                )
            self._candidates[candidate.candidate_id] = candidate
            return candidate

    def get_candidate(self, candidate_id: str) -> KnowledgeIngestionCandidate | None:
        with self._lock:
            return self._candidates.get(candidate_id)

    def list_candidates(self, status: str | None = None) -> list[KnowledgeIngestionCandidate]:
        with self._lock:
            candidates = list(self._candidates.values())
        if status is not None:
            candidates = [item for item in candidates if item.status == status]
        return sorted(candidates, key=lambda item: (item.created_at, item.candidate_id))

    def commit_approval(
        self, approval: KnowledgeIngestionApproval
    ) -> KnowledgeIngestionCandidate:
        with self._lock:
            current = self._candidates.get(approval.candidate_id)
            if current is None:
                raise KnowledgeCandidateNotFoundError(approval.candidate_id)
            updated, document, chunks = _publication(current, approval)
            if document is not None:
                if document.document_id in self._documents:
                    raise KnowledgeIngestionConflictError(
                        "DOCUMENT_ID_CONFLICT", document.document_id
                    )
                self._documents[document.document_id] = document
                self._chunks.update({item.chunk_id: item for item in chunks})
            self._candidates[approval.candidate_id] = updated
            return updated

    def active_documents(self) -> list[KnowledgeDocument]:
        with self._lock:
            return sorted(self._documents.values(), key=lambda item: item.document_id)

    def active_chunks(self) -> list[KnowledgeChunk]:
        with self._lock:
            return sorted(
                self._chunks.values(),
                key=lambda item: (item.document_id or "", item.chunk_index),
            )

    def register_confirmed_document(
        self,
        document: KnowledgeDocument,
        chunks: tuple[KnowledgeChunk, ...],
    ) -> None:
        """Register an already governed Memory publication in the same Active Index."""

        with self._lock:
            self._documents[document.document_id] = document
            self._chunks.update({item.chunk_id: item for item in chunks})
            if document.case_id:
                self._case_ids.add(document.case_id)


class PostgresKnowledgeStore:
    """Transactional staging, dual approval, and Active Index publication."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def check_ready(self) -> None:
        import psycopg

        required_relations = (
            "knowledge_ingestion_candidate",
            "knowledge_ingestion_chunk",
            "knowledge_ingestion_approval",
            "knowledge_chunk",
            "active_knowledge_chunk",
        )
        with psycopg.connect(self.database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT " + ", ".join("to_regclass(%s)" for _ in required_relations),
                    tuple(f"public.{name}" for name in required_relations),
                )
                row = cursor.fetchone()
                if row is None or any(value is None for value in row):
                    raise RuntimeError("knowledge ingestion migration 007 is not applied")
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM schema_migrations
                        WHERE version = '007_knowledge_ingestion'
                    )
                    """
                )
                migration = cursor.fetchone()
                if migration is None or not bool(migration[0]):
                    raise RuntimeError("knowledge ingestion migration 007 is not applied")

    def case_exists(self, case_id: str) -> bool:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM rca_case WHERE case_id = %s", (case_id,))
                return cursor.fetchone() is not None

    def create_candidate(
        self, candidate: KnowledgeIngestionCandidate
    ) -> KnowledgeIngestionCandidate:
        import psycopg

        try:
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT candidate_id FROM knowledge_ingestion_candidate
                        WHERE content_sha256 = %s AND status = 'pending_approval'
                        UNION ALL
                        SELECT document_id FROM knowledge_document
                        WHERE content_sha256 = %s AND validation_status = 'CONFIRMED'
                        LIMIT 1
                        """,
                        (candidate.content_sha256, candidate.content_sha256),
                    )
                    duplicate = cursor.fetchone()
                    if duplicate is not None:
                        raise KnowledgeIngestionConflictError(
                            "DUPLICATE_KNOWLEDGE_DOCUMENT",
                            f"the same content already exists: {duplicate[0]}",
                        )
                    cursor.execute(
                        """
                        INSERT INTO knowledge_ingestion_candidate (
                            candidate_id, filename, source_format, document_type, case_id,
                            title, parsed_content, content_sha256, module, equipment_type,
                            operation, defect_type, tags, status, publication_policy,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            candidate.candidate_id,
                            candidate.filename,
                            candidate.source_format,
                            candidate.document_type,
                            candidate.case_id,
                            candidate.title,
                            candidate.parsed_content,
                            candidate.content_sha256,
                            candidate.module,
                            candidate.equipment_type,
                            candidate.operation,
                            candidate.defect_type,
                            list(candidate.tags),
                            candidate.status,
                            candidate.publication_policy,
                            candidate.created_at,
                            candidate.updated_at,
                        ),
                    )
                    cursor.executemany(
                        """
                        INSERT INTO knowledge_ingestion_chunk (
                            chunk_id, candidate_id, chunk_index, section_type, heading,
                            content, token_count, metadata, validation_status,
                            embedding_status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        """,
                        [
                            (
                                item.chunk_id,
                                candidate.candidate_id,
                                item.chunk_index,
                                item.section_type,
                                item.heading,
                                item.content,
                                item.token_count,
                                json.dumps(item.metadata),
                                item.validation_status,
                                item.embedding_status,
                            )
                            for item in candidate.chunks
                        ],
                    )
        except KnowledgeIngestionConflictError:
            raise
        except psycopg.errors.UniqueViolation as exc:
            raise KnowledgeIngestionConflictError(
                "DUPLICATE_KNOWLEDGE_DOCUMENT", "duplicate knowledge candidate"
            ) from exc
        stored = self.get_candidate(candidate.candidate_id)
        if stored is None:
            raise RuntimeError("knowledge candidate insert did not persist")
        return stored

    def get_candidate(self, candidate_id: str) -> KnowledgeIngestionCandidate | None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM knowledge_ingestion_candidate WHERE candidate_id = %s",
                    (candidate_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                candidate_row = _row_dict(cursor, row)
                chunks = self._read_staged_chunks(cursor, candidate_id)
                approvals = self._read_approvals(cursor, candidate_id)
        return _candidate_from_rows(candidate_row, chunks, approvals)

    def list_candidates(self, status: str | None = None) -> list[KnowledgeIngestionCandidate]:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                if status is None:
                    cursor.execute(
                        """
                        SELECT candidate_id FROM knowledge_ingestion_candidate
                        ORDER BY created_at, candidate_id
                        """
                    )
                else:
                    cursor.execute(
                        """
                        SELECT candidate_id FROM knowledge_ingestion_candidate
                        WHERE status = %s ORDER BY created_at, candidate_id
                        """,
                        (status,),
                    )
                candidate_ids = [str(row[0]) for row in cursor.fetchall()]
        return [
            candidate
            for candidate_id in candidate_ids
            if (candidate := self.get_candidate(candidate_id)) is not None
        ]

    def commit_approval(
        self, approval: KnowledgeIngestionApproval
    ) -> KnowledgeIngestionCandidate:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM knowledge_ingestion_candidate
                    WHERE candidate_id = %s FOR UPDATE
                    """,
                    (approval.candidate_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KnowledgeCandidateNotFoundError(approval.candidate_id)
                current = _candidate_from_rows(
                    _row_dict(cursor, row),
                    self._read_staged_chunks(cursor, approval.candidate_id),
                    self._read_approvals(cursor, approval.candidate_id),
                )
                updated, document, chunks = _publication(current, approval)
                try:
                    cursor.execute(
                        """
                        INSERT INTO knowledge_ingestion_approval (
                            approval_id, candidate_id, engineer_id, engineer_role,
                            decision, comment, decided_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            approval.approval_id,
                            approval.candidate_id,
                            approval.engineer_id,
                            approval.engineer_role,
                            approval.decision,
                            approval.comment,
                            approval.decided_at,
                        ),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    raise KnowledgeIngestionConflictError(
                        "ENGINEER_ALREADY_DECIDED", "the same engineer cannot decide twice"
                    ) from exc
                if document is not None:
                    cursor.execute(
                        """
                        INSERT INTO knowledge_document (
                            document_id, case_id, document_type, title, content, tags,
                            validation_status, module, equipment_type, operation,
                            defect_type, source_format, content_sha256,
                            publication_policy, source_ingestion_candidate_id, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s
                        )
                        """,
                        (
                            document.document_id,
                            document.case_id,
                            document.document_type,
                            document.title,
                            document.content,
                            list(document.tags),
                            document.validation_status,
                            document.module,
                            document.equipment_type,
                            document.operation,
                            document.defect_type,
                            document.source_format,
                            document.content_sha256,
                            document.publication_policy,
                            document.source_candidate_id,
                            document.created_at,
                        ),
                    )
                    cursor.executemany(
                        """
                        INSERT INTO knowledge_chunk (
                            chunk_id, document_id, chunk_index, section_type, heading,
                            content, token_count, metadata, validation_status,
                            embedding_status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        """,
                        [
                            (
                                item.chunk_id,
                                item.document_id,
                                item.chunk_index,
                                item.section_type,
                                item.heading,
                                item.content,
                                item.token_count,
                                json.dumps(item.metadata),
                                item.validation_status,
                                item.embedding_status,
                            )
                            for item in chunks
                        ],
                    )
                cursor.execute(
                    """
                    UPDATE knowledge_ingestion_candidate
                    SET status = %s, published_document_id = %s,
                        published_at = %s, updated_at = %s
                    WHERE candidate_id = %s
                    """,
                    (
                        updated.status,
                        updated.published_document_id,
                        (
                            updated.updated_at
                            if updated.status == KnowledgeCandidateStatus.PUBLISHED.value
                            else None
                        ),
                        updated.updated_at,
                        updated.candidate_id,
                    ),
                )
        return updated

    def active_documents(self) -> list[KnowledgeDocument]:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT kd.*, COALESCE(NULLIF(kd.module, ''), rc.module, '') AS resolved_module,
                           COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, '')
                               AS resolved_equipment_type
                    FROM knowledge_document kd
                    LEFT JOIN rca_case rc ON rc.case_id = kd.case_id
                    WHERE kd.validation_status = 'CONFIRMED'
                    ORDER BY kd.document_id
                    """
                )
                return [_document_from_row(_row_dict(cursor, row)) for row in cursor.fetchall()]

    def active_chunks(self) -> list[KnowledgeChunk]:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM active_knowledge_chunk ORDER BY document_id, chunk_index"
                )
                return [_chunk_from_row(_row_dict(cursor, row)) for row in cursor.fetchall()]

    @staticmethod
    def _read_staged_chunks(cursor: Any, candidate_id: str) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT * FROM knowledge_ingestion_chunk
            WHERE candidate_id = %s ORDER BY chunk_index
            """,
            (candidate_id,),
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]

    @staticmethod
    def _read_approvals(cursor: Any, candidate_id: str) -> list[dict[str, Any]]:
        cursor.execute(
            """
            SELECT * FROM knowledge_ingestion_approval
            WHERE candidate_id = %s ORDER BY decided_at, approval_id
            """,
            (candidate_id,),
        )
        return [_row_dict(cursor, row) for row in cursor.fetchall()]


def load_builtin_knowledge_store(
    corpus_path: Path,
    *,
    additional_case_ids: set[str] | None = None,
) -> InMemoryKnowledgeStore:
    """Load only explicitly CONFIRMED Synthetic assets into the local Active Index."""

    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    documents: list[KnowledgeDocument] = []
    for item in payload.get("documents", []):
        if item.get("validation_status") != KnowledgeValidationStatus.CONFIRMED.value:
            continue
        content = str(item["content"])
        documents.append(
            KnowledgeDocument(
                document_id=str(item["document_id"]),
                case_id=str(item["case_id"]) if item.get("case_id") else None,
                asset_id=str(item["asset_id"]) if item.get("asset_id") else None,
                document_type=str(item["document_type"]),
                title=str(item["title"]),
                content=content,
                module=str(item.get("module", "")),
                equipment_type=str(item.get("equipment_type", "")),
                operation=str(item.get("operation", "")),
                defect_type=str(item.get("defect_type", "")),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                source_format=KnowledgeSourceFormat.SYNTHETIC.value,
                content_sha256=str(
                    item.get("content_hash") or sha256(content.encode("utf-8")).hexdigest()
                ),
                publication_policy="BUILTIN_SYNTHETIC_SEED",
                created_at=str(item.get("created_at", "2026-08-08T00:00:00+08:00")),
            )
        )
    chunker = KnowledgeChunker()
    chunks = tuple(chunk for document in documents for chunk in chunker.chunk_document(document))
    case_ids = set(additional_case_ids or ()) | {
        item.case_id for item in documents if item.case_id
    }
    return InMemoryKnowledgeStore(
        documents=tuple(documents),
        chunks=chunks,
        case_ids=case_ids,
    )


def _row_dict(cursor: Any, row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip((item.name for item in cursor.description or []), row, strict=True))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return dict(json.loads(value))
    return dict(value or {})


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(";") if item.strip())
    return tuple(str(item) for item in value)


def _chunk_from_row(row: dict[str, Any]) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=str(row["chunk_id"]),
        document_id=str(row["document_id"]) if row.get("document_id") else None,
        candidate_id=str(row["candidate_id"]) if row.get("candidate_id") else None,
        chunk_index=int(row["chunk_index"]),
        section_type=str(row["section_type"]),
        heading=str(row.get("heading", "")),
        content=str(row["content"]),
        token_count=int(row["token_count"]),
        metadata=_json_object(row.get("metadata")),
        validation_status=str(row["validation_status"]),
        embedding_status=str(row["embedding_status"]),
    )


def _approval_from_row(row: dict[str, Any]) -> KnowledgeIngestionApproval:
    return KnowledgeIngestionApproval(
        approval_id=str(row["approval_id"]),
        candidate_id=str(row["candidate_id"]),
        engineer_id=str(row["engineer_id"]),
        engineer_role=str(row["engineer_role"]),
        decision=str(row["decision"]),
        comment=str(row.get("comment", "")),
        decided_at=str(row["decided_at"]),
    )


def _candidate_from_rows(
    row: dict[str, Any],
    chunk_rows: list[dict[str, Any]],
    approval_rows: list[dict[str, Any]],
) -> KnowledgeIngestionCandidate:
    return KnowledgeIngestionCandidate(
        candidate_id=str(row["candidate_id"]),
        filename=str(row["filename"]),
        source_format=str(row["source_format"]),
        document_type=str(row["document_type"]),
        case_id=str(row["case_id"]) if row.get("case_id") else None,
        title=str(row["title"]),
        parsed_content=str(row["parsed_content"]),
        content_sha256=str(row["content_sha256"]),
        module=str(row["module"]),
        equipment_type=str(row.get("equipment_type", "")),
        operation=str(row.get("operation", "")),
        defect_type=str(row.get("defect_type", "")),
        tags=_string_tuple(row.get("tags")),
        status=str(row["status"]),
        chunks=tuple(_chunk_from_row(item) for item in chunk_rows),
        approvals=tuple(_approval_from_row(item) for item in approval_rows),
        published_document_id=(
            str(row["published_document_id"])
            if row.get("published_document_id")
            else None
        ),
        publication_policy=str(row["publication_policy"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _document_from_row(row: dict[str, Any]) -> KnowledgeDocument:
    content = str(row["content"])
    return KnowledgeDocument(
        document_id=str(row["document_id"]),
        case_id=str(row["case_id"]) if row.get("case_id") else None,
        asset_id=(
            str(row["asset_id"])
            if row.get("asset_id")
            else (
                str(row["evaluation_asset_id"])
                if row.get("evaluation_asset_id")
                else None
            )
        ),
        document_type=str(row["document_type"]),
        title=str(row["title"]),
        content=content,
        module=str(row.get("resolved_module", row.get("module", ""))),
        equipment_type=str(
            row.get("resolved_equipment_type", row.get("equipment_type", ""))
        ),
        operation=str(row.get("operation", "")),
        defect_type=str(row.get("defect_type", "")),
        tags=_string_tuple(row.get("tags")),
        source_format=str(row.get("source_format", KnowledgeSourceFormat.SYNTHETIC.value)),
        content_sha256=str(
            row.get("content_sha256") or sha256(content.encode("utf-8")).hexdigest()
        ),
        validation_status=str(row["validation_status"]),
        publication_policy=str(row.get("publication_policy", "LEGACY_CONFIRMED")),
        source_candidate_id=(
            str(row["source_ingestion_candidate_id"])
            if row.get("source_ingestion_candidate_id")
            else None
        ),
        created_at=str(row["created_at"]),
    )
