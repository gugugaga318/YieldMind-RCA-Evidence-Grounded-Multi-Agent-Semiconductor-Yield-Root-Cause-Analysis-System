"""Python-owned observation and causal-search scope contracts.

An observation identifies where a signal was detected.  It is deliberately
separate from the bounded directions in which an investigation may search for
a cause.  This module contains no LLM decisions and performs no retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.models import ModelValidationError
from yield_rca_core.repositories import FabRepository


class CausalLane(StrEnum):
    SAME_STEP = "same_step"
    UPSTREAM_ROUTE = "upstream_route"
    SHARED_RESOURCE = "shared_resource"
    GLOBAL_SEMANTIC = "global_semantic"


class CausalScopeMode(StrEnum):
    LEGACY_HARD = "legacy_hard"
    CAUSAL_WIDE = "causal_wide"
    EXPLICIT_HARD = "explicit_hard"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def _validated_timestamp(value: str, name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelValidationError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelValidationError(f"{name} must include a timezone")
    return parsed


def explicit_module_limit_requested(user_query: str, module: str) -> bool:
    """Recognize a narrow, user-authored Module restriction.

    This validator is intentionally conservative. An observed Module mention alone
    never becomes a hard constraint, and Qwen cannot override the result.
    """

    normalized_query = " ".join(
        user_query.casefold().replace("_", " ").replace("-", " ").split()
    )
    normalized_module = " ".join(
        module.casefold().replace("_", " ").replace("-", " ").split()
    )
    if not normalized_query or not normalized_module or normalized_module not in normalized_query:
        return False
    escaped = re.escape(normalized_module)
    patterns = (
        rf"\bonly\b.{{0,40}}\b{escaped}\b",
        rf"\brestrict(?:ed)?\b.{{0,40}}\b(?:to|within)\b.{{0,20}}\b{escaped}\b",
        rf"\b{escaped}\b.{{0,30}}\bonly\b",
        rf"(?:仅|只)(?:调查|检查|检索|搜索|看)?[^。；，,]{{0,20}}{escaped}",
        rf"{escaped}[^。；，,]{{0,20}}(?:范围内|以内|之外不要|之外不)",
    )
    return any(re.search(pattern, normalized_query) for pattern in patterns)


@dataclass(frozen=True)
class ScopeFilters:
    """Typed metadata fields used as either hard constraints or soft hints."""

    module: str = ""
    equipment_type: str = ""
    operation: str = ""
    defect_type: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tags, tuple):
            raise ModelValidationError("ScopeFilters.tags must be a tuple")
        if any(not item.strip() for item in self.tags):
            raise ModelValidationError("ScopeFilters.tags must not contain blanks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "equipment_type": self.equipment_type,
            "operation": self.operation,
            "defect_type": self.defect_type,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            module=_clean(data.get("module")),
            equipment_type=_clean(data.get("equipment_type")),
            operation=_clean(data.get("operation")),
            defect_type=_clean(data.get("defect_type")),
            tags=_unique([str(item) for item in data.get("tags", [])]),
        )


@dataclass(frozen=True)
class ObservationScope:
    """Known detection facts; none of these fields is a causal attribution."""

    source_lot_id: str = ""
    product_id: str = ""
    detected_module: str = ""
    detected_operation: str = ""
    detected_equipment_id: str = ""
    detected_equipment_type: str = ""
    detected_at: str = ""
    symptom_types: tuple[str, ...] = ()
    known_measurements: tuple[str, ...] = ()
    known_defect_attributes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "symptom_types",
            "known_measurements",
            "known_defect_attributes",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not item.strip() for item in values):
                raise ModelValidationError(f"ObservationScope.{name} must contain strings")
        _validated_timestamp(self.detected_at, "ObservationScope.detected_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_lot_id": self.source_lot_id,
            "product_id": self.product_id,
            "detected_module": self.detected_module,
            "detected_operation": self.detected_operation,
            "detected_equipment_id": self.detected_equipment_id,
            "detected_equipment_type": self.detected_equipment_type,
            "detected_at": self.detected_at,
            "symptom_types": list(self.symptom_types),
            "known_measurements": list(self.known_measurements),
            "known_defect_attributes": list(self.known_defect_attributes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            source_lot_id=_clean(data.get("source_lot_id")),
            product_id=_clean(data.get("product_id")),
            detected_module=_clean(data.get("detected_module")),
            detected_operation=_clean(data.get("detected_operation")),
            detected_equipment_id=_clean(data.get("detected_equipment_id")),
            detected_equipment_type=_clean(data.get("detected_equipment_type")),
            detected_at=_clean(data.get("detected_at")),
            symptom_types=_unique([str(item) for item in data.get("symptom_types", [])]),
            known_measurements=_unique(
                [str(item) for item in data.get("known_measurements", [])]
            ),
            known_defect_attributes=_unique(
                [str(item) for item in data.get("known_defect_attributes", [])]
            ),
        )


@dataclass(frozen=True)
class CausalLaneContext:
    lane: str
    available: bool
    reason: str
    modules: tuple[str, ...] = ()
    equipment_types: tuple[str, ...] = ()
    route_distance: int | None = None
    shared_resource_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            CausalLane(self.lane)
        except ValueError as exc:
            raise ModelValidationError(f"unknown causal lane: {self.lane}") from exc
        if not isinstance(self.available, bool):
            raise ModelValidationError("CausalLaneContext.available must be a boolean")
        if not self.reason.strip():
            raise ModelValidationError("CausalLaneContext.reason must not be blank")
        if self.route_distance is not None and self.route_distance < 0:
            raise ModelValidationError("route_distance must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "available": self.available,
            "reason": self.reason,
            "modules": list(self.modules),
            "equipment_types": list(self.equipment_types),
            "route_distance": self.route_distance,
            "shared_resource_types": list(self.shared_resource_types),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        distance = data.get("route_distance")
        available = data.get("available")
        if not isinstance(available, bool):
            raise ModelValidationError("CausalLaneContext.available must be a boolean")
        return cls(
            lane=_clean(data.get("lane")),
            available=available,
            reason=_clean(data.get("reason")),
            modules=_unique([str(item) for item in data.get("modules", [])]),
            equipment_types=_unique(
                [str(item) for item in data.get("equipment_types", [])]
            ),
            route_distance=(int(distance) if distance is not None else None),
            shared_resource_types=_unique(
                [str(item) for item in data.get("shared_resource_types", [])]
            ),
        )


@dataclass(frozen=True)
class CausalSearchScope:
    mode: str
    hard_constraints: ScopeFilters
    soft_hints: ScopeFilters
    expansion_lanes: tuple[CausalLaneContext, ...]
    scope_reason: str
    explicit_user_limits: tuple[str, ...] = ()
    time_boundary: str = ""
    candidate_budget: int = 20
    lane_minimum: int = 1

    def __post_init__(self) -> None:
        try:
            CausalScopeMode(self.mode)
        except ValueError as exc:
            raise ModelValidationError(f"unknown causal scope mode: {self.mode}") from exc
        if not self.scope_reason.strip():
            raise ModelValidationError("CausalSearchScope.scope_reason must not be blank")
        if not 4 <= self.candidate_budget <= 80:
            raise ModelValidationError("candidate_budget must be between 4 and 80")
        if not 1 <= self.lane_minimum <= 5:
            raise ModelValidationError("lane_minimum must be between 1 and 5")
        _validated_timestamp(self.time_boundary, "CausalSearchScope.time_boundary")
        lanes = [item.lane for item in self.expansion_lanes]
        if len(lanes) != len(set(lanes)):
            raise ModelValidationError("causal expansion lanes must be unique")
        if self.mode == CausalScopeMode.CAUSAL_WIDE.value:
            required = {item.value for item in CausalLane}
            if set(lanes) != required:
                raise ModelValidationError("causal_wide scope requires all four lane records")

    @property
    def available_lanes(self) -> tuple[str, ...]:
        return tuple(item.lane for item in self.expansion_lanes if item.available)

    def lane(self, value: str) -> CausalLaneContext | None:
        return next((item for item in self.expansion_lanes if item.lane == value), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "hard_constraints": self.hard_constraints.to_dict(),
            "soft_hints": self.soft_hints.to_dict(),
            "expansion_lanes": [item.to_dict() for item in self.expansion_lanes],
            "available_lanes": list(self.available_lanes),
            "explicit_user_limits": list(self.explicit_user_limits),
            "time_boundary": self.time_boundary,
            "candidate_budget": self.candidate_budget,
            "lane_minimum": self.lane_minimum,
            "scope_reason": self.scope_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            mode=_clean(data.get("mode")),
            hard_constraints=ScopeFilters.from_dict(
                dict(data.get("hard_constraints", {}))
            ),
            soft_hints=ScopeFilters.from_dict(dict(data.get("soft_hints", {}))),
            expansion_lanes=tuple(
                CausalLaneContext.from_dict(dict(item))
                for item in data.get("expansion_lanes", [])
            ),
            explicit_user_limits=_unique(
                [str(item) for item in data.get("explicit_user_limits", [])]
            ),
            time_boundary=_clean(data.get("time_boundary")),
            candidate_budget=int(data.get("candidate_budget", 20)),
            lane_minimum=int(data.get("lane_minimum", 1)),
            scope_reason=_clean(data.get("scope_reason")),
        )


@dataclass(frozen=True)
class CausalScopePolicy:
    name: str
    observed_module_is_soft: bool
    module_can_be_explicit_hard: bool
    default_lanes: tuple[str, ...] = field(
        default_factory=lambda: tuple(item.value for item in CausalLane)
    )


CAUSAL_SCOPE_POLICY_REGISTRY: dict[str, CausalScopePolicy] = {
    "root_cause": CausalScopePolicy("root_cause", True, True),
    "full_rca": CausalScopePolicy("full_rca", True, True),
    "impact_scope": CausalScopePolicy("impact_scope", True, True),
    "historical_match": CausalScopePolicy("historical_match", True, True),
    "engineering_note_lookup": CausalScopePolicy(
        "engineering_note_lookup", True, True
    ),
    "procedure_guidance": CausalScopePolicy(
        "procedure_guidance", False, True
    ),
}


class RepositoryCausalContextProvider:
    """Resolve route and configured shared-resource context from Fab tables."""

    def __init__(self, repository: FabRepository) -> None:
        self.repository = repository

    def _rows(self, table_name: str) -> list[dict[str, str]]:
        try:
            return [dict(row) for row in self.repository.rows(table_name)]
        except FileNotFoundError:
            return []

    def lane_contexts(
        self,
        observation: ObservationScope,
    ) -> tuple[CausalLaneContext, ...]:
        same_step = CausalLaneContext(
            lane=CausalLane.SAME_STEP.value,
            available=bool(
                observation.detected_module
                or observation.detected_operation
                or observation.detected_equipment_type
            ),
            reason=(
                "Use detected-step metadata as a bounded ranking lane, not a causal claim."
                if observation.detected_module
                or observation.detected_operation
                or observation.detected_equipment_type
                else "No detected-step metadata is available."
            ),
            modules=((observation.detected_module,) if observation.detected_module else ()),
            equipment_types=(
                (observation.detected_equipment_type,)
                if observation.detected_equipment_type
                else ()
            ),
            route_distance=0,
        )
        upstream = self._upstream_context(observation)
        shared = self._shared_context(observation)
        global_lane = CausalLaneContext(
            lane=CausalLane.GLOBAL_SEMANTIC.value,
            available=True,
            reason=(
                "Search all approved documents of the requested type so an observed "
                "Module cannot suppress a cross-Module cause."
            ),
        )
        return same_step, upstream, shared, global_lane

    def _lot_and_route(
        self,
        observation: ObservationScope,
    ) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
        if not observation.source_lot_id:
            return None, []
        lot = next(
            (
                row
                for row in self._rows("lot_master")
                if row.get("lot_id") == observation.source_lot_id
            ),
            None,
        )
        if lot is None:
            return None, []
        route_id = lot.get("route_id", "")
        rows = [
            row
            for row in self._rows("process_route")
            if row.get("route_id") == route_id
        ]
        rows.sort(key=lambda row: int(row.get("sequence_no", "0") or 0))
        return lot, rows

    def _upstream_context(self, observation: ObservationScope) -> CausalLaneContext:
        lot, route = self._lot_and_route(observation)
        if lot is None or not route:
            return CausalLaneContext(
                lane=CausalLane.UPSTREAM_ROUTE.value,
                available=False,
                reason="Source Lot route data is unavailable for upstream expansion.",
            )
        detected_index: int | None = None
        for index, row in enumerate(route):
            if observation.detected_operation and row.get("operation_no") == (
                observation.detected_operation
            ):
                detected_index = index
                break
        if detected_index is None and observation.detected_module:
            matches = [
                index
                for index, row in enumerate(route)
                if row.get("module", "").casefold()
                == observation.detected_module.casefold()
            ]
            detected_index = matches[-1] if matches else None
        if detected_index is None:
            return CausalLaneContext(
                lane=CausalLane.UPSTREAM_ROUTE.value,
                available=False,
                reason="Detected operation cannot be located on the source Lot route.",
            )
        upstream_rows = list(reversed(route[:detected_index]))
        modules = _unique([row.get("module", "") for row in upstream_rows])
        return CausalLaneContext(
            lane=CausalLane.UPSTREAM_ROUTE.value,
            available=bool(modules),
            reason=(
                "Use earlier operations on the protected source Lot route."
                if modules
                else "The detected operation has no earlier route operations."
            ),
            modules=modules,
            route_distance=1 if modules else None,
        )

    def _shared_context(self, observation: ObservationScope) -> CausalLaneContext:
        if not observation.source_lot_id:
            return CausalLaneContext(
                lane=CausalLane.SHARED_RESOURCE.value,
                available=False,
                reason="Source Lot history is unavailable for shared-resource expansion.",
            )
        histories = [
            row
            for row in self._rows("process_history")
            if row.get("lot_id") == observation.source_lot_id
            and (
                not observation.detected_operation
                or row.get("operation_no") == observation.detected_operation
            )
        ]
        if not histories:
            return CausalLaneContext(
                lane=CausalLane.SHARED_RESOURCE.value,
                available=False,
                reason="No configured equipment, chamber, or recipe exposure was found.",
            )
        equipment_rows = {
            row.get("equipment_id", ""): row
            for row in self._rows("equipment_master")
        }
        equipment_types = _unique(
            [
                equipment_rows.get(row.get("equipment_id", ""), {}).get(
                    "equipment_type", ""
                )
                for row in histories
            ]
        )
        resource_types: list[str] = []
        if any(row.get("equipment_id") for row in histories):
            resource_types.append("equipment")
        if any(row.get("chamber_id") for row in histories):
            resource_types.append("chamber")
        if any(row.get("recipe_id") for row in histories):
            resource_types.append("recipe")
        return CausalLaneContext(
            lane=CausalLane.SHARED_RESOURCE.value,
            available=bool(equipment_types),
            reason=(
                "Use configured equipment, chamber, and recipe exposure relationships."
                if equipment_types
                else "Exposure rows exist but no equipment type can be resolved."
            ),
            modules=_unique([row.get("module", "") for row in histories]),
            equipment_types=equipment_types,
            shared_resource_types=tuple(resource_types),
        )


def _default_lane_contexts(observation: ObservationScope) -> tuple[CausalLaneContext, ...]:
    same_available = bool(
        observation.detected_module
        or observation.detected_operation
        or observation.detected_equipment_type
    )
    return (
        CausalLaneContext(
            lane=CausalLane.SAME_STEP.value,
            available=same_available,
            reason=(
                "Use detected-step metadata as a soft same-step lane."
                if same_available
                else "No detected-step metadata is available."
            ),
            modules=((observation.detected_module,) if observation.detected_module else ()),
            equipment_types=(
                (observation.detected_equipment_type,)
                if observation.detected_equipment_type
                else ()
            ),
            route_distance=0,
        ),
        CausalLaneContext(
            lane=CausalLane.UPSTREAM_ROUTE.value,
            available=False,
            reason="Fab route context is unavailable because no provider is configured.",
        ),
        CausalLaneContext(
            lane=CausalLane.SHARED_RESOURCE.value,
            available=False,
            reason=(
                "Fab shared-resource context is unavailable because no provider is "
                "configured."
            ),
        ),
        CausalLaneContext(
            lane=CausalLane.GLOBAL_SEMANTIC.value,
            available=True,
            reason="Search all approved documents of the requested type.",
        ),
    )


def build_causal_search_scope(
    *,
    question_kind: str,
    observation: ObservationScope,
    explicit_module_limit: bool = False,
    context_provider: RepositoryCausalContextProvider | None = None,
    candidate_budget: int = 20,
    lane_minimum: int = 1,
    time_boundary: str = "",
) -> CausalSearchScope:
    policy = CAUSAL_SCOPE_POLICY_REGISTRY.get(question_kind)
    if policy is None:
        raise ModelValidationError(f"no causal Scope policy for {question_kind}")
    procedure_hard = question_kind == "procedure_guidance" and bool(
        observation.detected_module or observation.detected_operation
    )
    hard_module = bool(explicit_module_limit or procedure_hard)
    hard = ScopeFilters(
        module=observation.detected_module if hard_module else "",
        operation=observation.detected_operation if procedure_hard else "",
    )
    soft = ScopeFilters(
        module=observation.detected_module,
        equipment_type=observation.detected_equipment_type,
        operation=observation.detected_operation,
        defect_type=(
            observation.known_defect_attributes[0]
            if observation.known_defect_attributes
            else ""
        ),
        tags=observation.symptom_types,
    )
    contexts = (
        context_provider.lane_contexts(observation)
        if context_provider is not None
        else _default_lane_contexts(observation)
    )
    limits = (
        (f"module={observation.detected_module}",)
        if explicit_module_limit and observation.detected_module
        else ()
    )
    return CausalSearchScope(
        mode=(
            CausalScopeMode.EXPLICIT_HARD.value
            if hard_module
            else CausalScopeMode.CAUSAL_WIDE.value
        ),
        hard_constraints=hard,
        soft_hints=soft,
        expansion_lanes=contexts,
        explicit_user_limits=limits,
        time_boundary=time_boundary or observation.detected_at,
        candidate_budget=candidate_budget,
        lane_minimum=lane_minimum,
        scope_reason=(
            "The user explicitly restricted Module scope."
            if explicit_module_limit
            else (
                "Procedure guidance uses the explicitly requested operation scope."
                if procedure_hard
                else "Observed metadata is a soft hint; bounded cross-Module lanes remain visible."
            )
        ),
    )
