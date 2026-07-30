"""Shared domain models and DTOs for the RCA workflow.

The models in this module are deliberately framework-free. They define the
contracts shared by Planner, Supervisor, Tools, Agents, RCA reasoning, and
report generation without implementing any business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.evidence_models import (
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from yield_rca_core.evidence_models import (
    AgentKind as AgentKind,
)
from yield_rca_core.evidence_models import (
    Evidence as Evidence,
)
from yield_rca_core.evidence_models import (
    EvidenceEntity as EvidenceEntity,
)
from yield_rca_core.evidence_models import (
    EvidenceSourceType as EvidenceSourceType,
)
from yield_rca_core.evidence_models import (
    EvidenceType as EvidenceType,
)
from yield_rca_core.evidence_models import (
    ModelValidationError as ModelValidationError,
)
from yield_rca_core.investigation_models import (
    ActionRecord,
    ConclusionLevel,
    GoalStatus,
    InvestigationAction,
    InvestigationGoal,
    StopReason,
)


class LotDrivenRCAError(ValueError):
    """Raised when a Lot-driven investigation cannot resolve its scope."""

    error_code = "LOT_CONTEXT_ERROR"


class LotNotFoundError(LotDrivenRCAError):
    """Raised when the requested Lot does not exist in the configured data source."""

    error_code = "LOT_NOT_FOUND"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class InvestigationMode(StrEnum):
    PRODUCT_WINDOW = "product_window"
    LOT = "lot"


class AgentMode(StrEnum):
    DETERMINISTIC = "deterministic"
    FAKE = "fake"
    LLM = "llm"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class HypothesisStatus(StrEnum):
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


class FindingKind(StrEnum):
    SPECIALIST_OBSERVATION = "specialist_observation"
    KNOWLEDGE_DISCOVERY = "knowledge_discovery"
    KNOWLEDGE_VALIDATION = "knowledge_validation"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_RANKING = "hypothesis_ranking"
    IMPROVEMENT = "improvement"


def _default_finding_kind(agent: str) -> str:
    if agent == AgentKind.KNOWLEDGE.value:
        return FindingKind.KNOWLEDGE_DISCOVERY.value
    if agent == AgentKind.RCA_REASONING.value:
        return FindingKind.HYPOTHESIS_RANKING.value
    if agent == AgentKind.IMPROVEMENT.value:
        return FindingKind.IMPROVEMENT.value
    return FindingKind.SPECIALIST_OBSERVATION.value


def _validate_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string")


def _validate_confidence(value: float, field_name: str = "confidence") -> None:
    if not isinstance(value, int | float):
        raise ModelValidationError(f"{field_name} must be a number")
    if not 0 <= float(value) <= 1:
        raise ModelValidationError(f"{field_name} must be between 0 and 1")


def _validate_string_list(values: list[str], field_name: str, *, allow_empty: bool) -> None:
    if not isinstance(values, list):
        raise ModelValidationError(f"{field_name} must be a list")
    if not allow_empty and not values:
        raise ModelValidationError(f"{field_name} must not be empty")
    for index, value in enumerate(values):
        _validate_non_empty(value, f"{field_name}[{index}]")


def _enum_value(value: StrEnum | str, enum_type: type[StrEnum], field_name: str) -> str:
    try:
        return enum_type(value).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ModelValidationError(f"{field_name} must be one of: {allowed}") from exc


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_schema_version(schema_version: str) -> None:
    if schema_version != SCHEMA_VERSION:
        raise ModelValidationError(
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )


def _validate_json_object(value: dict[str, Any], field_name: str) -> None:
    if not isinstance(value, dict):
        raise ModelValidationError(f"{field_name} must be a JSON object")
    for key in value:
        _validate_non_empty(key, f"{field_name} key")


def _coerce_evidence_list(value: object, field_name: str) -> list[Evidence]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ModelValidationError(f"{field_name} must be a list")
    evidence: list[Evidence] = []
    for index, item in enumerate(value):
        if isinstance(item, Evidence):
            evidence.append(item)
        elif isinstance(item, dict):
            evidence.append(Evidence.from_dict(item))
        else:
            raise ModelValidationError(
                f"{field_name}[{index}] must be an Evidence instance or JSON object"
            )
    return evidence


def _normalize_evidence_envelope(
    *,
    evidence_ids: list[str],
    evidence: list[Evidence],
    legacy_container: dict[str, Any],
    legacy_field: str,
) -> tuple[list[str], list[Evidence], dict[str, Any]]:
    _validate_string_list(evidence_ids, "evidence_ids", allow_empty=True)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ModelValidationError("evidence_ids must not contain duplicates")

    first_class = _coerce_evidence_list(evidence, "evidence")
    mirrored = _coerce_evidence_list(
        legacy_container.get("evidence"),
        f'{legacy_field}["evidence"]',
    )
    if first_class and mirrored:
        first_payload = [item.to_dict() for item in first_class]
        mirrored_payload = [item.to_dict() for item in mirrored]
        if first_payload != mirrored_payload:
            raise ModelValidationError(
                f'evidence and {legacy_field}["evidence"] must contain identical payloads'
            )

    normalized_evidence = first_class or mirrored
    normalized_ids = [item.evidence_id for item in normalized_evidence]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ModelValidationError("evidence must not contain duplicate evidence_id values")
    if normalized_evidence and evidence_ids and evidence_ids != normalized_ids:
        raise ModelValidationError("evidence_ids must match first-class Evidence in the same order")

    normalized_container = dict(legacy_container)
    if normalized_evidence:
        normalized_container["evidence"] = [item.to_dict() for item in normalized_evidence]
    return (
        normalized_ids if normalized_evidence else list(evidence_ids),
        normalized_evidence,
        normalized_container,
    )


@dataclass(frozen=True)
class Warning:
    warning_id: str
    message: str
    severity: str = Severity.WARNING.value
    evidence_ids: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.warning_id, "warning_id")
        _validate_non_empty(self.message, "message")
        _enum_value(self.severity, Severity, "severity")
        _validate_string_list(self.evidence_ids, "evidence_ids", allow_empty=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_id": self.warning_id,
            "message": self.message,
            "severity": self.severity,
            "evidence_ids": list(self.evidence_ids),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            warning_id=data["warning_id"],
            message=data["message"],
            severity=data.get("severity", Severity.WARNING.value),
            evidence_ids=list(data.get("evidence_ids", [])),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    agent: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    status: str = TaskStatus.PENDING.value
    inputs: dict[str, Any] = field(default_factory=dict)
    finding_kind: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.task_id, "task_id")
        _enum_value(self.agent, AgentKind, "agent")
        object.__setattr__(
            self,
            "finding_kind",
            _enum_value(
                self.finding_kind or _default_finding_kind(self.agent),
                FindingKind,
                "finding_kind",
            ),
        )
        _validate_non_empty(self.objective, "objective")
        _validate_string_list(self.depends_on, "depends_on", allow_empty=True)
        _enum_value(self.status, TaskStatus, "status")
        _validate_json_object(self.inputs, "inputs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "objective": self.objective,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "inputs": dict(self.inputs),
            "finding_kind": self.finding_kind,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            task_id=data["task_id"],
            agent=data["agent"],
            objective=data["objective"],
            depends_on=list(data.get("depends_on", [])),
            status=data.get("status", TaskStatus.PENDING.value),
            inputs=dict(data.get("inputs", {})),
            finding_kind=data.get("finding_kind", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class TaskPlan:
    plan_id: str
    objective: str
    tasks: list[AgentTask]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.plan_id, "plan_id")
        _validate_non_empty(self.objective, "objective")
        if not self.tasks:
            raise ModelValidationError("tasks must not be empty")
        self._validate_task_graph()

    def _validate_task_graph(self) -> None:
        task_ids = [task.task_id for task in self.tasks]
        duplicate_ids = {task_id for task_id in task_ids if task_ids.count(task_id) > 1}
        if duplicate_ids:
            raise ModelValidationError(f"duplicate task_id values: {sorted(duplicate_ids)}")

        known = set(task_ids)
        for task in self.tasks:
            missing = set(task.depends_on) - known
            if missing:
                raise ModelValidationError(
                    f"task {task.task_id} depends on unknown tasks: {sorted(missing)}"
                )

        graph = {task.task_id: set(task.depends_on) for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ModelValidationError("task graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency_id in graph[task_id]:
                visit(dependency_id)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "tasks": [task.to_dict() for task in self.tasks],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            plan_id=data["plan_id"],
            objective=data["objective"],
            tasks=[AgentTask.from_dict(item) for item in data["tasks"]],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    root_cause: str
    confidence: float
    evidence_ids: list[str]
    status: str = HypothesisStatus.CANDIDATE.value
    rationale: str = ""
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    neutral_evidence_ids: list[str] = field(default_factory=list)
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    rank: int | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.hypothesis_id, "hypothesis_id")
        _validate_non_empty(self.root_cause, "root_cause")
        _validate_confidence(self.confidence)
        _validate_string_list(self.evidence_ids, "evidence_ids", allow_empty=False)
        _enum_value(self.status, HypothesisStatus, "status")
        if self.rationale:
            _validate_non_empty(self.rationale, "rationale")
        _validate_string_list(
            self.supporting_evidence_ids, "supporting_evidence_ids", allow_empty=True
        )
        _validate_string_list(
            self.contradicting_evidence_ids, "contradicting_evidence_ids", allow_empty=True
        )
        _validate_string_list(
            self.neutral_evidence_ids, "neutral_evidence_ids", allow_empty=True
        )
        _validate_string_list(self.rejection_reasons, "rejection_reasons", allow_empty=True)
        if self.rank is not None and (not isinstance(self.rank, int) or self.rank < 1):
            raise ModelValidationError("rank must be a positive integer when provided")
        for result in self.validation_results:
            _validate_json_object(result, "validation_results item")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "root_cause": self.root_cause,
            "confidence": float(self.confidence),
            "evidence_ids": list(self.evidence_ids),
            "status": self.status,
            "rationale": self.rationale,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
            "neutral_evidence_ids": list(self.neutral_evidence_ids),
            "validation_results": [dict(item) for item in self.validation_results],
            "rank": self.rank,
            "rejection_reasons": list(self.rejection_reasons),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            hypothesis_id=data["hypothesis_id"],
            root_cause=data["root_cause"],
            confidence=float(data["confidence"]),
            evidence_ids=list(data["evidence_ids"]),
            status=data.get("status", HypothesisStatus.CANDIDATE.value),
            rationale=data.get("rationale", ""),
            supporting_evidence_ids=list(data.get("supporting_evidence_ids", [])),
            contradicting_evidence_ids=list(data.get("contradicting_evidence_ids", [])),
            neutral_evidence_ids=list(data.get("neutral_evidence_ids", [])),
            validation_results=[dict(item) for item in data.get("validation_results", [])],
            rank=data.get("rank"),
            rejection_reasons=list(data.get("rejection_reasons", [])),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class AgentFinding:
    finding_id: str
    agent: str
    summary: str
    confidence: float
    evidence_ids: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[Warning] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    evidence: list[Evidence] = field(default_factory=list)
    task_id: str | None = None
    finding_kind: str = ""

    def __post_init__(self) -> None:
        evidence_ids, evidence, details = _normalize_evidence_envelope(
            evidence_ids=self.evidence_ids,
            evidence=self.evidence,
            legacy_container=self.details,
            legacy_field="details",
        )
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "details", details)
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.finding_id, "finding_id")
        _enum_value(self.agent, AgentKind, "agent")
        if self.task_id is not None:
            _validate_non_empty(self.task_id, "task_id")
        object.__setattr__(
            self,
            "finding_kind",
            _enum_value(
                self.finding_kind or _default_finding_kind(self.agent),
                FindingKind,
                "finding_kind",
            ),
        )
        _validate_non_empty(self.summary, "summary")
        _validate_confidence(self.confidence)
        _validate_string_list(self.evidence_ids, "evidence_ids", allow_empty=False)
        _validate_json_object(self.details, "details")
        for warning in self.warnings:
            if not isinstance(warning, Warning):
                raise ModelValidationError("warnings must contain Warning instances")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "finding_kind": self.finding_kind,
            "summary": self.summary,
            "confidence": float(self.confidence),
            "evidence_ids": list(self.evidence_ids),
            "evidence": [item.to_dict() for item in self.evidence],
            "details": dict(self.details),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            finding_id=data["finding_id"],
            task_id=data.get("task_id"),
            agent=data["agent"],
            finding_kind=data.get("finding_kind", ""),
            summary=data["summary"],
            confidence=float(data["confidence"]),
            evidence_ids=list(data["evidence_ids"]),
            evidence=_coerce_evidence_list(data.get("evidence", []), "evidence"),
            details=dict(data.get("details", {})),
            warnings=[Warning.from_dict(item) for item in data.get("warnings", [])],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class ToolInput:
    tool_name: str
    request_id: str
    parameters: dict[str, Any]
    requested_by: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.tool_name, "tool_name")
        _validate_non_empty(self.request_id, "request_id")
        _validate_json_object(self.parameters, "parameters")
        _enum_value(self.requested_by, AgentKind, "requested_by")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "request_id": self.request_id,
            "parameters": dict(self.parameters),
            "requested_by": self.requested_by,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            tool_name=data["tool_name"],
            request_id=data["request_id"],
            parameters=dict(data["parameters"]),
            requested_by=data["requested_by"],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class ToolOutput:
    tool_name: str
    request_id: str
    success: bool
    data: dict[str, Any]
    evidence_ids: list[str] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    error_code: str | None = None
    schema_version: str = SCHEMA_VERSION
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        evidence_ids, evidence, data = _normalize_evidence_envelope(
            evidence_ids=self.evidence_ids,
            evidence=self.evidence,
            legacy_container=self.data,
            legacy_field="data",
        )
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "data", data)
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.tool_name, "tool_name")
        _validate_non_empty(self.request_id, "request_id")
        if not isinstance(self.success, bool):
            raise ModelValidationError("success must be a boolean")
        _validate_json_object(self.data, "data")
        _validate_string_list(self.evidence_ids, "evidence_ids", allow_empty=True)
        for warning in self.warnings:
            if not isinstance(warning, Warning):
                raise ModelValidationError("warnings must contain Warning instances")
        if self.error_code is not None:
            _validate_non_empty(self.error_code, "error_code")
        if self.success and self.error_code is not None:
            raise ModelValidationError("successful ToolOutput must not include error_code")
        if not self.success and self.error_code is None:
            raise ModelValidationError("failed ToolOutput must include error_code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "request_id": self.request_id,
            "success": self.success,
            "data": dict(self.data),
            "evidence_ids": list(self.evidence_ids),
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "error_code": self.error_code,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            tool_name=data["tool_name"],
            request_id=data["request_id"],
            success=bool(data["success"]),
            data=dict(data["data"]),
            evidence_ids=list(data.get("evidence_ids", [])),
            evidence=_coerce_evidence_list(data.get("evidence", []), "evidence"),
            warnings=[Warning.from_dict(item) for item in data.get("warnings", [])],
            error_code=data.get("error_code"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class Report:
    report_id: str
    title: str
    markdown: str
    cited_evidence_ids: list[str]
    created_at: str = field(default_factory=_utc_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.report_id, "report_id")
        _validate_non_empty(self.title, "title")
        _validate_non_empty(self.markdown, "markdown")
        _validate_string_list(self.cited_evidence_ids, "cited_evidence_ids", allow_empty=False)
        _validate_non_empty(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "title": self.title,
            "markdown": self.markdown,
            "cited_evidence_ids": list(self.cited_evidence_ids),
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            report_id=data["report_id"],
            title=data["title"],
            markdown=data["markdown"],
            cited_evidence_ids=list(data["cited_evidence_ids"]),
            created_at=data.get("created_at", _utc_now_iso()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class RCAJob:
    job_id: str
    user_query: str
    investigation_mode: str = InvestigationMode.PRODUCT_WINDOW.value
    source_lot_id: str | None = None
    product_id: str | None = None
    time_window: dict[str, str] = field(default_factory=dict)
    status: str = TaskStatus.PENDING.value
    created_at: str = field(default_factory=_utc_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.job_id, "job_id")
        _validate_non_empty(self.user_query, "user_query")
        _enum_value(self.investigation_mode, InvestigationMode, "investigation_mode")
        if self.source_lot_id is not None:
            _validate_non_empty(self.source_lot_id, "source_lot_id")
        if self.investigation_mode == InvestigationMode.LOT.value and self.source_lot_id is None:
            raise ModelValidationError("lot investigation requires source_lot_id")
        if self.product_id is not None:
            _validate_non_empty(self.product_id, "product_id")
        _validate_json_object(self.time_window, "time_window")
        _enum_value(self.status, TaskStatus, "status")
        _validate_non_empty(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_query": self.user_query,
            "investigation_mode": self.investigation_mode,
            "source_lot_id": self.source_lot_id,
            "product_id": self.product_id,
            "time_window": dict(self.time_window),
            "status": self.status,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            job_id=data["job_id"],
            user_query=data["user_query"],
            investigation_mode=data.get(
                "investigation_mode",
                InvestigationMode.PRODUCT_WINDOW.value,
            ),
            source_lot_id=data.get("source_lot_id"),
            product_id=data.get("product_id"),
            time_window=dict(data.get("time_window", {})),
            status=data.get("status", TaskStatus.PENDING.value),
            created_at=data.get("created_at", _utc_now_iso()),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class LLMUsageEvent:
    call_id: str
    agent: str
    provider: str
    model: str
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "success"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_empty(self.call_id, "call_id")
        _enum_value(self.agent, AgentKind, "agent")
        _validate_non_empty(self.provider, "provider")
        _validate_non_empty(self.model, "model")
        _validate_non_empty(self.prompt_version, "prompt_version")
        for field_name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                raise ModelValidationError(f"{field_name} must be a non-negative integer")
        if self.total_tokens < self.prompt_tokens + self.completion_tokens:
            raise ModelValidationError(
                "total_tokens must not be less than prompt_tokens + completion_tokens"
            )
        if not isinstance(self.latency_ms, int | float) or self.latency_ms < 0:
            raise ModelValidationError("latency_ms must be a non-negative number")
        if self.status not in {"success", "failed"}:
            raise ModelValidationError("LLM usage status must be success or failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "agent": self.agent,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "latency_ms": float(self.latency_ms),
            "status": self.status,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            call_id=data["call_id"],
            agent=data["agent"],
            provider=data["provider"],
            model=data["model"],
            prompt_version=data["prompt_version"],
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
            cached_tokens=int(data.get("cached_tokens", 0)),
            reasoning_tokens=int(data.get("reasoning_tokens", 0)),
            latency_ms=float(data.get("latency_ms", 0.0)),
            status=data.get("status", "success"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class RCAState:
    job: RCAJob
    task_plan: TaskPlan | None = None
    current_task_id: str | None = None
    completed_task_ids: list[str] = field(default_factory=list)
    affected_lots: list[str] = field(default_factory=list)
    impact_lots: list[str] = field(default_factory=list)
    affected_wafers: list[str] = field(default_factory=list)
    impact_wafers: list[str] = field(default_factory=list)
    scope_level: str = "lot"
    impact_criteria: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    findings: list[AgentFinding] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    report: Report | None = None
    llm_usage: list[LLMUsageEvent] = field(default_factory=list)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    investigation_goal: InvestigationGoal | None = None
    action_history: list[ActionRecord] = field(default_factory=list)
    goal_status: str | None = None
    conclusion_level: str | None = None
    evidence_gaps: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        if not isinstance(self.job, RCAJob):
            raise ModelValidationError("job must be an RCAJob")
        if self.task_plan is not None and not isinstance(self.task_plan, TaskPlan):
            raise ModelValidationError("task_plan must be a TaskPlan")
        if self.current_task_id is not None:
            _validate_non_empty(self.current_task_id, "current_task_id")
        _validate_string_list(self.completed_task_ids, "completed_task_ids", allow_empty=True)
        _validate_string_list(self.affected_lots, "affected_lots", allow_empty=True)
        _validate_string_list(self.impact_lots, "impact_lots", allow_empty=True)
        _validate_string_list(self.affected_wafers, "affected_wafers", allow_empty=True)
        _validate_string_list(self.impact_wafers, "impact_wafers", allow_empty=True)
        if self.scope_level not in {"lot", "wafer", "mixed"}:
            raise ModelValidationError("scope_level must be lot, wafer, or mixed")
        _validate_json_object(self.impact_criteria, "impact_criteria")
        if self.job.source_lot_id and self.job.source_lot_id in self.impact_lots:
            raise ModelValidationError("impact_lots must exclude source_lot_id")
        for usage in self.llm_usage:
            if not isinstance(usage, LLMUsageEvent):
                raise ModelValidationError("llm_usage must contain LLMUsageEvent instances")
        if self.investigation_goal is not None and not isinstance(
            self.investigation_goal, InvestigationGoal
        ):
            raise ModelValidationError("investigation_goal must be an InvestigationGoal")
        for record in self.action_history:
            if not isinstance(record, ActionRecord):
                raise ModelValidationError("action_history must contain ActionRecord instances")
        if self.goal_status is not None:
            try:
                GoalStatus(self.goal_status)
            except ValueError as exc:
                raise ModelValidationError("goal_status is invalid") from exc
        if self.conclusion_level is not None:
            try:
                ConclusionLevel(self.conclusion_level)
            except ValueError as exc:
                raise ModelValidationError("conclusion_level is invalid") from exc
        _validate_string_list(self.evidence_gaps, "evidence_gaps", allow_empty=True)
        if self.stop_reason is not None:
            try:
                StopReason(self.stop_reason)
            except ValueError as exc:
                raise ModelValidationError("stop_reason is invalid") from exc
        _validate_json_object(self.execution_metadata, "execution_metadata")
        self._validate_evidence_references()
        self._validate_task_references()

    @property
    def evidence_by_id(self) -> dict[str, Evidence]:
        return {item.evidence_id: item for item in self.evidence}

    @property
    def evidence_by_type(self) -> dict[str, list[Evidence]]:
        indexed: dict[str, list[Evidence]] = {}
        for item in self.evidence:
            if item.evidence_type is not None:
                indexed.setdefault(item.evidence_type, []).append(item)
        return indexed

    @property
    def evidence_by_entity(self) -> dict[tuple[str, str], list[Evidence]]:
        indexed: dict[tuple[str, str], list[Evidence]] = {}
        for item in self.evidence:
            for entity in item.entities:
                indexed.setdefault((entity.entity_type, entity.entity_id), []).append(item)
        return indexed

    def finding_for_task(self, task_id: str) -> AgentFinding | None:
        _validate_non_empty(task_id, "task_id")
        return next((item for item in self.findings if item.task_id == task_id), None)

    def findings_for_agent(self, agent: str) -> list[AgentFinding]:
        normalized_agent = _enum_value(agent, AgentKind, "agent")
        return [item for item in self.findings if item.agent == normalized_agent]

    def findings_for_kind(
        self,
        finding_kind: str,
        *,
        agent: str | None = None,
    ) -> list[AgentFinding]:
        normalized_kind = _enum_value(finding_kind, FindingKind, "finding_kind")
        normalized_agent = _enum_value(agent, AgentKind, "agent") if agent is not None else None
        return [
            item
            for item in self.findings
            if item.finding_kind == normalized_kind
            and (normalized_agent is None or item.agent == normalized_agent)
        ]

    def _validate_evidence_references(self) -> None:
        evidence_ids = [item.evidence_id for item in self.evidence]
        duplicates = {item_id for item_id in evidence_ids if evidence_ids.count(item_id) > 1}
        if duplicates:
            raise ModelValidationError(f"duplicate evidence_id values: {sorted(duplicates)}")
        known_evidence_ids = set(evidence_ids)

        finding_ids = [item.finding_id for item in self.findings]
        duplicate_finding_ids = {
            finding_id for finding_id in finding_ids if finding_ids.count(finding_id) > 1
        }
        if duplicate_finding_ids:
            raise ModelValidationError(
                f"duplicate finding_id values: {sorted(duplicate_finding_ids)}"
            )
        finding_task_ids = [item.task_id for item in self.findings if item.task_id is not None]
        duplicate_finding_task_ids = {
            task_id for task_id in finding_task_ids if finding_task_ids.count(task_id) > 1
        }
        if duplicate_finding_task_ids:
            raise ModelValidationError(
                "multiple findings reference the same task_id: "
                f"{sorted(duplicate_finding_task_ids)}"
            )

        for finding in self.findings:
            if not isinstance(finding, AgentFinding):
                raise ModelValidationError("findings must contain AgentFinding instances")
            self._validate_reference_set(finding.evidence_ids, known_evidence_ids, "finding")
            for warning in finding.warnings:
                self._validate_reference_set(warning.evidence_ids, known_evidence_ids, "warning")
        for hypothesis in self.hypotheses:
            self._validate_reference_set(hypothesis.evidence_ids, known_evidence_ids, "hypothesis")
        for warning in self.warnings:
            self._validate_reference_set(warning.evidence_ids, known_evidence_ids, "warning")
        if self.report is not None:
            self._validate_reference_set(
                self.report.cited_evidence_ids,
                known_evidence_ids,
                "report",
            )

    @staticmethod
    def _validate_reference_set(values: list[str], known_values: set[str], context: str) -> None:
        missing = set(values) - known_values
        if missing:
            raise ModelValidationError(
                f"{context} references unknown evidence_ids: {sorted(missing)}"
            )

    def _validate_task_references(self) -> None:
        if self.task_plan is None:
            if self.current_task_id is not None:
                raise ModelValidationError("current_task_id requires task_plan")
            if self.completed_task_ids:
                raise ModelValidationError("completed_task_ids requires task_plan")
            return
        tasks_by_id = {task.task_id: task for task in self.task_plan.tasks}
        task_ids = set(tasks_by_id)
        if self.current_task_id is not None and self.current_task_id not in task_ids:
            raise ModelValidationError("current_task_id must exist in task_plan")
        missing_completed = set(self.completed_task_ids) - task_ids
        if missing_completed:
            raise ModelValidationError(
                f"completed_task_ids contains unknown tasks: {sorted(missing_completed)}"
            )
        for finding in self.findings:
            if finding.task_id is None:
                continue
            task = tasks_by_id.get(finding.task_id)
            if task is None:
                raise ModelValidationError(
                    f"finding references unknown task_id: {finding.task_id!r}"
                )
            if finding.agent != task.agent:
                raise ModelValidationError(
                    f"finding agent does not match task {finding.task_id!r}"
                )
            if finding.finding_kind != task.finding_kind:
                raise ModelValidationError(
                    f"finding_kind does not match task {finding.task_id!r}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job.to_dict(),
            "task_plan": self.task_plan.to_dict() if self.task_plan else None,
            "current_task_id": self.current_task_id,
            "completed_task_ids": list(self.completed_task_ids),
            "affected_lots": list(self.affected_lots),
            "impact_lots": list(self.impact_lots),
            "affected_wafers": list(self.affected_wafers),
            "impact_wafers": list(self.impact_wafers),
            "scope_level": self.scope_level,
            "impact_criteria": dict(self.impact_criteria),
            "evidence": [item.to_dict() for item in self.evidence],
            "findings": [item.to_dict() for item in self.findings],
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "warnings": [item.to_dict() for item in self.warnings],
            "report": self.report.to_dict() if self.report else None,
            "llm_usage": [item.to_dict() for item in self.llm_usage],
            "execution_metadata": dict(self.execution_metadata),
            "investigation_goal": (
                self.investigation_goal.to_dict() if self.investigation_goal else None
            ),
            "action_history": [item.to_dict() for item in self.action_history],
            "goal_status": self.goal_status,
            "conclusion_level": self.conclusion_level,
            "evidence_gaps": list(self.evidence_gaps),
            "stop_reason": self.stop_reason,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            job=RCAJob.from_dict(data["job"]),
            task_plan=TaskPlan.from_dict(data["task_plan"]) if data.get("task_plan") else None,
            current_task_id=data.get("current_task_id"),
            completed_task_ids=list(data.get("completed_task_ids", [])),
            affected_lots=list(data.get("affected_lots", [])),
            impact_lots=list(data.get("impact_lots", [])),
            affected_wafers=list(data.get("affected_wafers", [])),
            impact_wafers=list(data.get("impact_wafers", [])),
            scope_level=data.get("scope_level", "lot"),
            impact_criteria=dict(data.get("impact_criteria", {})),
            evidence=[Evidence.from_dict(item) for item in data.get("evidence", [])],
            findings=[AgentFinding.from_dict(item) for item in data.get("findings", [])],
            hypotheses=[Hypothesis.from_dict(item) for item in data.get("hypotheses", [])],
            warnings=[Warning.from_dict(item) for item in data.get("warnings", [])],
            report=Report.from_dict(data["report"]) if data.get("report") else None,
            llm_usage=[LLMUsageEvent.from_dict(item) for item in data.get("llm_usage", [])],
            execution_metadata=dict(data.get("execution_metadata", {})),
            investigation_goal=(
                InvestigationGoal(**dict(data["investigation_goal"]))
                if data.get("investigation_goal")
                else None
            ),
            action_history=[
                ActionRecord(
                    action=InvestigationAction.from_dict(item["action"]),
                    status=item["status"],
                    produced_finding_ids=list(item.get("produced_finding_ids", [])),
                    produced_evidence_ids=list(item.get("produced_evidence_ids", [])),
                    decision_summary=item["decision_summary"],
                )
                for item in data.get("action_history", [])
            ],
            goal_status=data.get("goal_status"),
            conclusion_level=data.get("conclusion_level"),
            evidence_gaps=list(data.get("evidence_gaps", [])),
            stop_reason=data.get("stop_reason"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
