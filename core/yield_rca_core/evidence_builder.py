"""Factories and compatibility adapters for typed Evidence."""

from __future__ import annotations

from typing import Any

from yield_rca_core.evidence_models import (
    EVIDENCE_SCHEMA_VERSION,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    ModelValidationError,
)
from yield_rca_core.models import ToolInput


def _enum_string(value: EvidenceType | EvidenceSourceType | str) -> str:
    return value.value if isinstance(value, EvidenceType | EvidenceSourceType) else value


class EvidenceBuilder:
    """Build complete typed Evidence from a validated Tool request."""

    @classmethod
    def from_tool(
        cls,
        *,
        tool_input: ToolInput,
        evidence_id: str,
        evidence_type: EvidenceType | str,
        source_type: EvidenceSourceType | str,
        observation: str,
        entities: list[EvidenceEntity],
        confidence: float,
        source_id: str,
        source_table: str | None = None,
        source_field: str | None = None,
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
        summary: str | None = None,
    ) -> Evidence:
        if not isinstance(tool_input, ToolInput):
            raise ModelValidationError("tool_input must be a ToolInput instance")
        return Evidence(
            evidence_id=evidence_id,
            source_type=_enum_string(source_type),
            source_id=source_id,
            summary=summary if summary is not None else observation,
            source_table=source_table,
            source_field=source_field,
            timestamp=timestamp,
            metadata=dict(metadata or {}),
            evidence_type=_enum_string(evidence_type),
            source_agent=tool_input.requested_by,
            source_tool=tool_input.tool_name,
            observation=observation,
            entities=list(entities),
            confidence=confidence,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        )


class LegacyEvidenceAdapter:
    """Upgrade one legacy Evidence record using explicit industrial semantics."""

    @classmethod
    def to_typed(
        cls,
        evidence: Evidence,
        *,
        evidence_type: EvidenceType | str,
        source_agent: str,
        source_tool: str,
        entities: list[EvidenceEntity],
        confidence: float,
        observation: str | None = None,
    ) -> Evidence:
        if not isinstance(evidence, Evidence):
            raise ModelValidationError("evidence must be an Evidence instance")
        if evidence.is_typed:
            raise ModelValidationError("LegacyEvidenceAdapter requires legacy Evidence")
        return Evidence(
            evidence_id=evidence.evidence_id,
            source_type=evidence.source_type,
            source_id=evidence.source_id,
            summary=evidence.summary,
            source_table=evidence.source_table,
            source_field=evidence.source_field,
            timestamp=evidence.timestamp,
            metadata=dict(evidence.metadata),
            schema_version=evidence.schema_version,
            evidence_type=_enum_string(evidence_type),
            source_agent=source_agent,
            source_tool=source_tool,
            observation=observation if observation is not None else evidence.summary,
            entities=list(entities),
            confidence=confidence,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        )
