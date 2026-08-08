"""Controlled candidate persistence and dual-engineer memory publication."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from hashlib import sha256
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from yield_rca_core.knowledge_ingestion import KnowledgeChunker
from yield_rca_core.knowledge_models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSourceFormat,
)
from yield_rca_core.knowledge_store import InMemoryKnowledgeStore
from yield_rca_core.memory_models import (
    ApprovalDecision,
    EngineerRole,
    KnowledgeIndexStatus,
    MemoryApproval,
    MemoryCandidate,
    MemoryCandidateStatus,
)
from yield_rca_core.models import AgentKind, HypothesisStatus, RCAState

from yield_rca_api.audit import AuditEvent


class MemoryCandidateError(ValueError):
    """Base error for the controlled memory workflow."""


class MemoryCandidateNotFoundError(MemoryCandidateError):
    pass


class MemoryCandidateNotEligibleError(MemoryCandidateError):
    pass


class MemoryApprovalConflictError(MemoryCandidateError):
    pass


class MemoryApprovalValidationError(MemoryCandidateError):
    pass


class MemoryStore(Protocol):
    def create(self, candidate: MemoryCandidate) -> MemoryCandidate: ...

    def get(self, candidate_id: str) -> MemoryCandidate | None: ...

    def get_by_job(self, job_id: str) -> MemoryCandidate | None: ...

    def commit_decision(
        self,
        *,
        candidate_id: str,
        approval: MemoryApproval,
        correlation_id: str,
    ) -> MemoryCandidate: ...


def _improvement_finding(state: RCAState) -> Any:
    matches = [item for item in state.findings if item.agent == AgentKind.IMPROVEMENT.value]
    if len(matches) != 1:
        raise MemoryCandidateNotEligibleError(
            "completed RCAState must contain exactly one Improvement finding"
        )
    return matches[0]


def _evidence_snapshot(state: RCAState, evidence_ids: list[str]) -> list[dict[str, Any]]:
    """Persist cited Evidence and provenance, never the underlying Fab tables."""
    evidence_by_id = state.evidence_by_id
    snapshot: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise MemoryCandidateNotEligibleError(
                f"memory candidate references unavailable evidence {evidence_id}"
            )
        snapshot.append(
            {
                "evidence_id": evidence.evidence_id,
                "evidence_type": evidence.evidence_type,
                "observation": evidence.observation or evidence.summary,
                "summary": evidence.summary,
                "confidence": evidence.confidence,
                "entities": [item.to_dict() for item in evidence.entities],
                "source": {
                    "source_type": evidence.source_type,
                    "source_id": evidence.source_id,
                    "source_tool": evidence.source_tool,
                    "source_agent": evidence.source_agent,
                    "timestamp": evidence.timestamp,
                },
            }
        )
    return snapshot


def build_memory_candidate(state: RCAState) -> MemoryCandidate:
    """Create a non-published candidate from a supported RCA state."""

    if not state.hypotheses:
        raise MemoryCandidateNotEligibleError("RCAState has no hypothesis")
    hypothesis = state.hypotheses[-1]
    if hypothesis.status != HypothesisStatus.SUPPORTED.value:
        raise MemoryCandidateNotEligibleError(
            "only a supported RCA conclusion can become a confirmed memory"
        )
    finding = _improvement_finding(state)
    details = finding.details
    raw_recommendations = details.get("recommendations")
    if not isinstance(raw_recommendations, dict):
        raise MemoryCandidateNotEligibleError("Improvement finding has no recommendations")
    recommendations = {
        str(category): [dict(item) for item in items]
        for category, items in raw_recommendations.items()
        if isinstance(items, list)
    }
    scope = details.get("scope_assessment", {})
    scope_level = str(scope.get("level", "event")) if isinstance(scope, dict) else "event"
    incident_summary = str(details.get("incident_summary", finding.summary)).strip()
    engineering_summary = str(details.get("engineering_summary", finding.summary)).strip()
    subject = state.job.source_lot_id or state.job.product_id or state.job.job_id
    evidence_ids = list(dict.fromkeys(hypothesis.evidence_ids))
    rca_finding = next(
        (item for item in state.findings if item.agent == AgentKind.RCA_REASONING.value),
        None,
    )
    reasoning_engine = (
        str(rca_finding.details.get("reasoning_engine", "legacy"))
        if rca_finding is not None
        else "legacy"
    )
    return MemoryCandidate(
        candidate_id=f"MEM_{uuid4().hex.upper()}",
        job_id=state.job.job_id,
        status=MemoryCandidateStatus.PENDING_APPROVAL.value,
        scope_level=scope_level,
        source_lot_id=state.job.source_lot_id,
        product_id=state.job.product_id,
        title=f"Confirmed Yield RCA candidate for {subject}",
        incident_summary=incident_summary,
        engineering_summary=engineering_summary,
        root_cause=hypothesis.root_cause,
        confidence=hypothesis.confidence,
        recommendations=recommendations,
        evidence_ids=evidence_ids,
        requires_process_engineer_approval=bool(recommendations.get("recipe_optimization")),
        evidence_snapshot=_evidence_snapshot(state, evidence_ids),
        knowledge_provenance={
            "job_id": state.job.job_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "hypothesis_status": hypothesis.status,
            "supporting_evidence_ids": list(hypothesis.supporting_evidence_ids),
            "contradicting_evidence_ids": list(hypothesis.contradicting_evidence_ids),
            "neutral_evidence_ids": list(hypothesis.neutral_evidence_ids),
            "validation_results": [dict(item) for item in hypothesis.validation_results],
        },
        reasoning_engine=reasoning_engine,
    )


def _recommendation_actions(candidate: MemoryCandidate) -> list[str]:
    actions: list[str] = []
    for recommendations in candidate.recommendations.values():
        for recommendation in recommendations:
            action = str(recommendation.get("action", "")).strip()
            if action and action not in actions:
                actions.append(action)
    return actions


def _module_and_equipment(root_cause: str) -> tuple[str, str | None]:
    token = root_cause.split(maxsplit=1)[0]
    normalized = re.sub(r"[^A-Z0-9_]+", "", token.upper())
    module = normalized.split("_", maxsplit=1)[0] or "UNKNOWN"
    return module, normalized or None


def _publication_payloads(
    candidate: MemoryCandidate,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = f"RCA_MEMORY_{candidate.candidate_id.removeprefix('MEM_')}"
    document_id = f"DOC_MEMORY_{candidate.candidate_id.removeprefix('MEM_')}"
    module, equipment_type = _module_and_equipment(candidate.root_cause)
    actions = _recommendation_actions(candidate)
    approvals = [item.to_dict() for item in candidate.approvals]
    case = {
        "case_id": case_id,
        "title": candidate.title,
        "technology": None,
        "module": module,
        "equipment_type": equipment_type,
        "symptom": candidate.incident_summary,
        "root_cause": candidate.root_cause,
        "solution": "; ".join(actions) or "Continue engineering review",
        "confidence": candidate.confidence,
        "validation_status": "CONFIRMED",
        "source_candidate_id": candidate.candidate_id,
        "approval_count": candidate.approval_count,
    }
    document = {
        "document_id": document_id,
        "case_id": case_id,
        "document_type": "RCA_CASE",
        "title": candidate.title,
        "content": json.dumps(
            {
                "engineering_summary": candidate.engineering_summary,
                "recommendations": candidate.recommendations,
                "evidence_ids": candidate.evidence_ids,
                "evidence_snapshot": candidate.evidence_snapshot,
                "knowledge_provenance": candidate.knowledge_provenance,
                "reasoning_engine": candidate.reasoning_engine,
                "approvals": approvals,
            },
            ensure_ascii=False,
        ),
        "tags": ["confirmed", candidate.scope_level, module.casefold()],
        "validation_status": "CONFIRMED",
    }
    return case, document


def _memory_knowledge_asset(
    case: dict[str, Any], document: dict[str, Any]
) -> tuple[KnowledgeDocument, tuple[KnowledgeChunk, ...]]:
    content = str(document["content"])
    asset = KnowledgeDocument(
        document_id=str(document["document_id"]),
        case_id=str(document["case_id"]),
        document_type=str(document["document_type"]),
        title=str(document["title"]),
        content=content,
        module=str(case["module"]),
        equipment_type=str(case["equipment_type"] or ""),
        tags=tuple(str(item) for item in document["tags"]),
        source_format=KnowledgeSourceFormat.TEXT.value,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        publication_policy="DUAL_ENGINEER_APPROVAL",
    )
    return asset, KnowledgeChunker().chunk_document(asset)


def _apply_decision(
    candidate: MemoryCandidate,
    approval: MemoryApproval,
    *,
    correlation_id: str,
) -> tuple[
    MemoryCandidate,
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[AuditEvent],
]:
    if candidate.status != MemoryCandidateStatus.PENDING_APPROVAL.value:
        raise MemoryApprovalConflictError("memory candidate is already terminal")
    if approval.engineer_id in {item.engineer_id for item in candidate.approvals}:
        raise MemoryApprovalConflictError("the same engineer cannot approve or reject twice")

    approvals = [*candidate.approvals, approval]
    next_status = MemoryCandidateStatus.PENDING_APPROVAL.value
    if approval.decision == ApprovalDecision.REJECT.value:
        next_status = MemoryCandidateStatus.REJECTED.value
    else:
        approved = [item for item in approvals if item.decision == ApprovalDecision.APPROVE.value]
        if len(approved) >= 2:
            has_process_approval = any(
                item.engineer_role == EngineerRole.PROCESS_ENGINEER.value for item in approved
            )
            if candidate.requires_process_engineer_approval and not has_process_approval:
                raise MemoryApprovalValidationError(
                    "the second approval must be from a Process Engineer because "
                    "the candidate contains Recipe recommendations"
                )
            next_status = MemoryCandidateStatus.PUBLISHED.value

    updated = replace(
        candidate,
        status=next_status,
        approvals=approvals,
        updated_at=approval.decided_at,
        published_case_id=(
            f"RCA_MEMORY_{candidate.candidate_id.removeprefix('MEM_')}"
            if next_status == MemoryCandidateStatus.PUBLISHED.value
            else None
        ),
        index_status=(
            KnowledgeIndexStatus.PENDING.value
            if next_status == MemoryCandidateStatus.PUBLISHED.value
            else KnowledgeIndexStatus.NOT_REQUESTED.value
        ),
    )
    published_case: dict[str, Any] | None = None
    published_document: dict[str, Any] | None = None
    if updated.status == MemoryCandidateStatus.PUBLISHED.value:
        published_case, published_document = _publication_payloads(updated)

    audit_events = [
        AuditEvent(
            action="MEMORY_APPROVAL_RECORDED",
            job_id=updated.job_id,
            correlation_id=correlation_id,
            actor=approval.engineer_id,
            outcome="success",
            details={
                "candidate_id": updated.candidate_id,
                "engineer_role": approval.engineer_role,
                "decision": approval.decision,
                "status": updated.status,
            },
        )
    ]
    if updated.status in {
        MemoryCandidateStatus.PUBLISHED.value,
        MemoryCandidateStatus.REJECTED.value,
    }:
        audit_events.append(
            AuditEvent(
                action=(
                    "MEMORY_CANDIDATE_PUBLISHED"
                    if updated.status == MemoryCandidateStatus.PUBLISHED.value
                    else "MEMORY_CANDIDATE_REJECTED"
                ),
                job_id=updated.job_id,
                correlation_id=correlation_id,
                actor=approval.engineer_id,
                outcome="success",
                details={
                    "candidate_id": updated.candidate_id,
                    "published_case_id": updated.published_case_id,
                },
            )
        )
    return updated, published_case, published_document, audit_events


class InMemoryMemoryStore:
    """Process-local Step 19 store used by CSV mode and API tests."""

    def __init__(self, knowledge_store: InMemoryKnowledgeStore | None = None) -> None:
        self._candidates: dict[str, MemoryCandidate] = {}
        self._job_candidates: dict[str, str] = {}
        self.published_cases: dict[str, dict[str, Any]] = {}
        self.published_documents: dict[str, dict[str, Any]] = {}
        self.audit_events: list[AuditEvent] = []
        self.knowledge_store = knowledge_store
        self._lock = RLock()

    def create(self, candidate: MemoryCandidate) -> MemoryCandidate:
        with self._lock:
            existing_id = self._job_candidates.get(candidate.job_id)
            if existing_id:
                return self._candidates[existing_id]
            self._candidates[candidate.candidate_id] = candidate
            self._job_candidates[candidate.job_id] = candidate.candidate_id
            return candidate

    def get(self, candidate_id: str) -> MemoryCandidate | None:
        with self._lock:
            return self._candidates.get(candidate_id)

    def get_by_job(self, job_id: str) -> MemoryCandidate | None:
        with self._lock:
            candidate_id = self._job_candidates.get(job_id)
            return self._candidates.get(candidate_id) if candidate_id else None

    def commit_decision(
        self,
        *,
        candidate_id: str,
        approval: MemoryApproval,
        correlation_id: str,
    ) -> MemoryCandidate:
        with self._lock:
            current = self._candidates.get(candidate_id)
            if current is None:
                raise MemoryCandidateNotFoundError(candidate_id)
            (
                updated,
                published_case,
                published_document,
                audit_events,
            ) = _apply_decision(
                current,
                approval,
                correlation_id=correlation_id,
            )
            if updated.status == MemoryCandidateStatus.PUBLISHED.value:
                updated = replace(
                    updated,
                    index_status=KnowledgeIndexStatus.COMPLETED.value,
                    index_attempts=updated.index_attempts + 1,
                )
            self._candidates[candidate_id] = updated
            if published_case and published_document:
                self.published_cases[published_case["case_id"]] = published_case
                self.published_documents[published_document["document_id"]] = published_document
                if self.knowledge_store is not None:
                    asset, chunks = _memory_knowledge_asset(published_case, published_document)
                    self.knowledge_store.register_confirmed_document(asset, chunks)
            self.audit_events.extend(audit_events)
            return updated


class PostgresMemoryStore:
    """PostgreSQL-backed candidate, approval, and publication store."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def create(self, candidate: MemoryCandidate) -> MemoryCandidate:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO memory_candidate (
                        candidate_id, job_id, status, scope_level, source_lot_id,
                        product_id, title, incident_summary, engineering_summary,
                        root_cause, confidence, recommendations, evidence_ids,
                        requires_process_engineer_approval, evidence_snapshot,
                        knowledge_provenance, reasoning_engine, index_status,
                        index_attempts, index_error, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    (
                        candidate.candidate_id,
                        candidate.job_id,
                        candidate.status,
                        candidate.scope_level,
                        candidate.source_lot_id,
                        candidate.product_id,
                        candidate.title,
                        candidate.incident_summary,
                        candidate.engineering_summary,
                        candidate.root_cause,
                        candidate.confidence,
                        json.dumps(candidate.recommendations),
                        candidate.evidence_ids,
                        candidate.requires_process_engineer_approval,
                        json.dumps(candidate.evidence_snapshot),
                        json.dumps(candidate.knowledge_provenance),
                        candidate.reasoning_engine,
                        candidate.index_status,
                        candidate.index_attempts,
                        candidate.index_error,
                        candidate.created_at,
                        candidate.updated_at,
                    ),
                )
        stored = self.get_by_job(candidate.job_id)
        if stored is None:
            raise RuntimeError("memory candidate insert did not persist")
        return stored

    def get(self, candidate_id: str) -> MemoryCandidate | None:
        return self._read_candidate("candidate_id", candidate_id)

    def get_by_job(self, job_id: str) -> MemoryCandidate | None:
        return self._read_candidate("job_id", job_id)

    def _read_candidate(self, column: str, value: str) -> MemoryCandidate | None:
        import psycopg

        if column not in {"candidate_id", "job_id"}:
            raise ValueError("invalid candidate lookup column")
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT * FROM memory_candidate WHERE {column} = %s", (value,))
                row = cursor.fetchone()
                if row is None:
                    return None
                columns = [item.name for item in cursor.description or []]
                candidate_row = dict(zip(columns, row, strict=True))
                cursor.execute(
                    """
                    SELECT approval_id, candidate_id, engineer_id, engineer_role,
                           decision, comment, decided_at
                    FROM memory_approval
                    WHERE candidate_id = %s
                    ORDER BY decided_at, approval_id
                    """,
                    (candidate_row["candidate_id"],),
                )
                approval_rows = cursor.fetchall()
        return _candidate_from_database(candidate_row, approval_rows)

    def commit_decision(
        self,
        *,
        candidate_id: str,
        approval: MemoryApproval,
        correlation_id: str,
    ) -> MemoryCandidate:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM memory_candidate WHERE candidate_id = %s FOR UPDATE",
                    (candidate_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise MemoryCandidateNotFoundError(candidate_id)
                columns = [item.name for item in cursor.description or []]
                candidate_row = dict(zip(columns, row, strict=True))
                cursor.execute(
                    """
                    SELECT approval_id, candidate_id, engineer_id, engineer_role,
                           decision, comment, decided_at
                    FROM memory_approval
                    WHERE candidate_id = %s
                    ORDER BY decided_at, approval_id
                    """,
                    (candidate_id,),
                )
                current = _candidate_from_database(candidate_row, cursor.fetchall())
                (
                    updated,
                    published_case,
                    published_document,
                    audit_events,
                ) = _apply_decision(
                    current,
                    approval,
                    correlation_id=correlation_id,
                )
                if updated.status == MemoryCandidateStatus.PUBLISHED.value:
                    updated = replace(
                        updated,
                        index_status=KnowledgeIndexStatus.COMPLETED.value,
                        index_attempts=updated.index_attempts + 1,
                    )
                try:
                    cursor.execute(
                        """
                        INSERT INTO memory_approval (
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
                    raise MemoryApprovalConflictError(
                        "engineer has already decided this candidate"
                    ) from exc
                if published_case and published_document:
                    cursor.execute(
                        """
                        INSERT INTO rca_case (
                            case_id, title, technology, module, equipment_type, symptom,
                            root_cause, solution, confidence, validation_status,
                            source_candidate_id, approval_count, approved_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
                        )
                        """,
                        tuple(published_case.values()),
                    )
                    cursor.execute(
                        """
                        INSERT INTO knowledge_document (
                            document_id, case_id, document_type, title, content, tags,
                            validation_status, module, equipment_type, source_format,
                            content_sha256, publication_policy
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            *tuple(published_document.values()),
                            published_case["module"],
                            published_case["equipment_type"],
                            "text",
                            sha256(str(published_document["content"]).encode("utf-8")).hexdigest(),
                            "DUAL_ENGINEER_APPROVAL",
                        ),
                    )
                    memory_asset, memory_chunks = _memory_knowledge_asset(
                        published_case, published_document
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
                                memory_asset.document_id,
                                item.chunk_index,
                                item.section_type,
                                item.heading,
                                item.content,
                                item.token_count,
                                json.dumps(item.metadata),
                                item.validation_status,
                                item.embedding_status,
                            )
                            for item in memory_chunks
                        ],
                    )
                    cursor.execute(
                        """
                        INSERT INTO knowledge_index_update (
                            update_id, candidate_id, case_id, status, attempts,
                            last_error, completed_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, now())
                        """,
                        (
                            f"INDEX_{updated.candidate_id.removeprefix('MEM_')}",
                            updated.candidate_id,
                            updated.published_case_id,
                            updated.index_status,
                            updated.index_attempts,
                            None,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE memory_candidate
                    SET status = %s, published_case_id = %s, published_at = %s,
                        index_status = %s, index_attempts = %s, index_error = %s,
                        updated_at = %s
                    WHERE candidate_id = %s
                    """,
                    (
                        updated.status,
                        updated.published_case_id,
                        updated.updated_at if updated.published_case_id else None,
                        updated.index_status,
                        updated.index_attempts,
                        updated.index_error,
                        updated.updated_at,
                        updated.candidate_id,
                    ),
                )
                for event in audit_events:
                    cursor.execute(
                        """
                        INSERT INTO audit_event (
                            event_id, occurred_at, action, job_id, correlation_id,
                            actor, outcome, details
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            event.event_id,
                            event.occurred_at,
                            event.action,
                            event.job_id,
                            event.correlation_id,
                            event.actor,
                            event.outcome,
                            json.dumps(event.details),
                        ),
                    )
        return updated


def _candidate_from_database(
    row: dict[str, Any], approval_rows: list[tuple[Any, ...]]
) -> MemoryCandidate:
    recommendations = row["recommendations"]
    if isinstance(recommendations, str):
        recommendations = json.loads(recommendations)
    approvals = [
        MemoryApproval(
            approval_id=str(item[0]),
            candidate_id=str(item[1]),
            engineer_id=str(item[2]),
            engineer_role=str(item[3]),
            decision=str(item[4]),
            comment=str(item[5] or ""),
            decided_at=item[6].isoformat() if hasattr(item[6], "isoformat") else str(item[6]),
        )
        for item in approval_rows
    ]
    return MemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        job_id=str(row["job_id"]),
        status=str(row["status"]),
        scope_level=str(row["scope_level"]),
        source_lot_id=str(row["source_lot_id"]) if row["source_lot_id"] else None,
        product_id=str(row["product_id"]) if row["product_id"] else None,
        title=str(row["title"]),
        incident_summary=str(row["incident_summary"]),
        engineering_summary=str(row["engineering_summary"]),
        root_cause=str(row["root_cause"]),
        confidence=float(row["confidence"]),
        recommendations={str(key): list(value) for key, value in recommendations.items()},
        evidence_ids=list(row["evidence_ids"]),
        requires_process_engineer_approval=bool(row["requires_process_engineer_approval"]),
        evidence_snapshot=(
            json.loads(row["evidence_snapshot"])
            if isinstance(row.get("evidence_snapshot"), str)
            else list(row.get("evidence_snapshot") or [])
        ),
        knowledge_provenance=(
            json.loads(row["knowledge_provenance"])
            if isinstance(row.get("knowledge_provenance"), str)
            else dict(row.get("knowledge_provenance") or {})
        ),
        reasoning_engine=str(row.get("reasoning_engine") or "legacy"),
        index_status=str(row.get("index_status") or KnowledgeIndexStatus.NOT_REQUESTED.value),
        index_attempts=int(row.get("index_attempts") or 0),
        index_error=str(row["index_error"]) if row.get("index_error") else None,
        approvals=approvals,
        published_case_id=(str(row["published_case_id"]) if row["published_case_id"] else None),
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


class MemoryApprovalService:
    """Enforce the dual-control publication policy above the persistence adapter."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._lock = RLock()

    def create_from_state(self, state: RCAState) -> MemoryCandidate:
        return self.store.create(build_memory_candidate(state))

    def get(self, candidate_id: str) -> MemoryCandidate:
        candidate = self.store.get(candidate_id)
        if candidate is None:
            raise MemoryCandidateNotFoundError(candidate_id)
        return candidate

    def get_by_job(self, job_id: str) -> MemoryCandidate:
        candidate = self.store.get_by_job(job_id)
        if candidate is None:
            raise MemoryCandidateNotFoundError(job_id)
        return candidate

    def decide(
        self,
        *,
        candidate_id: str,
        engineer_id: str,
        engineer_role: str,
        decision: str,
        comment: str = "",
        correlation_id: str = "CORR_MEMORY_SERVICE",
    ) -> MemoryCandidate:
        with self._lock:
            normalized_engineer_id = engineer_id.strip().upper()
            approval = MemoryApproval(
                approval_id=f"APPROVAL_{uuid4().hex.upper()}",
                candidate_id=candidate_id,
                engineer_id=normalized_engineer_id,
                engineer_role=engineer_role,
                decision=decision,
                comment=comment.strip(),
            )
            return self.store.commit_decision(
                candidate_id=candidate_id,
                approval=approval,
                correlation_id=correlation_id,
            )
