"""Typed Evidence contracts for the Yield RCA domain.

This module owns the evidence-facing enums and models so the rest of the
domain can continue importing them through ``yield_rca_core.models`` while
the Evidence layer evolves independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

SCHEMA_VERSION = "1.0"
EVIDENCE_SCHEMA_VERSION = "1.0"


class ModelValidationError(ValueError):
    """Raised when a domain model violates its structural contract."""


class AgentKind(StrEnum):
    PLANNER = "planner"
    SUPERVISOR = "supervisor"
    MES = "mes"
    FDC = "fdc"
    DEFECT_WAT = "defect_wat"
    KNOWLEDGE = "knowledge"
    RCA_REASONING = "rca_reasoning"
    IMPROVEMENT = "improvement"
    REPORT = "report"


class EvidenceSourceType(StrEnum):
    MES = "mes"
    FDC = "fdc"
    DEFECT = "defect"
    WAT = "wat"
    KNOWLEDGE = "knowledge"
    ANALYTICS = "analytics"
    USER = "user"
    SYSTEM = "system"


class EvidenceType(StrEnum):
    LOT_CONTEXT = "lot_context"
    PROCESS_EXPOSURE = "process_exposure"
    EQUIPMENT_EXPOSURE = "equipment_exposure"
    IMPACT_SCOPE = "impact_scope"
    RECIPE_CHANGE = "recipe_change"
    HOLD_EVENT = "hold_event"
    PARAMETER_DEVIATION = "parameter_deviation"
    TREND_DEVIATION = "trend_deviation"
    OOC_EVENT = "ooc_event"
    SPC_VIOLATION = "spc_violation"
    EXCURSION_WINDOW = "excursion_window"
    DEFECT_SIGNAL = "defect_signal"
    METROLOGY_DEVIATION = "metrology_deviation"
    ELECTRICAL_FAILURE = "electrical_failure"
    HISTORICAL_CASE_MATCH = "historical_case_match"
    SOP_GUIDANCE = "sop_guidance"
    ENGINEERING_NOTE = "engineering_note"
    NEGATIVE_SIGNAL = "negative_signal"
    DATA_MISSING = "data_missing"


class EntityType(StrEnum):
    PRODUCT = "product"
    LOT = "lot"
    WAFER = "wafer"
    ROUTE = "route"
    OPERATION = "operation"
    EQUIPMENT = "equipment"
    CHAMBER = "chamber"
    RECIPE = "recipe"
    PARAMETER = "parameter"
    DEFECT = "defect"
    WAT_ITEM = "wat_item"
    EXCURSION = "excursion"
    KNOWLEDGE_ASSET = "knowledge_asset"


def _validate_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string")


def _validate_enum(
    value: StrEnum | str,
    enum_type: type[StrEnum],
    field_name: str,
) -> None:
    try:
        enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ModelValidationError(f"{field_name} must be one of: {allowed}") from exc


def _validate_confidence(value: float, field_name: str = "confidence") -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModelValidationError(f"{field_name} must be a number")
    if not 0 <= float(value) <= 1:
        raise ModelValidationError(f"{field_name} must be between 0 and 1")


def _freeze_json_value(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            _validate_non_empty(key, f"{field_name} key")
            frozen[key] = _freeze_json_value(item, f"{field_name}[{key!r}]")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json_value(item, f"{field_name}[{index}]") for index, item in enumerate(value)
        )
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ModelValidationError(f"{field_name} must contain only JSON-compatible values")


def _freeze_json_object(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelValidationError(f"{field_name} must be a JSON object")
    frozen = _freeze_json_value(value, field_name)
    if not isinstance(frozen, Mapping):
        raise ModelValidationError(f"{field_name} must be a JSON object")
    return frozen


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json_value(item) for item in value]
    return value


def _thaw_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


@dataclass(frozen=True)
class EvidenceEntity:
    """A typed industrial entity referenced by an Evidence observation."""

    entity_type: str
    entity_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_enum(self.entity_type, EntityType, "entity_type")
        _validate_non_empty(self.entity_id, "entity_id")
        object.__setattr__(
            self,
            "attributes",
            _freeze_json_object(self.attributes, "attributes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "attributes": _thaw_json_object(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            attributes=dict(data.get("attributes", {})),
        )


@dataclass(frozen=True)
class Evidence:
    """An immutable, traceable observation used by the RCA workflow.

    The original fields remain unchanged. The V1 fields are an additive,
    all-or-none contract: legacy Evidence can still be constructed and
    serialized exactly as before, while typed Evidence must be complete.
    """

    evidence_id: str
    source_type: str
    source_id: str
    summary: str
    source_table: str | None = None
    source_field: str | None = None
    timestamp: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    evidence_type: str | None = None
    source_agent: str | None = None
    source_tool: str | None = None
    observation: str | None = None
    entities: Sequence[EvidenceEntity] = field(default_factory=tuple)
    confidence: float | None = None
    evidence_schema_version: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ModelValidationError(
                f"unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}"
            )
        _validate_non_empty(self.evidence_id, "evidence_id")
        _validate_enum(self.source_type, EvidenceSourceType, "source_type")
        _validate_non_empty(self.source_id, "source_id")
        _validate_non_empty(self.summary, "summary")
        if self.source_table is not None:
            _validate_non_empty(self.source_table, "source_table")
        if self.source_field is not None:
            _validate_non_empty(self.source_field, "source_field")
        if self.timestamp is not None:
            _validate_non_empty(self.timestamp, "timestamp")
        object.__setattr__(
            self,
            "metadata",
            _freeze_json_object(self.metadata, "metadata"),
        )
        if not isinstance(self.entities, list | tuple):
            raise ModelValidationError("entities must be a list or tuple")
        object.__setattr__(self, "entities", tuple(self.entities))

        v1_values = (
            self.evidence_type,
            self.source_agent,
            self.source_tool,
            self.observation,
            self.confidence,
            self.evidence_schema_version,
        )
        has_v1_value = any(value is not None for value in v1_values) or bool(self.entities)
        if not has_v1_value:
            return
        if (
            self.evidence_type is None
            or self.source_agent is None
            or self.source_tool is None
            or self.observation is None
            or self.confidence is None
            or self.evidence_schema_version is None
        ):
            raise ModelValidationError("typed Evidence requires all V1 fields")
        if not self.entities:
            raise ModelValidationError("typed Evidence requires at least one entity")

        _validate_enum(self.evidence_type, EvidenceType, "evidence_type")
        _validate_enum(self.source_agent, AgentKind, "source_agent")
        _validate_non_empty(self.source_tool, "source_tool")
        _validate_non_empty(self.observation, "observation")
        _validate_confidence(self.confidence)
        if self.evidence_schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ModelValidationError(
                "unsupported evidence_schema_version "
                f"{self.evidence_schema_version!r}; expected {EVIDENCE_SCHEMA_VERSION!r}"
            )
        for entity in self.entities:
            if not isinstance(entity, EvidenceEntity):
                raise ModelValidationError("entities must contain EvidenceEntity instances")

    @property
    def is_typed(self) -> bool:
        return self.evidence_schema_version is not None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "summary": self.summary,
            "source_table": self.source_table,
            "source_field": self.source_field,
            "timestamp": self.timestamp,
            "metadata": _thaw_json_object(self.metadata),
            "schema_version": self.schema_version,
        }
        if self.is_typed:
            if self.confidence is None:
                raise ModelValidationError("typed Evidence requires confidence")
            data.update(
                {
                    "evidence_type": self.evidence_type,
                    "source_agent": self.source_agent,
                    "source_tool": self.source_tool,
                    "observation": self.observation,
                    "entities": [entity.to_dict() for entity in self.entities],
                    "confidence": float(self.confidence),
                    "evidence_schema_version": self.evidence_schema_version,
                }
            )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            evidence_id=data["evidence_id"],
            source_type=data["source_type"],
            source_id=data["source_id"],
            summary=data["summary"],
            source_table=data.get("source_table"),
            source_field=data.get("source_field"),
            timestamp=data.get("timestamp"),
            metadata=dict(data.get("metadata", {})),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            evidence_type=data.get("evidence_type"),
            source_agent=data.get("source_agent"),
            source_tool=data.get("source_tool"),
            observation=data.get("observation"),
            entities=[EvidenceEntity.from_dict(item) for item in data.get("entities", [])],
            confidence=(float(data["confidence"]) if data.get("confidence") is not None else None),
            evidence_schema_version=data.get("evidence_schema_version"),
        )
