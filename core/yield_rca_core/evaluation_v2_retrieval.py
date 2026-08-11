"""Evaluation V2 retrieval adapters, calibration, slices, and release gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yield_rca_core.causal_retrieval import prepare_causal_plan
from yield_rca_core.causal_scope import ObservationScope, RepositoryCausalContextProvider
from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever
from yield_rca_core.knowledge_models import (
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.repositories import FabRepository
from yield_rca_core.retrieval_evaluation import (
    RankedRetrievalAsset,
    RetrievalEvaluationQuery,
    RetrievalGroundTruth,
    RetrievalRanking,
    evaluate_retrieval,
)


@dataclass(frozen=True)
class RetrievalV2QueryContext:
    """Hidden causal context joined by Incident Family, never exposed to Query Writer."""

    incident_family_id: str
    observation: ObservationScope
    causal_module: str
    discovery_lane: str
    metadata_quality: str
    expected_status: str

    @property
    def causal_slice(self) -> str:
        return (
            "same_module"
            if self.observation.detected_module.casefold() == self.causal_module.casefold()
            else "cross_module"
        )


def build_query_contexts(
    catalog: dict[str, Any],
    repository: FabRepository,
) -> dict[str, RetrievalV2QueryContext]:
    """Build Python-owned lookup context from the reviewed hidden Incident catalog."""

    equipment_types = {
        row.get("equipment_id", ""): row.get("equipment_type", "")
        for row in repository.rows("equipment_master")
    }
    contexts: dict[str, RetrievalV2QueryContext] = {}
    for family in catalog["incident_families"]:
        family_id = str(family["incident_family_id"])
        observation = dict(family["observation_record"])
        known_measurements = tuple(
            " ".join(
                str(item.get(key, ""))
                for key in ("metric_name", "observed", "unit")
                if str(item.get(key, "")).strip()
            )
            for item in observation.get("known_measurements", [])
        )
        known_attributes = tuple(
            str(value)
            for value in observation.get("known_defect_attributes", {}).values()
            if str(value).strip()
        )
        context = RetrievalV2QueryContext(
            incident_family_id=family_id,
            observation=ObservationScope(
                source_lot_id=str(observation.get("source_lot_id", "")),
                product_id=str(observation.get("product_id", "")),
                detected_module=str(observation.get("detected_module", "")),
                detected_operation=str(observation.get("detected_operation", "")),
                detected_equipment_id=str(observation.get("detected_equipment_id", "")),
                detected_equipment_type=equipment_types.get(
                    str(observation.get("detected_equipment_id", "")), ""
                ),
                detected_at=str(observation.get("detected_at", "")),
                symptom_types=tuple(str(item) for item in observation.get("symptom_types", [])),
                known_measurements=known_measurements,
                known_defect_attributes=known_attributes,
            ),
            causal_module=str(
                (family.get("causal_record") or {}).get("causal_module", "")
            ),
            discovery_lane=str(family.get("discovery_lane", "")),
            metadata_quality=str(family.get("metadata_quality", "")),
            expected_status=str(family.get("expected_status", "")),
        )
        contexts[family_id] = context
    return contexts


@dataclass(frozen=True)
class AbstentionCalibration:
    threshold: float
    calibration_query_count: int
    answerable_recall: float
    no_answer_accuracy: float
    balanced_accuracy: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "threshold": self.threshold,
            "calibration_query_count": self.calibration_query_count,
            "answerable_recall": self.answerable_recall,
            "no_answer_accuracy": self.no_answer_accuracy,
            "balanced_accuracy": self.balanced_accuracy,
        }


class RetrievalV2EvaluationBackend:
    """Apply one Retriever under either legacy-hard or four-lane causal Scope."""

    def __init__(
        self,
        name: str,
        retriever: KnowledgeLookupRetriever,
        *,
        query_contexts: dict[str, RetrievalV2QueryContext],
        context_provider: RepositoryCausalContextProvider,
        scope_mode: str,
        abstention_threshold: float | None = None,
        candidate_k: int = 20,
        final_k: int = 10,
    ) -> None:
        if scope_mode not in {"legacy_hard", "causal_wide"}:
            raise ValueError("scope_mode must be legacy_hard or causal_wide")
        if not 1 <= final_k <= candidate_k <= 20:
            raise ValueError("V2 retrieval requires 1 <= final_k <= candidate_k <= 20")
        self.name = name
        self.retriever = retriever
        self.query_contexts = query_contexts
        self.context_provider = context_provider
        self.scope_mode = scope_mode
        self.abstention_threshold = abstention_threshold
        self.candidate_k = candidate_k
        self.final_k = final_k
        self.scope_audits: dict[str, dict[str, Any]] = {}

    def _plan(self, query: RetrievalEvaluationQuery) -> KnowledgeLookupPlan:
        context = self.query_contexts[query.incident_family_id]
        kind = KnowledgeQuestionKind(query.question_kind)
        observation = context.observation
        plan = KnowledgeLookupPlan(
            intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
            question_kind=kind.value,
            query=query.text,
            allowed_document_types=(kind.document_type,),
            reason="Evaluation V2 uses reviewed Query text and Python-owned causal Scope.",
            module=observation.detected_module,
            observation_scope=observation,
            top_k=self.candidate_k,
        )
        if self.scope_mode == "causal_wide":
            return prepare_causal_plan(
                self.retriever,
                plan,
                context_provider=self.context_provider,
            )
        return plan

    @staticmethod
    def _ranked_asset(hit: Any) -> RankedRetrievalAsset:
        score = (
            float(hit.calibrated_relevance)
            if hit.calibrated_relevance is not None
            else float(hit.score)
        )
        return RankedRetrievalAsset(
            asset_id=str(hit.document.evaluation_asset_id),
            score=score,
            validation_status=str(hit.document.validation_status),
        )

    def rank(self, query: RetrievalEvaluationQuery) -> RetrievalRanking:
        plan = self._plan(query)
        hits = self.retriever.retrieve(
            plan,
            lookup_id=f"KLOOK_V2_{self.name}_{query.query_id}",
        )
        candidates = tuple(self._ranked_asset(hit) for hit in hits[: self.candidate_k])
        answered = bool(candidates) and (
            self.abstention_threshold is None
            or candidates[0].score >= self.abstention_threshold
        )
        final_hits = candidates[: self.final_k] if answered else ()
        scope = plan.causal_search_scope
        self.scope_audits[query.query_id] = {
            "query_id": query.query_id,
            "incident_family_id": query.incident_family_id,
            "scope_mode": self.scope_mode,
            "source_lot_id": (
                plan.observation_scope.source_lot_id if plan.observation_scope else ""
            ),
            "time_boundary": scope.time_boundary if scope else "",
            "hard_module": scope.hard_constraints.module if scope else plan.module,
            "soft_module": scope.soft_hints.module if scope else "",
            "available_lanes": list(scope.available_lanes) if scope else ["same_step"],
            "candidate_lanes": {
                str(hit.document.evaluation_asset_id): list(hit.candidate_lanes)
                for hit in hits
            },
            "top_score": candidates[0].score if candidates else None,
            "abstention_threshold": self.abstention_threshold,
            "abstained": not answered,
        }
        return RetrievalRanking(candidates=candidates, final_hits=final_hits)


def fit_abstention_threshold(
    calibration: RetrievalGroundTruth,
    backend: RetrievalV2EvaluationBackend,
) -> AbstentionCalibration:
    """Fit one top-score threshold using only the fixed calibration partition."""

    rows: list[tuple[float | None, bool]] = []
    for query in calibration.queries:
        ranking = backend.rank(query)
        score = ranking.candidates[0].score if ranking.candidates else None
        rows.append((score, not query.no_answer))
    scores = sorted({score for score, _ in rows if score is not None})
    thresholds = [0.0, *(round(score, 12) for score in scores)]
    if scores:
        thresholds.append(round(max(scores) + 1e-9, 12))
    answerable_count = sum(is_answerable for _, is_answerable in rows)
    no_answer_count = len(rows) - answerable_count
    best: tuple[tuple[float, float, float, float], AbstentionCalibration] | None = None
    for threshold in thresholds:
        answerable_correct = sum(
            bool(score is not None and score >= threshold)
            for score, is_answerable in rows
            if is_answerable
        )
        no_answer_correct = sum(
            bool(score is None or score < threshold)
            for score, is_answerable in rows
            if not is_answerable
        )
        answer_recall = answerable_correct / answerable_count if answerable_count else 1.0
        no_answer_accuracy = no_answer_correct / no_answer_count if no_answer_count else 1.0
        balanced = (answer_recall + no_answer_accuracy) / 2.0
        fitted = AbstentionCalibration(
            threshold=threshold,
            calibration_query_count=len(rows),
            answerable_recall=round(answer_recall, 6),
            no_answer_accuracy=round(no_answer_accuracy, 6),
            balanced_accuracy=round(balanced, 6),
        )
        key = (
            min(answer_recall, no_answer_accuracy),
            balanced,
            answer_recall,
            -threshold,
        )
        if best is None or key > best[0]:
            best = (key, fitted)
    assert best is not None
    return best[1]


def evaluate_v2_retriever(
    ground_truth: RetrievalGroundTruth,
    backend: RetrievalV2EvaluationBackend,
    *,
    asset_statuses: dict[str, str],
) -> dict[str, Any]:
    evaluation = evaluate_retrieval(
        ground_truth,
        backend,
        asset_statuses=asset_statuses,
    )
    query_by_id = {item.query_id: item for item in ground_truth.queries}
    rows = [row for row in evaluation["results"] if not row["no_answer"]]
    for row in evaluation["results"]:
        query = query_by_id[row["query_id"]]
        context = backend.query_contexts[query.incident_family_id]
        row.update(
            {
                "incident_family_id": query.incident_family_id,
                "partition": query.partition,
                "metadata_quality": query.metadata_quality,
                "causal_slice": context.causal_slice,
                "expected_discovery_lane": context.discovery_lane,
                "scope_audit": backend.scope_audits[query.query_id],
            }
        )

    def sliced(field: str, value: str) -> dict[str, int | float]:
        selected = [row for row in rows if row[field] == value]
        recalls = [float(row["per_query_metrics"]["recall_at_5"]) for row in selected]
        return {
            "query_count": len(selected),
            "recall_at_5": round(sum(recalls) / len(recalls), 6) if recalls else 0.0,
        }

    evaluation["slices"] = {
        "causal_scope": {
            value: sliced("causal_slice", value)
            for value in ("same_module", "cross_module")
        },
        "metadata_quality": {
            value: sliced("metadata_quality", value)
            for value in ("complete", "missing", "noisy")
        },
        "discovery_lane": {
            value: sliced("expected_discovery_lane", value)
            for value in (
                "same_step",
                "upstream_route",
                "shared_resource",
                "global_semantic",
            )
        },
    }
    return dict(evaluation)


def retrieval_release_decision(
    *,
    chunk_keyword: dict[str, Any],
    hybrid: dict[str, Any],
    legacy_hybrid: dict[str, Any],
    causal_hybrid: dict[str, Any],
    reranked: dict[str, Any] | None,
) -> dict[str, Any]:
    keyword_metrics = chunk_keyword["metrics"]
    hybrid_metrics = hybrid["metrics"]
    same_legacy = legacy_hybrid["slices"]["causal_scope"]["same_module"]
    same_causal = causal_hybrid["slices"]["causal_scope"]["same_module"]
    cross_legacy = legacy_hybrid["slices"]["causal_scope"]["cross_module"]
    cross_causal = causal_hybrid["slices"]["causal_scope"]["cross_module"]
    hybrid_non_regression = all(
        hybrid_metrics[name] >= keyword_metrics[name]
        for name in (
            "recall_at_5",
            "hard_negative_pairwise_win_rate",
            "no_answer_accuracy",
        )
    )
    causal_promoted = bool(
        cross_causal["query_count"]
        and cross_causal["recall_at_5"] > cross_legacy["recall_at_5"]
        and same_causal["recall_at_5"] >= same_legacy["recall_at_5"]
    )
    reranker_promoted = False
    reranker_checks: dict[str, bool] = {
        "evaluated": reranked is not None,
        "ndcg_strictly_improved": False,
        "primary_metrics_non_regressing": False,
    }
    if reranked is not None:
        reranked_metrics = reranked["metrics"]
        reranker_checks.update(
            {
                "ndcg_strictly_improved": (
                    reranked_metrics["ndcg_at_10"] > hybrid_metrics["ndcg_at_10"]
                ),
                "primary_metrics_non_regressing": all(
                    reranked_metrics[name] >= hybrid_metrics[name]
                    for name in (
                        "recall_at_5",
                        "hard_negative_pairwise_win_rate",
                        "no_answer_accuracy",
                    )
                ),
            }
        )
        reranker_promoted = all(reranker_checks.values())
    selected_ranker = "hybrid_rrf" if hybrid_non_regression else "chunk_keyword"
    return {
        "hybrid_non_regression": hybrid_non_regression,
        "causal_scope": {
            "cross_module_strictly_improved": (
                cross_causal["recall_at_5"] > cross_legacy["recall_at_5"]
            ),
            "same_module_non_regressing": (
                same_causal["recall_at_5"] >= same_legacy["recall_at_5"]
            ),
            "promoted": causal_promoted,
        },
        "reranker": {**reranker_checks, "promoted": reranker_promoted},
        "selected_runtime": {
            "retriever": selected_ranker,
            "causal_scope_enabled": causal_promoted,
            "reranker_enabled": reranker_promoted,
        },
        "passed": hybrid_non_regression and causal_promoted,
    }
