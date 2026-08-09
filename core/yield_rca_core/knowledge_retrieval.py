"""Knowledge Asset models and retriever contracts.

This module keeps knowledge retrieval separate from the Tool Layer. Tools
package retriever results into ToolOutput and Evidence, while retrievers own
asset normalization and ranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from yield_rca_core.causal_retrieval import prepare_causal_plan
from yield_rca_core.causal_scope import (
    CausalSearchScope,
    ObservationScope,
    RepositoryCausalContextProvider,
)
from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever
from yield_rca_core.knowledge_models import (
    KnowledgeDocumentType,
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.repositories import FabRepository, Row


def _float(value: str) -> float:
    return float(value)


def _is_confirmed(value: str | None) -> bool:
    return (value or "CONFIRMED").upper() == "CONFIRMED"


@dataclass(frozen=True)
class KnowledgeAssetDocument:
    """Engineer-governed supporting document attached to a KnowledgeAsset."""

    document_id: str
    case_id: str
    document_type: str
    title: str
    content: str
    tags: str
    created_at: str
    validation_status: str = "CONFIRMED"
    row: Row = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Row) -> KnowledgeAssetDocument:
        return cls(
            document_id=row["document_id"],
            case_id=row["case_id"],
            document_type=row["document_type"],
            title=row["title"],
            content=row["content"],
            tags=row.get("tags", ""),
            created_at=row["created_at"],
            validation_status=row.get("validation_status", "CONFIRMED"),
            row=dict(row),
        )

    def to_legacy_row(self) -> Row:
        return dict(self.row)


@dataclass(frozen=True)
class KnowledgeAsset:
    """Unified representation of an approved historical RCA knowledge item."""

    asset_id: str
    title: str
    module: str
    equipment_type: str
    symptom: str
    root_cause: str
    solution: str
    confidence: float
    created_at: str
    validation_status: str = "CONFIRMED"
    documents: tuple[KnowledgeAssetDocument, ...] = ()
    row: Row = field(default_factory=dict)

    @classmethod
    def from_case_row(
        cls,
        row: Row,
        *,
        documents: list[KnowledgeAssetDocument] | None = None,
    ) -> KnowledgeAsset:
        return cls(
            asset_id=row["case_id"],
            title=row["title"],
            module=row["module"],
            equipment_type=row["equipment_type"],
            symptom=row["symptom"],
            root_cause=row["root_cause"],
            solution=row["solution"],
            confidence=_float(row["confidence"]),
            created_at=row["created_at"],
            validation_status=row.get("validation_status", "CONFIRMED"),
            documents=tuple(documents or []),
            row=dict(row),
        )

    def to_legacy_case(self, *, similarity: float) -> dict[str, Any]:
        return {**self.row, "similarity": round(similarity, 3)}


@dataclass(frozen=True)
class RetrievalQuery:
    """Normalized retriever input."""

    query: str
    module: str = ""
    equipment_type: str = ""
    source_lot_id: str = ""
    product_id: str = ""
    detected_operation: str = ""
    detected_equipment_id: str = ""
    detected_at: str = ""
    symptom_types: tuple[str, ...] = ()
    explicit_module_limit: bool = False
    top_k: int = 10


@dataclass(frozen=True)
class RetrievalHit:
    """One ranked knowledge asset result."""

    asset: KnowledgeAsset
    score: float
    retrieval_strategy: str = "keyword"
    score_components: dict[str, float] = field(default_factory=dict)
    calibrated_relevance: float | None = None
    source_confidence: float | None = None
    matched_chunk_ids: tuple[str, ...] = ()
    candidate_lanes: tuple[str, ...] = ()
    scope_reasons: tuple[str, ...] = ()
    route_distance: int | None = None
    shared_resource_types: tuple[str, ...] = ()
    scope_fusion_score: float | None = None

    def to_legacy_case(self) -> dict[str, Any]:
        return self.asset.to_legacy_case(similarity=self.score)


@dataclass(frozen=True)
class RetrievalResult:
    """Retriever response independent of ToolOutput/Evidence packaging."""

    query: RetrievalQuery
    hits: list[RetrievalHit]
    observation_scope: ObservationScope | None = None
    causal_search_scope: CausalSearchScope | None = None

    @property
    def top_hit(self) -> RetrievalHit | None:
        return self.hits[0] if self.hits else None


class Retriever(Protocol):
    """Knowledge retrieval contract used by RetrieveSimilarCaseTool."""

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return approved knowledge assets ranked by relevance."""


class KnowledgeAssetRepository:
    """Adapter that projects legacy RCA tables into KnowledgeAsset objects."""

    def __init__(self, repository: FabRepository) -> None:
        self.repository = repository

    def confirmed_assets(self) -> list[KnowledgeAsset]:
        documents_by_case_id: dict[str, list[KnowledgeAssetDocument]] = {}
        for row in self.repository.rows("knowledge_document"):
            if not _is_confirmed(row.get("validation_status")):
                continue
            document = KnowledgeAssetDocument.from_row(row)
            documents_by_case_id.setdefault(document.case_id, []).append(document)

        assets: list[KnowledgeAsset] = []
        for row in self.repository.rows("rca_case"):
            if not _is_confirmed(row.get("validation_status")):
                continue
            assets.append(
                KnowledgeAsset.from_case_row(
                    row,
                    documents=documents_by_case_id.get(row["case_id"], []),
                )
            )
        return assets

