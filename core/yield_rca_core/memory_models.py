"""Domain contracts for controlled RCA memory publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.models import SCHEMA_VERSION, ModelValidationError


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string")


class MemoryCandidateStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"
    REJECTED = "rejected"


class KnowledgeIndexStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class EngineerRole(StrEnum):
    YIELD_ENGINEER = "yield_engineer"
    PROCESS_ENGINEER = "process_engineer"
    EQUIPMENT_ENGINEER = "equipment_engineer"
    QUALITY_ENGINEER = "quality_engineer"


@dataclass(frozen=True)
class MemoryApproval:
    approval_id: str
    candidate_id: str
    engineer_id: str
    engineer_role: str
    decision: str
    comment: str = ""
    decided_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.approval_id, "approval_id")
        _required(self.candidate_id, "candidate_id")
        _required(self.engineer_id, "engineer_id")
        try:
            EngineerRole(self.engineer_role)
        except ValueError as exc:
            raise ModelValidationError("engineer_role is not registered") from exc
        try:
            ApprovalDecision(self.decision)
        except ValueError as exc:
            raise ModelValidationError("decision must be approve or reject") from exc
        if self.comment:
            _required(self.comment, "comment")
        _required(self.decided_at, "decided_at")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported memory approval schema_version")

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
            approval_id=data["approval_id"],
            candidate_id=data["candidate_id"],
            engineer_id=data["engineer_id"],
            engineer_role=data["engineer_role"],
            decision=data["decision"],
            comment=data.get("comment", ""),
            decided_at=data.get("decided_at", _now_iso()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    job_id: str
    status: str
    scope_level: str
    title: str
    incident_summary: str
    engineering_summary: str
    root_cause: str
    confidence: float
    recommendations: dict[str, list[dict[str, Any]]]
    evidence_ids: list[str]
    source_lot_id: str | None = None
    product_id: str | None = None
    requires_process_engineer_approval: bool = False
    evidence_snapshot: list[dict[str, Any]] = field(default_factory=list)
    knowledge_provenance: dict[str, Any] = field(default_factory=dict)
    reasoning_engine: str = "legacy"
    index_status: str = KnowledgeIndexStatus.NOT_REQUESTED.value
    index_attempts: int = 0
    index_error: str | None = None
    approvals: list[MemoryApproval] = field(default_factory=list)
    published_case_id: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_id",
            "job_id",
            "title",
            "incident_summary",
            "engineering_summary",
            "root_cause",
            "created_at",
            "updated_at",
        ):
            _required(str(getattr(self, field_name)), field_name)
        try:
            MemoryCandidateStatus(self.status)
        except ValueError as exc:
            raise ModelValidationError("invalid memory candidate status") from exc
        if self.scope_level not in {"event", "fab"}:
            raise ModelValidationError("scope_level must be event or fab")
        if not isinstance(self.confidence, int | float) or not 0 <= self.confidence <= 1:
            raise ModelValidationError("confidence must be between 0 and 1")
        if not self.evidence_ids or any(not item.strip() for item in self.evidence_ids):
            raise ModelValidationError("memory candidate requires evidence_ids")
        if not isinstance(self.recommendations, dict):
            raise ModelValidationError("recommendations must be an object")
        if self.source_lot_id is not None:
            _required(self.source_lot_id, "source_lot_id")
        if self.product_id is not None:
            _required(self.product_id, "product_id")
        if self.status == MemoryCandidateStatus.PUBLISHED.value and not self.published_case_id:
            raise ModelValidationError("published candidate requires published_case_id")
        try:
            KnowledgeIndexStatus(self.index_status)
        except ValueError as exc:
            raise ModelValidationError("invalid knowledge index status") from exc
        if self.status != MemoryCandidateStatus.PUBLISHED.value and (
            self.index_status != KnowledgeIndexStatus.NOT_REQUESTED.value
        ):
            raise ModelValidationError("only published candidates may have an index status")
        if not isinstance(self.index_attempts, int) or self.index_attempts < 0:
            raise ModelValidationError("index_attempts must be a non-negative integer")
        if self.index_error is not None:
            _required(self.index_error, "index_error")
        for item in self.evidence_snapshot:
            if not isinstance(item, dict):
                raise ModelValidationError("evidence_snapshot must contain objects")
            _required(str(item.get("evidence_id", "")), "evidence_snapshot evidence_id")
        if not isinstance(self.knowledge_provenance, dict):
            raise ModelValidationError("knowledge_provenance must be an object")
        _required(self.reasoning_engine, "reasoning_engine")
        engineer_ids = [item.engineer_id for item in self.approvals]
        if len(engineer_ids) != len(set(engineer_ids)):
            raise ModelValidationError("one engineer may decide only once per candidate")
        if any(item.candidate_id != self.candidate_id for item in self.approvals):
            raise ModelValidationError("approval candidate_id does not match candidate")
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError("unsupported memory candidate schema_version")

    @property
    def approval_count(self) -> int:
        return sum(
            item.decision == ApprovalDecision.APPROVE.value for item in self.approvals
        )

    @property
    def has_process_engineer_approval(self) -> bool:
        return any(
            item.decision == ApprovalDecision.APPROVE.value
            and item.engineer_role == EngineerRole.PROCESS_ENGINEER.value
            for item in self.approvals
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "job_id": self.job_id,
            "status": self.status,
            "scope_level": self.scope_level,
            "title": self.title,
            "incident_summary": self.incident_summary,
            "engineering_summary": self.engineering_summary,
            "root_cause": self.root_cause,
            "confidence": float(self.confidence),
            "recommendations": self.recommendations,
            "evidence_ids": list(self.evidence_ids),
            "source_lot_id": self.source_lot_id,
            "product_id": self.product_id,
            "requires_process_engineer_approval": self.requires_process_engineer_approval,
            "evidence_snapshot": [dict(item) for item in self.evidence_snapshot],
            "knowledge_provenance": dict(self.knowledge_provenance),
            "reasoning_engine": self.reasoning_engine,
            "index_status": self.index_status,
            "index_attempts": self.index_attempts,
            "index_error": self.index_error,
            "approvals": [item.to_dict() for item in self.approvals],
            "approval_count": self.approval_count,
            "required_approval_count": 2,
            "has_process_engineer_approval": self.has_process_engineer_approval,
            "published_case_id": self.published_case_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            candidate_id=data["candidate_id"],
            job_id=data["job_id"],
            status=data["status"],
            scope_level=data["scope_level"],
            title=data["title"],
            incident_summary=data["incident_summary"],
            engineering_summary=data["engineering_summary"],
            root_cause=data["root_cause"],
            confidence=float(data["confidence"]),
            recommendations={
                str(key): [dict(item) for item in value]
                for key, value in data["recommendations"].items()
            },
            evidence_ids=list(data["evidence_ids"]),
            source_lot_id=data.get("source_lot_id"),
            product_id=data.get("product_id"),
            requires_process_engineer_approval=bool(
                data.get("requires_process_engineer_approval", False)
            ),
            evidence_snapshot=[dict(item) for item in data.get("evidence_snapshot", [])],
            knowledge_provenance=dict(data.get("knowledge_provenance", {})),
            reasoning_engine=str(data.get("reasoning_engine", "legacy")),
            index_status=str(data.get("index_status", KnowledgeIndexStatus.NOT_REQUESTED.value)),
            index_attempts=int(data.get("index_attempts", 0)),
            index_error=(str(data["index_error"]) if data.get("index_error") else None),
            approvals=[MemoryApproval.from_dict(item) for item in data.get("approvals", [])],
            published_case_id=data.get("published_case_id"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
