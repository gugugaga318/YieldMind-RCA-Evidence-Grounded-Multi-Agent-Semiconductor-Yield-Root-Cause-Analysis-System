"""Offline retrieval evaluation contracts and ranking metrics.

This module deliberately stays separate from the end-to-end RCA evaluation
suite.  Retrieval quality and RCA conclusion quality are different release
boundaries and use independent ground-truth catalogs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import log2
from pathlib import Path
from typing import Any, Protocol

from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever
from yield_rca_core.knowledge_models import (
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.knowledge_retrieval import KeywordRetriever, RetrievalQuery

RETRIEVAL_EVALUATION_SCHEMA_VERSION = "1.0"
ALLOWED_RELEVANCE_GRADES = {0, 1, 2, 3}
ALLOWED_QUERY_LANGUAGES = {"en", "zh", "mixed"}
ALLOWED_QUESTION_KINDS = {
    "historical_match",
    "procedure_guidance",
    "engineering_note_lookup",
}


class RetrievalEvaluationError(ValueError):
    """Raised when retrieval evaluation data violates its contract."""


@dataclass(frozen=True)
class RetrievalQrel:
    """One graded relevance judgment for a logical knowledge asset."""

    asset_id: str
    relevance: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalQrel:
        asset_id = str(data.get("asset_id", "")).strip()
        try:
            relevance = int(data.get("relevance", -1))
        except (TypeError, ValueError) as exc:
            raise RetrievalEvaluationError("qrel relevance must be an integer") from exc
        if not asset_id:
            raise RetrievalEvaluationError("qrel asset_id must not be empty")
        if relevance not in ALLOWED_RELEVANCE_GRADES:
            raise RetrievalEvaluationError("qrel relevance must be one of 0, 1, 2, or 3")
        return cls(asset_id=asset_id, relevance=relevance)


@dataclass(frozen=True)
class RetrievalEvaluationQuery:
    """One query and its explicit evaluation slice labels."""

    query_id: str
    text: str
    language: str
    question_kind: str
    module: str = ""
    equipment_type: str = ""
    cross_language: bool = False
    no_answer: bool = False
    hard_negative_asset_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalEvaluationQuery:
        query_id = str(data.get("query_id", "")).strip()
        text = str(data.get("text", "")).strip()
        language = str(data.get("language", "")).strip().lower()
        question_kind = str(data.get("question_kind", "")).strip()
        if not query_id or not text:
            raise RetrievalEvaluationError("query_id and text must not be empty")
        if language not in ALLOWED_QUERY_LANGUAGES:
            raise RetrievalEvaluationError(f"query {query_id} language must be en, zh, or mixed")
        if question_kind not in ALLOWED_QUESTION_KINDS:
            raise RetrievalEvaluationError(
                f"query {query_id} has unsupported question_kind {question_kind!r}"
            )
        hard_negatives = tuple(
            str(item).strip() for item in data.get("hard_negative_asset_ids", [])
        )
        if any(not item for item in hard_negatives):
            raise RetrievalEvaluationError(f"query {query_id} has an empty hard-negative asset ID")
        if len(hard_negatives) != len(set(hard_negatives)):
            raise RetrievalEvaluationError(
                f"query {query_id} has duplicate hard-negative asset IDs"
            )
        return cls(
            query_id=query_id,
            text=text,
            language=language,
            question_kind=question_kind,
            module=str(data.get("module", "")).strip(),
            equipment_type=str(data.get("equipment_type", "")).strip(),
            cross_language=bool(data.get("cross_language", False)),
            no_answer=bool(data.get("no_answer", False)),
            hard_negative_asset_ids=hard_negatives,
        )


@dataclass(frozen=True)
class RetrievalGroundTruth:
    """Versioned query catalog plus qrels, independent of RCA scenarios."""

    schema_version: str
    corpus_version: str
    relevance_threshold: int
    queries: tuple[RetrievalEvaluationQuery, ...]
    qrels: dict[str, tuple[RetrievalQrel, ...]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalGroundTruth:
        schema_version = str(data.get("schema_version", "")).strip()
        corpus_version = str(data.get("corpus_version", "")).strip()
        try:
            relevance_threshold = int(data.get("relevance_threshold", 1))
        except (TypeError, ValueError) as exc:
            raise RetrievalEvaluationError("relevance_threshold must be an integer") from exc
        if schema_version != RETRIEVAL_EVALUATION_SCHEMA_VERSION:
            raise RetrievalEvaluationError("unsupported retrieval evaluation schema version")
        if not corpus_version:
            raise RetrievalEvaluationError("corpus_version must not be empty")
        if relevance_threshold not in {1, 2, 3}:
            raise RetrievalEvaluationError("relevance_threshold must be 1, 2, or 3")

        raw_queries = data.get("queries")
        raw_qrels = data.get("qrels")
        if not isinstance(raw_queries, list) or not isinstance(raw_qrels, dict):
            raise RetrievalEvaluationError("queries must be a list and qrels must be an object")
        queries = tuple(RetrievalEvaluationQuery.from_dict(dict(item)) for item in raw_queries)
        query_ids = [item.query_id for item in queries]
        if len(query_ids) != len(set(query_ids)):
            raise RetrievalEvaluationError("query_id values must be unique")
        if set(raw_qrels) != set(query_ids):
            raise RetrievalEvaluationError("qrels keys must exactly match query IDs")

        qrels: dict[str, tuple[RetrievalQrel, ...]] = {}
        for query_id in query_ids:
            raw_items = raw_qrels[query_id]
            if not isinstance(raw_items, list):
                raise RetrievalEvaluationError(f"qrels for {query_id} must be a list")
            items = tuple(RetrievalQrel.from_dict(dict(item)) for item in raw_items)
            asset_ids = [item.asset_id for item in items]
            if len(asset_ids) != len(set(asset_ids)):
                raise RetrievalEvaluationError(f"qrels for {query_id} contain duplicate assets")
            qrels[query_id] = items

        result = cls(
            schema_version=schema_version,
            corpus_version=corpus_version,
            relevance_threshold=relevance_threshold,
            queries=queries,
            qrels=qrels,
        )
        result._validate_slices_and_judgments()
        return result

    @classmethod
    def load(cls, path: Path) -> RetrievalGroundTruth:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _validate_slices_and_judgments(self) -> None:
        answerable = 0
        no_answer = 0
        cross_language = 0
        hard_negative = 0
        graded_multi_relevant = 0
        for query in self.queries:
            judgments = self.qrels[query.query_id]
            positive = [item for item in judgments if item.relevance >= self.relevance_threshold]
            grades = {item.relevance for item in positive}
            by_asset = {item.asset_id: item.relevance for item in judgments}
            if query.no_answer:
                no_answer += 1
                if any(item.relevance > 0 for item in judgments):
                    raise RetrievalEvaluationError(
                        f"no-answer query {query.query_id} cannot have positive qrels"
                    )
            else:
                answerable += 1
                if not positive:
                    raise RetrievalEvaluationError(
                        f"answerable query {query.query_id} requires a positive qrel"
                    )
                if query.cross_language:
                    cross_language += 1
                if len(positive) > 1 and len(grades) > 1:
                    graded_multi_relevant += 1

            if query.hard_negative_asset_ids:
                hard_negative += 1
                for asset_id in query.hard_negative_asset_ids:
                    if by_asset.get(asset_id) != 0:
                        raise RetrievalEvaluationError(
                            f"hard negative {asset_id} for {query.query_id} must have grade 0"
                        )

        if not answerable or not no_answer or not cross_language or not hard_negative:
            raise RetrievalEvaluationError(
                "ground truth requires answerable, no-answer, cross-language, and "
                "hard-negative slices"
            )
        if not graded_multi_relevant:
            raise RetrievalEvaluationError(
                "ground truth requires a multi-relevant query with multiple positive grades"
            )

    def validate_asset_catalog(self, asset_statuses: dict[str, str]) -> None:
        """Validate qrel and hard-negative IDs against the governed corpus."""

        known = set(asset_statuses)
        for query in self.queries:
            for judgment in self.qrels[query.query_id]:
                if judgment.asset_id not in known:
                    raise RetrievalEvaluationError(
                        f"unknown qrel asset {judgment.asset_id} for {query.query_id}"
                    )
                if judgment.relevance >= self.relevance_threshold and (
                    asset_statuses[judgment.asset_id].upper() != "CONFIRMED"
                ):
                    raise RetrievalEvaluationError(
                        f"positive qrel asset {judgment.asset_id} is not CONFIRMED"
                    )
            for asset_id in query.hard_negative_asset_ids:
                if asset_id not in known:
                    raise RetrievalEvaluationError(
                        f"unknown hard-negative asset {asset_id} for {query.query_id}"
                    )


@dataclass(frozen=True)
class RankedRetrievalAsset:
    """One logical asset after any chunk-to-document/case aggregation."""

    asset_id: str
    score: float
    validation_status: str = "CONFIRMED"


@dataclass(frozen=True)
class RetrievalRanking:
    """Candidate-stage ranking and final retrieval ranking for one query."""

    candidates: tuple[RankedRetrievalAsset, ...]
    final_hits: tuple[RankedRetrievalAsset, ...]


class RetrievalEvaluationBackend(Protocol):
    """Adapter contract implemented by Keyword, Hybrid, and reranked systems."""

    name: str

    def rank(self, query: RetrievalEvaluationQuery) -> RetrievalRanking:
        """Return unique logical assets at candidate and final stages."""


class KeywordRetrieverEvaluationBackend:
    """Offline adapter that measures the current case-only KeywordRetriever."""

    name = "KeywordRetriever"

    def __init__(
        self,
        retriever: KeywordRetriever,
        *,
        name: str = "KeywordRetriever",
        candidate_k: int = 20,
        final_k: int = 10,
    ) -> None:
        self.name = name
        self.retriever = retriever
        self.candidate_k = candidate_k
        self.final_k = final_k

    def rank(self, query: RetrievalEvaluationQuery) -> RetrievalRanking:
        result = self.retriever.retrieve(
            RetrievalQuery(
                query=query.text,
                module=query.module,
                equipment_type=query.equipment_type,
                top_k=self.candidate_k,
            )
        )
        candidates = tuple(
            RankedRetrievalAsset(
                asset_id=hit.asset.asset_id,
                score=hit.score,
                validation_status=hit.asset.validation_status,
            )
            for hit in result.hits
        )
        # The current Retriever has no calibrated abstention contract.  Its
        # final result is therefore the same ranking, truncated to final_k.
        return RetrievalRanking(
            candidates=candidates,
            final_hits=candidates[: self.final_k],
        )


class KnowledgeLookupRetrieverEvaluationBackend:
    """Evaluate a governed Chunk Retriever after logical-asset aggregation."""

    def __init__(
        self,
        name: str,
        retriever: KnowledgeLookupRetriever,
        *,
        candidate_k: int = 20,
        final_k: int = 10,
    ) -> None:
        if not 1 <= final_k <= candidate_k <= 20:
            raise ValueError("evaluation requires 1 <= final_k <= candidate_k <= 20")
        self.name = name
        self.retriever = retriever
        self.candidate_k = candidate_k
        self.final_k = final_k

    def rank(self, query: RetrievalEvaluationQuery) -> RetrievalRanking:
        kind = KnowledgeQuestionKind(query.question_kind)
        plan = KnowledgeLookupPlan(
            intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
            question_kind=kind.value,
            query=query.text,
            allowed_document_types=(kind.document_type,),
            reason="Offline retrieval evaluation under Python-owned scope rules.",
            module=query.module,
            equipment_type=query.equipment_type,
            top_k=self.candidate_k,
        )
        hits = self.retriever.retrieve(plan, lookup_id=f"KLOOK_EVAL_{query.query_id}")
        candidates = tuple(
            RankedRetrievalAsset(
                asset_id=item.document.evaluation_asset_id,
                score=item.score,
                validation_status=item.document.validation_status,
            )
            for item in hits
        )
        return RetrievalRanking(
            candidates=candidates,
            final_hits=candidates[: self.final_k],
        )


def _assert_unique_ranked_assets(
    values: tuple[RankedRetrievalAsset, ...],
    *,
    query_id: str,
    stage: str,
) -> None:
    ids = [item.asset_id for item in values]
    if len(ids) != len(set(ids)):
        raise RetrievalEvaluationError(
            f"{stage} ranking for {query_id} contains duplicate logical asset IDs"
        )


def _recall_at(ids: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        raise RetrievalEvaluationError("recall requires at least one relevant asset")
    return len(set(ids[:k]) & relevant) / len(relevant)


def _reciprocal_rank_at(ids: list[str], relevant: set[str], k: int) -> float:
    for rank, asset_id in enumerate(ids[:k], start=1):
        if asset_id in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at(ids: list[str], qrels: dict[str, int], k: int) -> float:
    def dcg(grades: list[int]) -> float:
        return float(
            sum(((2**grade) - 1) / log2(rank + 1) for rank, grade in enumerate(grades, start=1))
        )

    actual = dcg([qrels.get(asset_id, 0) for asset_id in ids[:k]])
    ideal = dcg(sorted((grade for grade in qrels.values() if grade > 0), reverse=True)[:k])
    if ideal == 0:
        raise RetrievalEvaluationError("nDCG requires at least one positive qrel")
    return actual / ideal


def _mean(values: list[float], *, metric_name: str) -> float:
    if not values:
        raise RetrievalEvaluationError(f"{metric_name} has no eligible queries")
    return sum(values) / len(values)


def _rounded(value: float) -> float:
    return round(value, 6)


def evaluate_retrieval(
    ground_truth: RetrievalGroundTruth,
    backend: RetrievalEvaluationBackend,
    *,
    asset_statuses: dict[str, str],
) -> dict[str, Any]:
    """Evaluate one Retriever without applying any hidden score threshold."""

    ground_truth.validate_asset_catalog(asset_statuses)
    answerable_rows: list[dict[str, Any]] = []
    no_answer_rows: list[dict[str, Any]] = []
    leakage_ids: set[str] = set()
    leakage_hit_count = 0
    results: list[dict[str, Any]] = []

    for query in ground_truth.queries:
        ranking = backend.rank(query)
        _assert_unique_ranked_assets(
            ranking.candidates,
            query_id=query.query_id,
            stage="candidate",
        )
        _assert_unique_ranked_assets(
            ranking.final_hits,
            query_id=query.query_id,
            stage="final",
        )
        candidate_ids = [item.asset_id for item in ranking.candidates]
        final_ids = [item.asset_id for item in ranking.final_hits]
        unknown = (set(candidate_ids) | set(final_ids)) - set(asset_statuses)
        if unknown:
            raise RetrievalEvaluationError(
                f"retriever returned unknown assets for {query.query_id}: {sorted(unknown)}"
            )
        if not set(final_ids).issubset(candidate_ids):
            raise RetrievalEvaluationError(
                f"final ranking for {query.query_id} is not a subset of candidates"
            )

        row_leakage: set[str] = set()
        for item in (*ranking.candidates, *ranking.final_hits):
            catalog_status = asset_statuses[item.asset_id].upper()
            if item.validation_status.upper() != "CONFIRMED" or catalog_status != "CONFIRMED":
                row_leakage.add(item.asset_id)
                leakage_hit_count += 1
        leakage_ids.update(row_leakage)

        judgments = {item.asset_id: item.relevance for item in ground_truth.qrels[query.query_id]}
        relevant = {
            asset_id
            for asset_id, grade in judgments.items()
            if grade >= ground_truth.relevance_threshold
        }
        hard_negative_positions = {
            asset_id: final_ids.index(asset_id) + 1
            for asset_id in query.hard_negative_asset_ids
            if asset_id in final_ids[:10]
        }
        first_relevant_rank = next(
            (rank for rank, asset_id in enumerate(final_ids[:10], start=1) if asset_id in relevant),
            None,
        )
        hard_negatives_ahead = sorted(
            asset_id
            for asset_id, rank in hard_negative_positions.items()
            if first_relevant_rank is None or rank < first_relevant_rank
        )

        per_query: dict[str, float | bool | None] = {
            "recall_at_5": None,
            "candidate_recall_at_20": None,
            "mrr_at_10": None,
            "ndcg_at_10": None,
            "hard_negative_accuracy": None,
            "no_answer_correct": None,
        }
        if query.no_answer:
            per_query["no_answer_correct"] = not final_ids
            no_answer_rows.append(
                {
                    "correct": bool(per_query["no_answer_correct"]),
                    "question_kind": query.question_kind,
                }
            )
        else:
            recall_at_5 = _recall_at(final_ids, relevant, 5)
            candidate_recall_at_20 = _recall_at(candidate_ids, relevant, 20)
            mrr_at_10 = _reciprocal_rank_at(final_ids, relevant, 10)
            ndcg_at_10 = _ndcg_at(final_ids, judgments, 10)
            hard_negative_accuracy = (
                first_relevant_rank is not None and not hard_negatives_ahead
                if query.hard_negative_asset_ids
                else None
            )
            per_query.update(
                {
                    "recall_at_5": recall_at_5,
                    "candidate_recall_at_20": candidate_recall_at_20,
                    "mrr_at_10": mrr_at_10,
                    "ndcg_at_10": ndcg_at_10,
                    "hard_negative_accuracy": hard_negative_accuracy,
                }
            )
            answerable_rows.append(
                {
                    "query": query,
                    "recall_at_5": recall_at_5,
                    "candidate_recall_at_20": candidate_recall_at_20,
                    "mrr_at_10": mrr_at_10,
                    "ndcg_at_10": ndcg_at_10,
                    "hard_negative_accuracy": hard_negative_accuracy,
                }
            )

        results.append(
            {
                "query_id": query.query_id,
                "query_language": query.language,
                "question_kind": query.question_kind,
                "cross_language": query.cross_language,
                "no_answer": query.no_answer,
                "qrels": [
                    {"asset_id": asset_id, "relevance": grade}
                    for asset_id, grade in sorted(judgments.items())
                ],
                "candidate_asset_ids": candidate_ids,
                "final_asset_ids": final_ids,
                "first_relevant_rank": first_relevant_rank,
                "hard_negatives_ahead": hard_negatives_ahead,
                "approval_leakage_ids": sorted(row_leakage),
                "per_query_metrics": {
                    key: _rounded(value) if isinstance(value, float) else value
                    for key, value in per_query.items()
                },
            }
        )

    cross_language_rows = [row for row in answerable_rows if row["query"].cross_language]
    hard_negative_rows = [
        row for row in answerable_rows if row["hard_negative_accuracy"] is not None
    ]
    no_answer_accuracy = _mean(
        [float(row["correct"]) for row in no_answer_rows],
        metric_name="no-answer accuracy",
    )

    by_question_kind: dict[str, dict[str, int | float]] = {}
    for kind in sorted(ALLOWED_QUESTION_KINDS):
        rows = [row for row in answerable_rows if row["query"].question_kind == kind]
        if not rows:
            continue
        by_question_kind[kind] = {
            "query_count": len(rows),
            "recall_at_5": _rounded(
                _mean([row["recall_at_5"] for row in rows], metric_name=f"{kind} recall")
            ),
            "mrr_at_10": _rounded(
                _mean([row["mrr_at_10"] for row in rows], metric_name=f"{kind} MRR")
            ),
            "ndcg_at_10": _rounded(
                _mean([row["ndcg_at_10"] for row in rows], metric_name=f"{kind} nDCG")
            ),
        }

    metrics = {
        "query_count": len(ground_truth.queries),
        "answerable_query_count": len(answerable_rows),
        "no_answer_query_count": len(no_answer_rows),
        "cross_language_query_count": len(cross_language_rows),
        "hard_negative_query_count": len(hard_negative_rows),
        "recall_at_5": _rounded(
            _mean([row["recall_at_5"] for row in answerable_rows], metric_name="Recall@5")
        ),
        "candidate_recall_at_20": _rounded(
            _mean(
                [row["candidate_recall_at_20"] for row in answerable_rows],
                metric_name="Candidate Recall@20",
            )
        ),
        "mrr_at_10": _rounded(
            _mean([row["mrr_at_10"] for row in answerable_rows], metric_name="MRR@10")
        ),
        "ndcg_at_10": _rounded(
            _mean([row["ndcg_at_10"] for row in answerable_rows], metric_name="nDCG@10")
        ),
        "cross_language_recall_at_5": _rounded(
            _mean(
                [row["recall_at_5"] for row in cross_language_rows],
                metric_name="Cross-language Recall@5",
            )
        ),
        "hard_negative_accuracy": _rounded(
            _mean(
                [float(row["hard_negative_accuracy"]) for row in hard_negative_rows],
                metric_name="hard-negative accuracy",
            )
        ),
        "hard_negative_outrank_rate": _rounded(
            1.0
            - _mean(
                [float(row["hard_negative_accuracy"]) for row in hard_negative_rows],
                metric_name="hard-negative accuracy",
            )
        ),
        "no_answer_accuracy": _rounded(no_answer_accuracy),
        "no_answer_false_positive_rate": _rounded(1.0 - no_answer_accuracy),
        "unapproved_hit_count": leakage_hit_count,
        "unapproved_asset_ids": sorted(leakage_ids),
        "by_question_kind": by_question_kind,
    }
    release_gate_passed = not leakage_ids and leakage_hit_count == 0
    return {
        "schema_version": RETRIEVAL_EVALUATION_SCHEMA_VERSION,
        "corpus_version": ground_truth.corpus_version,
        "relevance_threshold": ground_truth.relevance_threshold,
        "retriever": backend.name,
        "passed": release_gate_passed,
        "acceptance": {
            "evaluation_completed": True,
            "quality_metrics_are_baseline_only": True,
            "unapproved_knowledge_leakage_gate": release_gate_passed,
        },
        "metrics": metrics,
        "results": results,
    }


def render_retrieval_evaluation_report(evaluation: dict[str, Any]) -> str:
    """Render a deterministic Markdown summary for interview and regression use."""

    metrics = evaluation["metrics"]
    acceptance = evaluation["acceptance"]
    lines = [
        f"# {evaluation['retriever']} Retrieval Baseline",
        "",
        f"- Corpus: `{evaluation['corpus_version']}`",
        f"- Recall relevance threshold: grade >= {evaluation['relevance_threshold']}",
        f"- Evaluation completed: `{'PASS' if acceptance['evaluation_completed'] else 'FAIL'}`",
        "- Quality metrics: baseline only (no target numbers are predeclared)",
        "- Unapproved knowledge leakage gate: "
        f"`{'PASS' if acceptance['unapproved_knowledge_leakage_gate'] else 'FAIL'}`",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Recall@5 | {metrics['recall_at_5']:.2%} |",
        f"| Candidate Recall@20 | {metrics['candidate_recall_at_20']:.2%} |",
        f"| MRR@10 | {metrics['mrr_at_10']:.4f} |",
        f"| nDCG@10 | {metrics['ndcg_at_10']:.4f} |",
        f"| Cross-language Recall@5 | {metrics['cross_language_recall_at_5']:.2%} |",
        f"| Hard-negative accuracy | {metrics['hard_negative_accuracy']:.2%} |",
        f"| Hard-negative outrank rate | {metrics['hard_negative_outrank_rate']:.2%} |",
        f"| No-answer accuracy | {metrics['no_answer_accuracy']:.2%} |",
        f"| No-answer false-positive rate | {metrics['no_answer_false_positive_rate']:.2%} |",
        f"| Unapproved hit count | {metrics['unapproved_hit_count']} |",
        "",
        "## Dataset Slices",
        "",
        f"- All queries: {metrics['query_count']}",
        f"- Answerable queries: {metrics['answerable_query_count']}",
        f"- No-answer queries: {metrics['no_answer_query_count']}",
        f"- Cross-language queries: {metrics['cross_language_query_count']}",
        f"- Hard-negative queries: {metrics['hard_negative_query_count']}",
        "",
        "## By Question Kind",
        "",
        "| Question kind | Queries | Recall@5 | MRR@10 | nDCG@10 |",
        "|---|---:|---:|---:|---:|",
    ]
    for kind, row in metrics["by_question_kind"].items():
        lines.append(
            f"| `{kind}` | {row['query_count']} | {row['recall_at_5']:.2%} | "
            f"{row['mrr_at_10']:.4f} | {row['ndcg_at_10']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "`scenarios.json` still evaluates end-to-end RCA conclusions. This report only "
            "evaluates retrieval ranking. A retrieved item is relevant only when the Python "
            "qrels contract says so; an LLM cannot override that judgment.",
            "",
            "The current KeywordRetriever has no calibrated abstention output, so every "
            "returned final hit counts as an answer. Its score mixes token matches and case "
            "confidence and is intentionally not reused as a hidden no-answer threshold.",
            "",
        ]
    )
    return "\n".join(lines)


def render_retrieval_ablation_report(ablation: dict[str, Any]) -> str:
    """Render a compact comparison without inventing a quality target."""

    runtime = ablation["embedding"]["runtime"]
    runtime_summary = (
        f"`sentence-transformers {runtime['sentence_transformers_version']}` / "
        f"`torch {runtime['torch_version']}` / `CUDA {runtime['cuda_runtime']}`"
        if runtime["backend"] == "sentence-transformers"
        else "`builtin deterministic backend`"
    )
    lines = [
        "# Hybrid Retrieval Ablation",
        "",
        f"- Corpus: `{ablation['corpus_version']}`",
        f"- Embedding backend: `{ablation['embedding']['model_name']}`",
        f"- Embedding revision: `{ablation['embedding']['model_revision']}`",
        f"- Requested device: `{ablation['embedding']['requested_device']}`",
        f"- Resolved device: `{ablation['embedding']['resolved_device']}`",
        f"- Runtime: {runtime_summary}",
        "- Quality metrics are measured comparisons, not predeclared release targets.",
        "- Unapproved knowledge leakage must remain zero for every Retriever.",
        "",
        "## Comparison",
        "",
        "| Retriever | Recall@5 | Candidate Recall@20 | MRR@10 | nDCG@10 | "
        "Cross-language Recall@5 | Hard-negative accuracy | No-answer accuracy | "
        "Unapproved hits |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ablation["order"]:
        metrics = ablation["evaluations"][name]["metrics"]
        lines.append(
            f"| `{name}` | {metrics['recall_at_5']:.2%} | "
            f"{metrics['candidate_recall_at_20']:.2%} | {metrics['mrr_at_10']:.4f} | "
            f"{metrics['ndcg_at_10']:.4f} | "
            f"{metrics['cross_language_recall_at_5']:.2%} | "
            f"{metrics['hard_negative_accuracy']:.2%} | "
            f"{metrics['no_answer_accuracy']:.2%} | "
            f"{metrics['unapproved_hit_count']} |"
        )
    lines.extend(
        [
            "",
            "## Architecture Boundary",
            "",
            "BM25 and Vector independently generate candidates. Hybrid combines their "
            "ranks with Reciprocal Rank Fusion; it does not ask an LLM to decide relevance. "
            "Document type, approval visibility, metadata scope, and qrels remain Python-owned.",
            "",
            "The exact-vector implementation is intentional for this small corpus. pgvector "
            "storage, Cross-Encoder reranking, online Agent cutover, and calibrated relevance "
            "remain Long Task 4 work.",
            "",
            "The Legacy Case Keyword row is a compatibility baseline, not a fair algorithm-only "
            "comparison because it cannot retrieve independent SOP or Engineering Note assets. "
            "Use Chunk Keyword as the current-online baseline when measuring BM25/Vector/Hybrid "
            "ranking gains. All values come from a Synthetic benchmark and do not claim "
            "production-fab accuracy.",
            "",
        ]
    )
    return "\n".join(lines)