class KeywordRetriever:
    """Keyword retriever that preserves the existing historical-case scoring."""

    def __init__(self, asset_repository: KnowledgeAssetRepository) -> None:
        self.asset_repository = asset_repository

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        normalized_query = query.query.strip().lower()
        module = query.module.lower()
        equipment_type = query.equipment_type.lower()
        hits: list[RetrievalHit] = []
        for asset in self.asset_repository.confirmed_assets():
            searchable = " ".join(
                [
                    asset.title,
                    asset.module,
                    asset.equipment_type,
                    asset.symptom,
                    asset.root_cause,
                    asset.solution,
                ]
            ).lower()
            score = 0.0
            for token in normalized_query.replace("/", " ").replace(";", " ").split():
                if token in searchable:
                    score += 0.08
            if module and module in asset.module.lower():
                score += 0.25
            if equipment_type and equipment_type == asset.equipment_type.lower():
                score += 0.2
            score = min(0.99, max(score, asset.confidence * 0.8))
            hits.append(RetrievalHit(asset=asset, score=round(score, 3)))

        hits = sorted(hits, key=lambda item: item.score, reverse=True)
        return RetrievalResult(query=query, hits=hits[: query.top_k])


class TypedKnowledgeRetrieverAdapter:
    """Expose typed logical-asset retrieval to the legacy RCA Knowledge Tool.

    The adapter deliberately resolves only approved ``RCA_CASE`` assets. SOPs
    and Engineering Notes remain available through the independent Knowledge
    lookup intent and cannot be mistaken for historical root-cause evidence.
    """

    def __init__(
        self,
        repository: FabRepository,
        retriever: KnowledgeLookupRetriever,
        additional_asset_repositories: Sequence[KnowledgeAssetRepository] = (),
    ) -> None:
        self.asset_repositories = (
            KnowledgeAssetRepository(repository),
            *additional_asset_repositories,
        )
        self.retriever = retriever
        self.context_provider = RepositoryCausalContextProvider(repository)

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        observation = ObservationScope(
            source_lot_id=query.source_lot_id,
            product_id=query.product_id,
            detected_module=query.module,
            detected_operation=query.detected_operation,
            detected_equipment_id=query.detected_equipment_id,
            detected_equipment_type=query.equipment_type,
            detected_at=query.detected_at,
            symptom_types=query.symptom_types,
        )
        plan = KnowledgeLookupPlan(
            intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
            question_kind=KnowledgeQuestionKind.HISTORICAL_MATCH.value,
            query=query.query,
            allowed_document_types=(KnowledgeDocumentType.RCA_CASE.value,),
            reason=(
                "RCA Knowledge Agent requested a historical match; Python restricts "
                "the action to approved RCA_CASE logical assets."
            ),
            module=query.module,
            equipment_type=query.equipment_type,
            observation_scope=observation,
            explicit_module_limit=query.explicit_module_limit,
            top_k=query.top_k,
        )
        plan = prepare_causal_plan(
            self.retriever,
            plan,
            context_provider=self.context_provider,
        )
        fingerprint = sha256(
            (
                f"{query.query}|{query.module}|{query.equipment_type}|"
                f"{query.source_lot_id}|{query.detected_operation}|{query.detected_at}"
            ).encode()
        ).hexdigest()[:16].upper()
        logical_hits = self.retriever.retrieve(
            plan,
            lookup_id=f"KLOOK_AGENT_{fingerprint}",
        )
        assets: dict[str, KnowledgeAsset] = {}
        for repository in self.asset_repositories:
            for candidate_asset in repository.confirmed_assets():
                assets.setdefault(candidate_asset.asset_id, candidate_asset)
        hits: list[RetrievalHit] = []
        for logical_hit in logical_hits:
            case_id = logical_hit.document.case_id
            resolved_asset = assets.get(case_id or "")
            if resolved_asset is None:
                continue
            hits.append(
                RetrievalHit(
                    asset=resolved_asset,
                    score=logical_hit.score,
                    retrieval_strategy=logical_hit.retrieval_strategy,
                    score_components=dict(logical_hit.score_components),
                    calibrated_relevance=logical_hit.calibrated_relevance,
                    source_confidence=logical_hit.source_confidence,
                    matched_chunk_ids=logical_hit.matched_chunk_ids,
                    candidate_lanes=logical_hit.candidate_lanes,
                    scope_reasons=logical_hit.scope_reasons,
                    route_distance=logical_hit.route_distance,
                    shared_resource_types=logical_hit.shared_resource_types,
                    scope_fusion_score=logical_hit.scope_fusion_score,
                )
            )
        return RetrievalResult(
            query=query,
            hits=hits,
            observation_scope=plan.observation_scope,
            causal_search_scope=plan.causal_search_scope,
        )
