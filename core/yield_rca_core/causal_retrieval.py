"""Bounded four-lane Knowledge candidate generation and Scope fusion."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from yield_rca_core.causal_scope import (
    CausalLane,
    CausalLaneContext,
    CausalScopeMode,
    ObservationScope,
    RepositoryCausalContextProvider,
    build_causal_search_scope,
)
from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever
from yield_rca_core.knowledge_models import KnowledgeLookupHit, KnowledgeLookupPlan


def _asset_id(hit: KnowledgeLookupHit) -> str:
    return str(hit.document.evaluation_asset_id)


class CausalLaneKnowledgeRetriever:
    """Wrap one governed Retriever with lane diversity and cross-Module recall.

    The delegate continues to own lexical/vector/RRF ranking.  This wrapper
    changes only candidate Scope and fuses the bounded lane rankings.
    """

    def __init__(
        self,
        delegate: KnowledgeLookupRetriever,
        *,
        candidate_budget: int = 20,
        lane_minimum: int = 1,
        rrf_k: int = 60,
    ) -> None:
        if not 4 <= candidate_budget <= 80:
            raise ValueError("causal candidate_budget must be between 4 and 80")
        if not 1 <= lane_minimum <= 5:
            raise ValueError("causal lane_minimum must be between 1 and 5")
        if rrf_k < 1:
            raise ValueError("causal rrf_k must be positive")
        self.delegate = delegate
        self.candidate_budget = candidate_budget
        self.lane_minimum = lane_minimum
        self.rrf_k = rrf_k

    def prepare_plan(
        self,
        plan: KnowledgeLookupPlan,
        *,
        context_provider: RepositoryCausalContextProvider | None = None,
    ) -> KnowledgeLookupPlan:
        observation = plan.observation_scope or ObservationScope(
            detected_module=plan.module,
            detected_operation=plan.operation,
            detected_equipment_type=plan.equipment_type,
            symptom_types=plan.tags,
            known_defect_attributes=((plan.defect_type,) if plan.defect_type else ()),
        )
        scope = build_causal_search_scope(
            question_kind=plan.question_kind,
            observation=observation,
            explicit_module_limit=plan.explicit_module_limit,
            context_provider=context_provider,
            candidate_budget=self.candidate_budget,
            lane_minimum=self.lane_minimum,
        )
        return replace(
            plan,
            observation_scope=observation,
            causal_search_scope=scope,
        )

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        prepared = plan if plan.causal_search_scope is not None else self.prepare_plan(plan)
        scope = prepared.causal_search_scope
        if scope is None or scope.mode == CausalScopeMode.LEGACY_HARD.value:
            return self.delegate.retrieve(prepared, lookup_id=lookup_id)

        lane_results: dict[str, tuple[KnowledgeLookupHit, ...]] = {}
        lane_contexts = {
            item.lane: item for item in scope.expansion_lanes if item.available
        }
        for lane, context in lane_contexts.items():
            lane_hits: dict[str, KnowledgeLookupHit] = {}
            for lane_plan in self._lane_plans(prepared, context):
                for hit in self.delegate.retrieve(
                    lane_plan,
                    lookup_id=f"{lookup_id}_{lane.upper()}",
                ):
                    current = lane_hits.get(_asset_id(hit))
                    if current is None or hit.score > current.score:
                        lane_hits[_asset_id(hit)] = hit
            lane_results[lane] = tuple(
                sorted(
                    lane_hits.values(),
                    key=lambda item: (-item.score, _asset_id(item)),
                )[: scope.candidate_budget]
            )

        return self._fuse_lanes(
            prepared,
            lookup_id=lookup_id,
            lane_results=lane_results,
            lane_contexts=lane_contexts,
        )

    def _lane_plans(
        self,
        plan: KnowledgeLookupPlan,
        context: CausalLaneContext,
    ) -> tuple[KnowledgeLookupPlan, ...]:
        scope = plan.causal_search_scope
        assert scope is not None
        hard = scope.hard_constraints
        lane_top_k = min(20, scope.candidate_budget)
        candidates: list[KnowledgeLookupPlan] = []

        def add(*, module: str = "", equipment_type: str = "") -> None:
            candidates.append(
                replace(
                    plan,
                    module=hard.module or module,
                    equipment_type=hard.equipment_type or equipment_type,
                    operation=hard.operation,
                    defect_type=hard.defect_type,
                    tags=hard.tags,
                    top_k=lane_top_k,
                )
            )

        if context.lane == CausalLane.SAME_STEP.value:
            if context.modules or context.equipment_types:
                add(
                    module=(context.modules[0] if context.modules else ""),
                    equipment_type=(
                        context.equipment_types[0] if context.equipment_types else ""
                    ),
                )
        elif context.lane == CausalLane.UPSTREAM_ROUTE.value:
            for module in context.modules:
                add(module=module)
        elif context.lane == CausalLane.SHARED_RESOURCE.value:
            for equipment_type in context.equipment_types:
                add(equipment_type=equipment_type)
            if not context.equipment_types:
                for module in context.modules:
                    add(module=module)
        else:
            add()
        unique: dict[tuple[str, str, str, str, tuple[str, ...]], KnowledgeLookupPlan] = {}
        for item in candidates:
            key = (
                item.module.casefold(),
                item.equipment_type.casefold(),
                item.operation.casefold(),
                item.defect_type.casefold(),
                tuple(tag.casefold() for tag in item.tags),
            )
            unique.setdefault(key, item)
        return tuple(unique.values())

    def _fuse_lanes(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
        lane_results: dict[str, tuple[KnowledgeLookupHit, ...]],
        lane_contexts: dict[str, CausalLaneContext],
    ) -> tuple[KnowledgeLookupHit, ...]:
        contributions: defaultdict[str, float] = defaultdict(float)
        candidates: dict[str, list[KnowledgeLookupHit]] = defaultdict(list)
        candidate_lanes: defaultdict[str, list[str]] = defaultdict(list)
        for lane, hits in lane_results.items():
            for rank, hit in enumerate(hits, start=1):
                asset_id = _asset_id(hit)
                contributions[asset_id] += 1.0 / (self.rrf_k + rank)
                candidates[asset_id].append(hit)
                candidate_lanes[asset_id].append(lane)
        if not contributions:
            return ()
        scope = plan.causal_search_scope
        assert scope is not None

        selected: list[str] = []
        for lane in (item.value for item in CausalLane):
            for hit in lane_results.get(lane, ()):
                asset_id = _asset_id(hit)
                if asset_id not in selected:
                    selected.append(asset_id)
                if sum(
                    1 for value in selected if lane in candidate_lanes[value]
                ) >= scope.lane_minimum:
                    break
                if len(selected) >= plan.top_k:
                    break
            if len(selected) >= plan.top_k:
                break
        for asset_id in sorted(
            contributions,
            key=lambda value: (-contributions[value], value),
        ):
            if asset_id not in selected:
                selected.append(asset_id)
            if len(selected) >= plan.top_k:
                break

        maximum = max(contributions.values())
        ranked_ids = sorted(
            selected[: plan.top_k],
            key=lambda value: (-contributions[value], value),
        )
        output: list[KnowledgeLookupHit] = []
        for rank, asset_id in enumerate(ranked_ids, start=1):
            variants = candidates[asset_id]
            best = max(variants, key=lambda item: item.score)
            lanes = tuple(
                lane for lane in (item.value for item in CausalLane)
                if lane in candidate_lanes[asset_id]
            )
            contexts = [lane_contexts[lane] for lane in lanes]
            scope_score = contributions[asset_id] / maximum
            components: dict[str, float] = {}
            for variant in variants:
                for name, value in variant.score_components.items():
                    components[name] = max(value, components.get(name, 0.0))
            route_distances = [
                item.route_distance for item in contexts if item.route_distance is not None
            ]
            shared_types = tuple(
                dict.fromkeys(
                    value for item in contexts for value in item.shared_resource_types
                )
            )
            strategies = tuple(dict.fromkeys(item.retrieval_strategy for item in variants))
            output.append(
                replace(
                    best,
                    rank=rank,
                    score=round(scope_score, 6),
                    evidence_id=f"KEV_{lookup_id.removeprefix('KLOOK_')}_{rank:03d}",
                    relevance_reason=(
                        best.relevance_reason
                        + "; Python causal Scope lanes: "
                        + ", ".join(lanes)
                    ),
                    retrieval_strategy=(
                        "causal_lane_rrf+" + "+".join(strategies)
                    ),
                    score_components=components,
                    candidate_lanes=lanes,
                    scope_reasons=tuple(item.reason for item in contexts),
                    route_distance=(min(route_distances) if route_distances else None),
                    shared_resource_types=shared_types,
                    scope_fusion_score=round(scope_score, 6),
                )
            )
        return tuple(output)


def prepare_causal_plan(
    retriever: KnowledgeLookupRetriever,
    plan: KnowledgeLookupPlan,
    *,
    context_provider: RepositoryCausalContextProvider | None = None,
) -> KnowledgeLookupPlan:
    """Find an optional causal wrapper through the Reranker decorator."""

    current: object = retriever
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CausalLaneKnowledgeRetriever):
            return current.prepare_plan(plan, context_provider=context_provider)
        nested = getattr(current, "base_retriever", None)
        if nested is None:
            break
        current = nested
    return plan
