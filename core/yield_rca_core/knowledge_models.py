"""Typed contracts for governed knowledge ingestion and independent lookup."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.causal_scope import CausalLane, CausalSearchScope, ObservationScope
from yield_rca_core.memory_models import ApprovalDecision, EngineerRole
from yield_rca_core.models import SCHEMA_VERSION, ModelValidationError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{name} must be a non-empty string")
    return value.strip()


class KnowledgeDocumentType(StrEnum):
    RCA_CASE = "RCA_CASE"
    SOP = "SOP"
    ENGINEERING_NOTE = "ENGINEERING_NOTE"


class KnowledgeSourceFormat(StrEnum):
    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    SYNTHETIC = "synthetic"


class KnowledgeCandidateStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"
    REJECTED = "rejected"


class KnowledgeValidationStatus(StrEnum):
    STAGED = "STAGED"
    CONFIRMED = "CONFIRMED"


class KnowledgeEmbeddingStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class KnowledgeLookupIntent(StrEnum):
    KNOWLEDGE_LOOKUP = "knowledge_lookup"


class KnowledgeQuestionKind(StrEnum):
    HISTORICAL_MATCH = "historical_match"
    PROCEDURE_GUIDANCE = "procedure_guidance"
    ENGINEERING_NOTE_LOOKUP = "engineering_note_lookup"

    @property
    def document_type(self) -> str:
        return {
            KnowledgeQuestionKind.HISTORICAL_MATCH: KnowledgeDocumentType.RCA_CASE.value,
            KnowledgeQuestionKind.PROCEDURE_GUIDANCE: KnowledgeDocumentType.SOP.value,
            KnowledgeQuestionKind.ENGINEERING_NOTE_LOOKUP: (
                KnowledgeDocumentType.ENGINEERING_NOTE.value
            ),
        }[self]

    @property
    def action(self) -> str:
        return {
            KnowledgeQuestionKind.HISTORICAL_MATCH: "retrieve_historical_case",
            KnowledgeQuestionKind.PROCEDURE_GUIDANCE: "retrieve_procedure_guidance",
            KnowledgeQuestionKind.ENGINEERING_NOTE_LOOKUP: (
                "retrieve_engineering_note"
            ),
        }[self]


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    document_type: str
    title: str
    content: str
    module: str
    equipment_type: str = ""
    operation: str = ""
    defect_type: str = ""
    tags: tuple[str, ...] = ()
    case_id: str | None = None
    asset_id: str | None = None
    source_format: str = KnowledgeSourceFormat.TEXT.value
    content_sha256: str = ""
    validation_status: str = KnowledgeValidationStatus.CONFIRMED.value
    publication_policy: str = "DUAL_ENGINEER_APPROVAL"
    source_candidate_id: str | None = None
    created_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("document_id", "document_type", "title", "content", "module"):
            _required(str(getattr(self, name)), name)
        try:
            KnowledgeDocumentType(self.document_type)
            KnowledgeSourceFormat(self.source_format)
            KnowledgeValidationStatus(self.validation_status)
        except ValueError as exc:
            raise ModelValidationError("invalid KnowledgeDocument enum value") from exc
        if self.validation_status != KnowledgeValidationStatus.CONFIRMED.value:
            raise ModelValidationError("active KnowledgeDocument must be CONFIRMED")
        if self.case_id is not None:
            _required(self.case_id, "case_id")
        if self.asset_id is not None:
            _required(self.asset_id, "asset_id")
        if self.source_candidate_id is not None:
            _required(self.source_candidate_id, "source_candidate_id")
        if not isinstance(self.tags, tuple) or any(not item.strip() for item in self.tags):
            raise ModelValidationError("tags must contain non-empty strings")
        _required(self.content_sha256, "content_sha256")
        _required(self.publication_policy, "publication_policy")
        _required(self.created_at, "created_at")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported KnowledgeDocument schema_version")

    @property
    def evaluation_asset_id(self) -> str:
        return self.asset_id or self.case_id or self.document_id

    @property
    def source_confidence(self) -> float:
        """Audited source-governance weight, separate from retrieval relevance."""

        return {
            "DUAL_ENGINEER_APPROVAL": 1.0,
            "BUILTIN_SYNTHETIC_SEED": 0.8,
            "LEGACY_CONFIRMED": 0.7,
        }.get(self.publication_policy, 0.5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "case_id": self.case_id,
            "asset_id": self.asset_id,
            "evaluation_asset_id": self.evaluation_asset_id,
            "document_type": self.document_type,
            "title": self.title,
            "content": self.content,
            "module": self.module,
            "equipment_type": self.equipment_type,
            "operation": self.operation,
            "defect_type": self.defect_type,
            "tags": list(self.tags),
            "source_format": self.source_format,
            "content_sha256": self.content_sha256,
            "validation_status": self.validation_status,
            "publication_policy": self.publication_policy,
            "source_candidate_id": self.source_candidate_id,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            document_id=str(data["document_id"]),
            case_id=(str(data["case_id"]) if data.get("case_id") else None),
            asset_id=(
                str(data["asset_id"])
                if data.get("asset_id")
                else (
                    str(data["evaluation_asset_id"])
                    if data.get("evaluation_asset_id")
                    else None
                )
            ),
            document_type=str(data["document_type"]),
            title=str(data["title"]),
            content=str(data["content"]),
            module=str(data["module"]),
            equipment_type=str(data.get("equipment_type", "")),
            operation=str(data.get("operation", "")),
            defect_type=str(data.get("defect_type", "")),
            tags=tuple(str(item) for item in data.get("tags", [])),
            source_format=str(data.get("source_format", KnowledgeSourceFormat.TEXT.value)),
            content_sha256=str(data.get("content_sha256", "")),
            validation_status=str(
                data.get("validation_status", KnowledgeValidationStatus.CONFIRMED.value)
            ),
            publication_policy=str(
                data.get("publication_policy", "DUAL_ENGINEER_APPROVAL")
            ),
            source_candidate_id=(
                str(data["source_candidate_id"])
                if data.get("source_candidate_id")
                else None
            ),
            created_at=str(data.get("created_at", _now_iso())),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    chunk_index: int
    section_type: str
    heading: str
    content: str
    token_count: int
    metadata: dict[str, Any]
    validation_status: str
    document_id: str | None = None
    candidate_id: str | None = None
    embedding_status: str = KnowledgeEmbeddingStatus.NOT_REQUESTED.value
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("chunk_id", "section_type", "content"):
            _required(str(getattr(self, name)), name)
        if (self.document_id is None) == (self.candidate_id is None):
            raise ModelValidationError(
                "KnowledgeChunk requires exactly one of document_id or candidate_id"
            )
        if self.document_id is not None:
            _required(self.document_id, "document_id")
        if self.candidate_id is not None:
            _required(self.candidate_id, "candidate_id")
        if not isinstance(self.chunk_index, int) or self.chunk_index < 0:
            raise ModelValidationError("chunk_index must be non-negative")
        if not isinstance(self.token_count, int) or self.token_count <= 0:
            raise ModelValidationError("token_count must be positive")
        if not isinstance(self.metadata, dict):
            raise ModelValidationError("chunk metadata must be an object")
        try:
            KnowledgeValidationStatus(self.validation_status)
            KnowledgeEmbeddingStatus(self.embedding_status)
        except ValueError as exc:
            raise ModelValidationError("invalid KnowledgeChunk status") from exc
        if self.document_id and self.validation_status != KnowledgeValidationStatus.CONFIRMED:
            raise ModelValidationError("active document chunks must be CONFIRMED")
        if self.candidate_id and self.validation_status != KnowledgeValidationStatus.STAGED:
            raise ModelValidationError("candidate chunks must be STAGED")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported KnowledgeChunk schema_version")

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "candidate_id": self.candidate_id,
            "chunk_index": self.chunk_index,
            "section_type": self.section_type,
            "heading": self.heading,
            "token_count": self.token_count,
            "metadata": dict(self.metadata),
            "validation_status": self.validation_status,
            "embedding_status": self.embedding_status,
            "schema_version": self.schema_version,
        }
        if include_content:
            payload["content"] = self.content
        else:
            payload["content_preview"] = self.content[:240]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            chunk_id=str(data["chunk_id"]),
            document_id=(str(data["document_id"]) if data.get("document_id") else None),
            candidate_id=(str(data["candidate_id"]) if data.get("candidate_id") else None),
            chunk_index=int(data["chunk_index"]),
            section_type=str(data["section_type"]),
            heading=str(data.get("heading", "")),
            content=str(data["content"]),
            token_count=int(data["token_count"]),
            metadata=dict(data.get("metadata", {})),
            validation_status=str(data["validation_status"]),
            embedding_status=str(
                data.get("embedding_status", KnowledgeEmbeddingStatus.NOT_REQUESTED.value)
            ),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class KnowledgeIngestionApproval:
    approval_id: str
    candidate_id: str
    engineer_id: str
    engineer_role: str
    decision: str
    comment: str = ""
    decided_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("approval_id", "candidate_id", "engineer_id", "decided_at"):
            _required(str(getattr(self, name)), name)
        try:
            EngineerRole(self.engineer_role)
            ApprovalDecision(self.decision)
        except ValueError as exc:
            raise ModelValidationError("invalid knowledge approval enum value") from exc
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported knowledge approval schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "candidate_id": self.candidate_id,
            "engineer_id": self.engineer_id,
            "engineer_role": self.engineer_role,
            "decision": self.decision,
            "comment": self.comment,
            "decided_at": self.decided_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            approval_id=str(data["approval_id"]),
            candidate_id=str(data["candidate_id"]),
            engineer_id=str(data["engineer_id"]),
            engineer_role=str(data["engineer_role"]),
            decision=str(data["decision"]),
            comment=str(data.get("comment", "")),
            decided_at=str(data.get("decided_at", _now_iso())),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class KnowledgeIngestionCandidate:
    candidate_id: str
    filename: str
    source_format: str
    document_type: str
    title: str
    parsed_content: str
    content_sha256: str
    module: str
    chunks: tuple[KnowledgeChunk, ...]
    case_id: str | None = None
    equipment_type: str = ""
    operation: str = ""
    defect_type: str = ""
    tags: tuple[str, ...] = ()
    status: str = KnowledgeCandidateStatus.PENDING_APPROVAL.value
    approvals: tuple[KnowledgeIngestionApproval, ...] = ()
    published_document_id: str | None = None
    publication_policy: str = "DUAL_ENGINEER_APPROVAL"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "filename",
            "source_format",
            "document_type",
            "title",
            "parsed_content",
            "content_sha256",
            "module",
            "publication_policy",
            "created_at",
            "updated_at",
        ):
            _required(str(getattr(self, name)), name)
        try:
            KnowledgeSourceFormat(self.source_format)
            KnowledgeDocumentType(self.document_type)
            KnowledgeCandidateStatus(self.status)
        except ValueError as exc:
            raise ModelValidationError("invalid ingestion candidate enum value") from exc
        if not self.chunks:
            raise ModelValidationError("knowledge candidate requires at least one chunk")
        if any(item.candidate_id != self.candidate_id for item in self.chunks):
            raise ModelValidationError("candidate chunk parent mismatch")
        if self.document_type == KnowledgeDocumentType.RCA_CASE.value:
            if self.case_id is None:
                raise ModelValidationError("ingested RCA_CASE requires case_id")
            _required(self.case_id, "case_id")
        elif self.case_id is not None:
            _required(self.case_id, "case_id")
        chunk_indexes = [item.chunk_index for item in self.chunks]
        if chunk_indexes != list(range(len(self.chunks))):
            raise ModelValidationError("candidate chunk indexes must be contiguous")
        engineer_ids = [item.engineer_id.casefold() for item in self.approvals]
        if len(engineer_ids) != len(set(engineer_ids)):
            raise ModelValidationError("one engineer may decide only once")
        if any(item.candidate_id != self.candidate_id for item in self.approvals):
            raise ModelValidationError("approval candidate parent mismatch")
        if self.status == KnowledgeCandidateStatus.PUBLISHED and not self.published_document_id:
            raise ModelValidationError("published candidate requires published_document_id")
        if self.status != KnowledgeCandidateStatus.PUBLISHED and self.published_document_id:
            raise ModelValidationError("non-published candidate cannot reference a document")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported ingestion candidate schema_version")

    @property
    def approval_count(self) -> int:
        return sum(item.decision == ApprovalDecision.APPROVE for item in self.approvals)

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "filename": self.filename,
            "source_format": self.source_format,
            "document_type": self.document_type,
            "case_id": self.case_id,
            "title": self.title,
            "content_sha256": self.content_sha256,
            "module": self.module,
            "equipment_type": self.equipment_type,
            "operation": self.operation,
            "defect_type": self.defect_type,
            "tags": list(self.tags),
            "status": self.status,
            "chunks": [
                item.to_dict(include_content=include_content) for item in self.chunks
            ],
            "chunk_count": len(self.chunks),
            "approvals": [item.to_dict() for item in self.approvals],
            "approval_count": self.approval_count,
            "required_approval_count": 2,
            "published_document_id": self.published_document_id,
            "publication_policy": self.publication_policy,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "schema_version": self.schema_version,
        }
        if include_content:
            payload["parsed_content"] = self.parsed_content
        else:
            payload["content_preview"] = self.parsed_content[:500]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            candidate_id=str(data["candidate_id"]),
            filename=str(data["filename"]),
            source_format=str(data["source_format"]),
            document_type=str(data["document_type"]),
            case_id=(str(data["case_id"]) if data.get("case_id") else None),
            title=str(data["title"]),
            parsed_content=str(data["parsed_content"]),
            content_sha256=str(data["content_sha256"]),
            module=str(data["module"]),
            equipment_type=str(data.get("equipment_type", "")),
            operation=str(data.get("operation", "")),
            defect_type=str(data.get("defect_type", "")),
            tags=tuple(str(item) for item in data.get("tags", [])),
            status=str(data.get("status", KnowledgeCandidateStatus.PENDING_APPROVAL)),
            chunks=tuple(KnowledgeChunk.from_dict(dict(item)) for item in data["chunks"]),
            approvals=tuple(
                KnowledgeIngestionApproval.from_dict(dict(item))
                for item in data.get("approvals", [])
            ),
            published_document_id=(
                str(data["published_document_id"])
                if data.get("published_document_id")
                else None
            ),
            publication_policy=str(
                data.get("publication_policy", "DUAL_ENGINEER_APPROVAL")
            ),
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class KnowledgeLookupPlan:
    intent: str
    question_kind: str
    query: str
    allowed_document_types: tuple[str, ...]
    reason: str
    module: str = ""
    equipment_type: str = ""
    operation: str = ""
    defect_type: str = ""
    tags: tuple[str, ...] = ()
    observation_scope: ObservationScope | None = None
    causal_search_scope: CausalSearchScope | None = None
    explicit_module_limit: bool = False
    top_k: int = 5

    def __post_init__(self) -> None:
        if self.intent != KnowledgeLookupIntent.KNOWLEDGE_LOOKUP:
            raise ModelValidationError("intent must be knowledge_lookup")
        kind = KnowledgeQuestionKind(self.question_kind)
        if self.allowed_document_types != (kind.document_type,):
            raise ModelValidationError("question kind and document-type scope do not match")
        _required(self.query, "query")
        _required(self.reason, "reason")
        if not isinstance(self.explicit_module_limit, bool):
            raise ModelValidationError("explicit_module_limit must be a boolean")
        if not 1 <= self.top_k <= 20:
            raise ModelValidationError("top_k must be between 1 and 20")

    @property
    def action(self) -> str:
        return KnowledgeQuestionKind(self.question_kind).action

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "question_kind": self.question_kind,
            "action": self.action,
            "query": self.query,
            "allowed_document_types": list(self.allowed_document_types),
            "reason": self.reason,
            "module": self.module,
            "equipment_type": self.equipment_type,
            "operation": self.operation,
            "defect_type": self.defect_type,
            "tags": list(self.tags),
            "observation_scope": (
                self.observation_scope.to_dict() if self.observation_scope else None
            ),
            "causal_search_scope": (
                self.causal_search_scope.to_dict() if self.causal_search_scope else None
            ),
            "explicit_module_limit": self.explicit_module_limit,
            "top_k": self.top_k,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        observation = data.get("observation_scope")
        causal_scope = data.get("causal_search_scope")
        return cls(
            intent=str(data["intent"]),
            question_kind=str(data["question_kind"]),
            query=str(data["query"]),
            allowed_document_types=tuple(
                str(item) for item in data["allowed_document_types"]
            ),
            reason=str(data["reason"]),
            module=str(data.get("module", "")),
            equipment_type=str(data.get("equipment_type", "")),
            operation=str(data.get("operation", "")),
            defect_type=str(data.get("defect_type", "")),
            tags=tuple(str(item) for item in data.get("tags", [])),
            observation_scope=(
                ObservationScope.from_dict(dict(observation))
                if isinstance(observation, dict)
                else None
            ),
            causal_search_scope=(
                CausalSearchScope.from_dict(dict(causal_scope))
                if isinstance(causal_scope, dict)
                else None
            ),
            explicit_module_limit=data.get("explicit_module_limit", False),
            top_k=int(data.get("top_k", 5)),
        )


@dataclass(frozen=True)
class KnowledgeLookupHit:
    rank: int
    document: KnowledgeDocument
    score: float
    matched_chunk_ids: tuple[str, ...]
    excerpt: str
    evidence_id: str
    relevance_reason: str
    retrieval_strategy: str = "keyword"
    score_components: dict[str, float] = field(default_factory=dict)
    calibrated_relevance: float | None = None
    source_confidence: float | None = None
    candidate_lanes: tuple[str, ...] = ()
    scope_reasons: tuple[str, ...] = ()
    route_distance: int | None = None
    shared_resource_types: tuple[str, ...] = ()
    scope_fusion_score: float | None = None

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ModelValidationError("lookup hit rank must be positive")
        if not 0 <= self.score <= 1:
            raise ModelValidationError("lookup hit score must be between 0 and 1")
        if not self.matched_chunk_ids:
            raise ModelValidationError("lookup hit requires a matched chunk")
        for name in ("excerpt", "evidence_id", "relevance_reason"):
            _required(str(getattr(self, name)), name)
        _required(self.retrieval_strategy, "retrieval_strategy")
        allowed_components = {"keyword", "lexical", "vector", "fusion", "reranker"}
        if not set(self.score_components) <= allowed_components:
            raise ModelValidationError("lookup hit contains an unknown score component")
        if any(not 0 <= value <= 1 for value in self.score_components.values()):
            raise ModelValidationError("lookup hit score components must be between 0 and 1")
        if self.calibrated_relevance is not None and not 0 <= self.calibrated_relevance <= 1:
            raise ModelValidationError("calibrated_relevance must be between 0 and 1")
        if self.source_confidence is not None and not 0 <= self.source_confidence <= 1:
            raise ModelValidationError("source_confidence must be between 0 and 1")
        if self.route_distance is not None and self.route_distance < 0:
            raise ModelValidationError("route_distance must be non-negative")
        if self.scope_fusion_score is not None and not 0 <= self.scope_fusion_score <= 1:
            raise ModelValidationError("scope_fusion_score must be between 0 and 1")
        for name in ("candidate_lanes", "scope_reasons", "shared_resource_types"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not item.strip() for item in values):
                raise ModelValidationError(f"{name} must contain non-empty strings")
        for lane in self.candidate_lanes:
            try:
                CausalLane(lane)
            except ValueError as exc:
                raise ModelValidationError(f"unknown candidate lane: {lane}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "document": self.document.to_dict(),
            "score": self.score,
            "matched_chunk_ids": list(self.matched_chunk_ids),
            "excerpt": self.excerpt,
            "evidence_id": self.evidence_id,
            "relevance_reason": self.relevance_reason,
            "retrieval_strategy": self.retrieval_strategy,
            "score_components": dict(self.score_components),
            "calibrated_relevance": self.calibrated_relevance,
            "source_confidence": self.source_confidence,
            "candidate_lanes": list(self.candidate_lanes),
            "scope_reasons": list(self.scope_reasons),
            "route_distance": self.route_distance,
            "shared_resource_types": list(self.shared_resource_types),
            "scope_fusion_score": self.scope_fusion_score,
        }


@dataclass(frozen=True)
class KnowledgeAgentTrace:
    agent: str
    action: str
    execution_reason: str
    inputs: dict[str, Any]
    output_evidence_ids: tuple[str, ...]
    stop_reason: str

    def __post_init__(self) -> None:
        if self.agent != "knowledge":
            raise ModelValidationError("independent lookup may only use Knowledge Agent")
        for name in ("action", "execution_reason", "stop_reason"):
            _required(str(getattr(self, name)), name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "action": self.action,
            "execution_reason": self.execution_reason,
            "inputs": dict(self.inputs),
            "output_evidence_ids": list(self.output_evidence_ids),
            "stop_reason": self.stop_reason,
        }


@dataclass(frozen=True)
class KnowledgeLookupResult:
    lookup_id: str
    plan: KnowledgeLookupPlan
    status: str
    hits: tuple[KnowledgeLookupHit, ...]
    agent_trace: tuple[KnowledgeAgentTrace, ...]
    answer_boundary: str
    warnings: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        _required(self.lookup_id, "lookup_id")
        if self.status not in {"completed", "no_match"}:
            raise ModelValidationError("lookup status must be completed or no_match")
        if self.status == "completed" and not self.hits:
            raise ModelValidationError("completed lookup requires hits")
        if self.status == "no_match" and self.hits:
            raise ModelValidationError("no_match lookup cannot contain hits")
        if len(self.agent_trace) != 1:
            raise ModelValidationError("lookup requires exactly one Knowledge Agent trace")
        _required(self.answer_boundary, "answer_boundary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookup_id": self.lookup_id,
            "intent": self.plan.intent,
            "question_kind": self.plan.question_kind,
            "status": self.status,
            "plan": self.plan.to_dict(),
            "hits": [item.to_dict() for item in self.hits],
            "agent_trace": [item.to_dict() for item in self.agent_trace],
            "answer_boundary": self.answer_boundary,
            "warnings": list(self.warnings),
            "root_cause_conclusion": None,
            "created_at": self.created_at,
        }
