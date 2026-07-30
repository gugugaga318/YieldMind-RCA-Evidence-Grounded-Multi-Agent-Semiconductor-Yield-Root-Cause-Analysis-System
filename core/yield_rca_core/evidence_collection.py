"""Deterministic collection and reference validation for Evidence."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Self

from yield_rca_core.evidence_models import (
    EntityType,
    Evidence,
    EvidenceType,
    ModelValidationError,
)


def _enum_string(value: EvidenceType | EntityType | str) -> str:
    return value.value if isinstance(value, EvidenceType | EntityType) else value


class EvidenceCollection:
    """An insertion-ordered Evidence index with conflict detection."""

    def __init__(self, evidence: Iterable[Evidence] | None = None) -> None:
        self._by_id: dict[str, Evidence] = {}
        if evidence is not None:
            self.merge(evidence)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self._by_id.values())

    def add(self, evidence: Evidence) -> Self:
        if not isinstance(evidence, Evidence):
            raise ModelValidationError("EvidenceCollection accepts only Evidence instances")
        existing = self._by_id.get(evidence.evidence_id)
        if existing is not None and existing.to_dict() != evidence.to_dict():
            raise ModelValidationError(
                f"conflicting payload for evidence_id {evidence.evidence_id!r}"
            )
        if existing is None:
            self._by_id[evidence.evidence_id] = evidence
        return self

    def merge(self, evidence: Iterable[Evidence] | EvidenceCollection) -> Self:
        incoming = (
            evidence.to_list() if isinstance(evidence, EvidenceCollection) else list(evidence)
        )
        staged = EvidenceCollection(self.to_list()) if self._by_id else EvidenceCollection()
        for item in incoming:
            staged.add(item)
        self._by_id = dict(staged._by_id)
        return self

    def get(self, evidence_id: str) -> Evidence | None:
        return self._by_id.get(evidence_id)

    def require(self, evidence_ids: Iterable[str]) -> list[Evidence]:
        requested = list(evidence_ids)
        missing = [evidence_id for evidence_id in requested if evidence_id not in self._by_id]
        if missing:
            raise ModelValidationError(f"unknown evidence_ids: {sorted(set(missing))}")
        return [self._by_id[evidence_id] for evidence_id in requested]

    def by_type(self, evidence_type: EvidenceType | str) -> list[Evidence]:
        wanted = _enum_string(evidence_type)
        return [item for item in self._by_id.values() if item.evidence_type == wanted]

    def by_entity(
        self,
        entity_type: EntityType | str,
        entity_id: str | None = None,
    ) -> list[Evidence]:
        wanted_type = _enum_string(entity_type)
        return [
            item
            for item in self._by_id.values()
            if any(
                entity.entity_type == wanted_type
                and (entity_id is None or entity.entity_id == entity_id)
                for entity in item.entities
            )
        ]

    def to_list(self) -> list[Evidence]:
        return list(self._by_id.values())
