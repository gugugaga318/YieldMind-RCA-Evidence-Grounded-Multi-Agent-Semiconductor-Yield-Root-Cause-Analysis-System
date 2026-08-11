"""Run the reviewed Evaluation V2 retrieval and causal-Scope release evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
from yield_rca_core.causal_retrieval import CausalLaneKnowledgeRetriever  # noqa: E402
from yield_rca_core.causal_scope import RepositoryCausalContextProvider  # noqa: E402
from yield_rca_core.evaluation_v2_data import (  # noqa: E402
    TemplateSurfaceQueryProvider,
    build_evaluation_v2_dataset,
    load_incident_catalog,
    validate_evaluation_v2_dataset,
)
from yield_rca_core.evaluation_v2_retrieval import (  # noqa: E402
    RetrievalV2EvaluationBackend,
    build_query_contexts,
    evaluate_v2_retriever,
    fit_abstention_threshold,
    retrieval_release_decision,
)
from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    BM25DocumentChunkRetriever,
    DeterministicHashEmbeddingBackend,
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    PythonBM25CandidateSource,
    SentenceTransformerEmbeddingBackend,
    VectorDocumentChunkRetriever,
)
from yield_rca_core.hypothesis_engine import HypothesisEngine  # noqa: E402
from yield_rca_core.knowledge_lookup import DocumentChunkKeywordRetriever  # noqa: E402
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.models import AgentFinding, AgentKind, FindingKind  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.reranking import (  # noqa: E402
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    PlattScoreCalibrator,
    RerankedKnowledgeRetriever,
    RerankerBackend,
    SentenceTransformerRerankerBackend,
    fit_platt_score_calibration,
    reranker_document_text,
)
from yield_rca_core.retrieval_evaluation import RetrievalGroundTruth  # noqa: E402

DEFAULT_GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth_v2.json"
DEFAULT_PARTITIONS = ROOT / "data" / "evaluation" / "retrieval_partitions_v2.json"
DEFAULT_CATALOG = ROOT / "data" / "evaluation" / "incident_families_v2.json"
DEFAULT_CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v2"
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "causal_scope_v2"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "evaluation_v2_release" / "retrieval"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


class CachedRerankerBackend:
    """Persist raw reranker logits so interrupted GPU evaluation can resume."""

    def __init__(self, delegate: RerankerBackend, cache_path: Path) -> None:
        self.delegate = delegate
        self.model_name = delegate.model_name
        self.model_revision = delegate.model_revision
        self.device = delegate.device
        self.cache_path = cache_path
        self._cache = self._load()

    def _load(self) -> dict[str, float]:
        if not self.cache_path.exists():
            return {}
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        if (
            payload.get("model_name") != self.model_name
            or payload.get("model_revision") != self.model_revision
        ):
            return {}
        return {
            str(key): float(value) for key, value in payload.get("logits", {}).items()
        }

    @staticmethod
    def _key(query: str, document: str) -> str:
        return sha256(f"{query}\n{document}".encode()).hexdigest()

    def score_logits(
        self,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        keys = [self._key(query, document) for document in documents]
        missing_documents = [
            document
            for key, document in zip(keys, documents, strict=True)
            if key not in self._cache
        ]
        if missing_documents:
            values = self.delegate.score_logits(query, missing_documents)
            missing_keys = [key for key in keys if key not in self._cache]
            for key, value in zip(missing_keys, values, strict=True):
                self._cache[key] = value
            self.save()
            self.device = self.delegate.device
        return tuple(self._cache[key] for key in keys)

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "model_name": self.model_name,
                    "model_revision": self.model_revision,
                    "logits": self._cache,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def _historical_overreach(evaluation: dict[str, Any]) -> dict[str, int | float]:
    evaluated = 0
    overreaches = 0
    engine = HypothesisEngine()
    for row in evaluation["results"]:
        if row["question_kind"] != "historical_match" or row["no_answer"]:
            continue
        final_ids = list(row["final_asset_ids"])
        if not final_ids:
            continue
        evaluated += 1
        finding = AgentFinding(
            finding_id=f"FIND_V2_OVERREACH_{evaluated:03d}",
            agent=AgentKind.KNOWLEDGE.value,
            finding_kind=FindingKind.KNOWLEDGE_DISCOVERY.value,
            summary="Historical-only Evaluation V2 retrieval probe.",
            confidence=1.0,
            evidence_ids=[f"KEV_V2_OVERREACH_{evaluated:03d}"],
            details={
                "cases": [{"root_cause": final_ids[0], "similarity": 1.0}],
                "top_case": {"case_id": final_ids[0]},
            },
        )
        decision = engine.analyze(
            request_id=f"V2_OVERREACH_{evaluated:03d}",
            findings=[finding],
            mode="active",
        )["decision_gate"]
        if decision["status"] == "supported" or decision["root_cause"] != "inconclusive":
            overreaches += 1
    return {
        "evaluated_historical_only_queries": evaluated,
        "overreach_count": overreaches,
        "historical_overreach_rate": (
            round(overreaches / evaluated, 6) if evaluated else 0.0
        ),
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _partitions(
    ground_truth: RetrievalGroundTruth,
    partition_path: Path,
) -> tuple[RetrievalGroundTruth, RetrievalGroundTruth, dict[str, Any]]:
    partition = _load(partition_path)
    calibration_ids = set(partition["partitions"]["calibration"]["query_ids"])
    test_ids = set(partition["partitions"]["test"]["query_ids"])
    all_ids = {query.query_id for query in ground_truth.queries}
    if calibration_ids & test_ids or calibration_ids | test_ids != all_ids:
        raise ValueError("V2 partitions must be disjoint and cover every retrieval query")
    return ground_truth.subset(calibration_ids), ground_truth.subset(test_ids), partition


def _asset_statuses(store: Any) -> dict[str, str]:
    return {
        str(document.evaluation_asset_id): str(document.validation_status)
        for document in store.active_documents()
    }


def _evaluate_one(
    *,
    name: str,
    retriever: Any,
    scope_mode: str,
    calibration: RetrievalGroundTruth,
    test: RetrievalGroundTruth,
    query_contexts: Any,
    context_provider: Any,
    asset_statuses: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = RetrievalV2EvaluationBackend(
        f"{name}_CALIBRATION",
        retriever,
        query_contexts=query_contexts,
        context_provider=context_provider,
        scope_mode=scope_mode,
    )
    threshold = fit_abstention_threshold(calibration, raw)
    backend = RetrievalV2EvaluationBackend(
        name,
        retriever,
        query_contexts=query_contexts,
        context_provider=context_provider,
        scope_mode=scope_mode,
        abstention_threshold=threshold.threshold,
    )
    evaluation = evaluate_v2_retriever(test, backend, asset_statuses=asset_statuses)
    evaluation["abstention_calibration"] = threshold.to_dict()
    return evaluation, backend.scope_audits


def _reranker_calibration_pairs(
    ground_truth: RetrievalGroundTruth,
    backend: RetrievalV2EvaluationBackend,
    reranker: CachedRerankerBackend,
) -> tuple[list[float], list[int]]:
    logits: list[float] = []
    labels: list[int] = []
    for query in ground_truth.queries:
        plan = backend._plan(query)  # noqa: SLF001 - shared evaluation contract
        hits = backend.retriever.retrieve(plan, lookup_id=f"KLOOK_V2_RERANK_CAL_{query.query_id}")
        documents = tuple(reranker_document_text(hit) for hit in hits)
        scores = reranker.score_logits(query.text, documents)
        judgments = {
            item.asset_id: item.relevance for item in ground_truth.qrels[query.query_id]
        }
        for hit, score in zip(hits, scores, strict=True):
            logits.append(score)
            labels.append(
                int(
                    judgments.get(str(hit.document.evaluation_asset_id), 0)
                    >= ground_truth.relevance_threshold
                )
            )
    return logits, labels


def _data_gate(catalog: dict[str, Any]) -> dict[str, Any]:
    built = build_evaluation_v2_dataset(catalog, TemplateSurfaceQueryProvider())
    evaluation_dir = ROOT / "data" / "evaluation"
    built["qrel_review"] = _load(evaluation_dir / "retrieval_qrel_review_v2.json")
    built["scenario_review"] = _load(evaluation_dir / "rca_scenario_review_v2.json")
    report = validate_evaluation_v2_dataset(built)
    return {
        "status": "PASS" if report.structural_pass and report.human_review_complete else "FAIL",
        "structural_pass": report.structural_pass,
        "human_review_complete": report.human_review_complete,
        "pending_qrel_reviews": report.metrics["pending_qrel_reviews"],
        "pending_scenario_reviews": report.metrics["pending_scenario_reviews"],
        "errors": list(report.errors),
    }


def _scope_boundaries_hold(
    audits: dict[str, dict[str, Any]],
    query_contexts: Any,
    test: RetrievalGroundTruth,
) -> bool:
    for query in test.queries:
        audit = audits[query.query_id]
        observation = query_contexts[query.incident_family_id].observation
        if audit["source_lot_id"] != observation.source_lot_id:
            return False
        if audit["time_boundary"] != observation.detected_at:
            return False
    return True


def _report(result: dict[str, Any]) -> str:
    evaluations = result["retrieval"]["evaluations"]
    decision = result["retrieval"]["release_decision"]
    lines = [
        "# Evaluation V2 Retrieval Release Report",
        "",
        "> Synthetic benchmark only; these are not production-Fab accuracy claims.",
        "",
        f"- Data-quality gate: **{result['gates']['data_quality']['status']}**",
        f"- Governance gate: **{result['gates']['governance']['status']}**",
        f"- Retrieval-quality gate: **{result['gates']['retrieval_quality']['status']}**",
        f"- Selected retriever: `{decision['selected_runtime']['retriever']}`",
        "- Causal Scope enabled: "
        f"`{decision['selected_runtime']['causal_scope_enabled']}`",
        f"- Reranker enabled: `{decision['selected_runtime']['reranker_enabled']}`",
        "",
        "## Fair test-partition comparison",
        "",
        "| Retriever | Recall@5 | nDCG@10 | Hard-negative pairwise | No-answer | Leakage |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in result["retrieval"]["order"]:
        metrics = evaluations[name]["metrics"]
        lines.append(
            f"| {name} | {metrics['recall_at_5']:.2%} | {metrics['ndcg_at_10']:.4f} | "
            f"{metrics['hard_negative_pairwise_win_rate']:.2%} | "
            f"{metrics['no_answer_accuracy']:.2%} | {metrics['unapproved_hit_count']} |"
        )
    legacy = result["retrieval"]["scope_ablation"]["legacy_observed_module"]
    causal = result["retrieval"]["scope_ablation"]["causal_wide"]
    lines.extend(
        [
            "",
            "## Causal Scope ablation",
            "",
            "| Scope | Same-module Recall@5 | Cross-module Recall@5 |",
            "|---|---:|---:|",
            f"| Legacy observed-Module hard filter | {legacy['same_module']:.2%} | "
            f"{legacy['cross_module']:.2%} |",
            f"| Four-lane causal wide recall | {causal['same_module']:.2%} | "
            f"{causal['cross_module']:.2%} |",
            "",
            "## Failed test Queries",
            "",
        ]
    )
    failures = result["failed_cases"]
    if not failures:
        lines.append("No retrieval Query failed all headline checks.")
    else:
        for item in failures:
            lines.append(
                f"- `{item['query_id']}` via `{item['retriever']}`: "
                + ", ".join(item["failed_metrics"])
            )
    lines.extend(
        [
            "",
            "The calibration partition selected abstention thresholds. All headline metrics, "
            "Scope promotion checks, and failed cases above use only the disjoint test "
            "partition. Python owns qrels, approval visibility, Scope, and release decisions.",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    ground_truth = RetrievalGroundTruth.load(args.ground_truth)
    calibration, test, partitions = _partitions(ground_truth, args.partitions)
    catalog = load_incident_catalog(args.catalog)
    repository = CsvFabRepository(args.seed_dir)
    context_provider = RepositoryCausalContextProvider(repository)
    query_contexts = build_query_contexts(catalog, repository)
    store = load_builtin_knowledge_store(args.corpus_dir / "corpus.json")
    statuses = _asset_statuses(store)
    if args.embedding_backend == "deterministic":
        embedding: Any = DeterministicHashEmbeddingBackend()
    else:
        embedding = SentenceTransformerEmbeddingBackend(
            args.embedding_model,
            revision=args.embedding_revision,
            device=args.device,
            batch_size=args.embedding_batch_size,
        )
    lexical_source = PythonBM25CandidateSource(store)
    vector_source = ExactVectorCandidateSource(store, embedding)
    vector_source.prepare_queries(tuple(query.text for query in ground_truth.queries))
    base_retrievers: dict[str, Any] = {
        "Chunk-Keyword": DocumentChunkKeywordRetriever(store),
        "BM25-only": BM25DocumentChunkRetriever(lexical_source),
        "Vector-only": VectorDocumentChunkRetriever(vector_source),
        "Hybrid-RRF": HybridDocumentChunkRetriever(lexical_source, vector_source),
    }
    evaluations: dict[str, Any] = {}
    audits: dict[str, Any] = {}
    calibrations: dict[str, Any] = {}
    for name, base in base_retrievers.items():
        scoped = CausalLaneKnowledgeRetriever(base)
        evaluation, scope_audit = _evaluate_one(
            name=name,
            retriever=scoped,
            scope_mode="causal_wide",
            calibration=calibration,
            test=test,
            query_contexts=query_contexts,
            context_provider=context_provider,
            asset_statuses=statuses,
        )
        evaluations[name] = evaluation
        audits[name] = scope_audit
        calibrations[name] = evaluation["abstention_calibration"]

    legacy_hybrid, legacy_audit = _evaluate_one(
        name="Hybrid-RRF-Legacy-Hard",
        retriever=base_retrievers["Hybrid-RRF"],
        scope_mode="legacy_hard",
        calibration=calibration,
        test=test,
        query_contexts=query_contexts,
        context_provider=context_provider,
        asset_statuses=statuses,
    )
    audits["Hybrid-RRF-Legacy-Hard"] = legacy_audit

    reranked_evaluation: dict[str, Any] | None = None
    reranker_calibration: dict[str, Any] | None = None
    if args.evaluate_reranker:
        causal_hybrid = CausalLaneKnowledgeRetriever(base_retrievers["Hybrid-RRF"])
        calibration_backend = RetrievalV2EvaluationBackend(
            "Hybrid-RRF-Reranker-Calibration",
            causal_hybrid,
            query_contexts=query_contexts,
            context_provider=context_provider,
            scope_mode="causal_wide",
        )
        reranker_delegate = SentenceTransformerRerankerBackend(
            args.reranker_model,
            revision=args.reranker_revision,
            device=args.device,
            batch_size=args.reranker_batch_size,
            model_path=args.reranker_local_path,
        )
        cached_reranker = CachedRerankerBackend(
            reranker_delegate,
            args.output_dir / "reranker_logits_cache.json",
        )
        logits, labels = _reranker_calibration_pairs(
            calibration,
            calibration_backend,
            cached_reranker,
        )
        artifact = fit_platt_score_calibration(
            logits,
            labels,
            model_name=cached_reranker.model_name,
            model_revision=cached_reranker.model_revision,
            calibration_query_ids=tuple(query.query_id for query in calibration.queries),
        )
        reranked = RerankedKnowledgeRetriever(
            causal_hybrid,
            cached_reranker,
            calibrator=PlattScoreCalibrator(
                artifact,
                model_name=cached_reranker.model_name,
                model_revision=cached_reranker.model_revision,
            ),
        )
        reranked_evaluation, reranked_audit = _evaluate_one(
            name="Hybrid-RRF+CrossEncoder",
            retriever=reranked,
            scope_mode="causal_wide",
            calibration=calibration,
            test=test,
            query_contexts=query_contexts,
            context_provider=context_provider,
            asset_statuses=statuses,
        )
        evaluations["Hybrid-RRF+CrossEncoder"] = reranked_evaluation
        audits["Hybrid-RRF+CrossEncoder"] = reranked_audit
        calibrations["Hybrid-RRF+CrossEncoder"] = reranked_evaluation[
            "abstention_calibration"
        ]
        reranker_calibration = artifact.to_dict()

    decision = retrieval_release_decision(
        chunk_keyword=evaluations["Chunk-Keyword"],
        hybrid=evaluations["Hybrid-RRF"],
        legacy_hybrid=legacy_hybrid,
        causal_hybrid=evaluations["Hybrid-RRF"],
        reranked=reranked_evaluation,
    )
    data_gate = _data_gate(catalog)
    selected_evaluation = evaluations[
        "Hybrid-RRF"
        if decision["selected_runtime"]["retriever"] == "hybrid_rrf"
        else "Chunk-Keyword"
    ]
    overreach = _historical_overreach(selected_evaluation)
    governance_passed = bool(
        all(item["metrics"]["unapproved_hit_count"] == 0 for item in evaluations.values())
        and overreach["historical_overreach_rate"] == 0
        and _scope_boundaries_hold(audits["Hybrid-RRF"], query_contexts, test)
    )
    order = ["Chunk-Keyword", "BM25-only", "Vector-only", "Hybrid-RRF"]
    if reranked_evaluation is not None:
        order.append("Hybrid-RRF+CrossEncoder")
    failed_cases = []
    for name in order:
        for row in evaluations[name]["results"]:
            metrics = row["per_query_metrics"]
            failed = []
            if row["no_answer"]:
                if not metrics["no_answer_correct"]:
                    failed.append("no_answer")
            else:
                if float(metrics["recall_at_5"]) < 1.0:
                    failed.append("recall_at_5")
                if metrics["hard_negative_pairwise_win_rate"] is not None and float(
                    metrics["hard_negative_pairwise_win_rate"]
                ) < 1.0:
                    failed.append("hard_negative_pairwise")
            if failed:
                failed_cases.append(
                    {
                        "retriever": name,
                        "query_id": row["query_id"],
                        "incident_family_id": row["incident_family_id"],
                        "failed_metrics": failed,
                        "final_asset_ids": row["final_asset_ids"],
                    }
                )
    legacy_slices = legacy_hybrid["slices"]["causal_scope"]
    causal_slices = evaluations["Hybrid-RRF"]["slices"]["causal_scope"]
    result: dict[str, Any] = {
        "schema_version": "2.0",
        "dataset_id": "synthetic-semiconductor-causal-v2",
        "synthetic": True,
        "partitions": {
            "policy": partitions["partition_key"],
            "calibration_query_count": len(calibration.queries),
            "test_query_count": len(test.queries),
            "overlap_count": 0,
        },
        "embedding": {
            "backend": args.embedding_backend,
            "model_name": embedding.model_name,
            "model_revision": embedding.model_revision,
            "device": embedding.device,
        },
        "calibrations": calibrations,
        "reranker_calibration": reranker_calibration,
        "retrieval": {
            "order": order,
            "evaluations": evaluations,
            "scope_ablation": {
                "legacy_observed_module": {
                    "same_module": legacy_slices["same_module"]["recall_at_5"],
                    "cross_module": legacy_slices["cross_module"]["recall_at_5"],
                    "evaluation": legacy_hybrid,
                },
                "causal_wide": {
                    "same_module": causal_slices["same_module"]["recall_at_5"],
                    "cross_module": causal_slices["cross_module"]["recall_at_5"],
                    "evaluation": evaluations["Hybrid-RRF"],
                },
            },
            "release_decision": decision,
        },
        "scope_audits": audits,
        "historical_overreach": overreach,
        "gates": {
            "data_quality": data_gate,
            "governance": {
                "status": "PASS" if governance_passed else "FAIL",
                "unapproved_knowledge_leakage": sum(
                    item["metrics"]["unapproved_hit_count"]
                    for item in evaluations.values()
                ),
                "historical_overreach_rate": overreach["historical_overreach_rate"],
                "source_and_time_scope_boundaries": _scope_boundaries_hold(
                    audits["Hybrid-RRF"], query_contexts, test
                ),
            },
            "retrieval_quality": {
                "status": "PASS" if decision["passed"] else "FAIL",
                **decision,
            },
        },
        "failed_cases": failed_cases,
    }
    result["passed"] = bool(
        data_gate["status"] == "PASS" and governance_passed and decision["passed"]
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(_report(result), encoding="utf-8")
    (args.output_dir / "failed_cases.json").write_text(
        json.dumps(failed_cases, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--partitions", type=Path, default=DEFAULT_PARTITIONS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--embedding-backend", choices=("sentence-transformers", "deterministic"),
        default="sentence-transformers",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-revision", default=DEFAULT_EMBEDDING_REVISION)
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--evaluate-reranker", action="store_true")
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER_MODEL)
    parser.add_argument("--reranker-revision", default=DEFAULT_RERANKER_REVISION)
    parser.add_argument("--reranker-local-path", type=Path)
    parser.add_argument("--reranker-batch-size", type=int, default=16)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    hybrid = result["retrieval"]["evaluations"]["Hybrid-RRF"]["metrics"]
    print(
        "Evaluation V2 retrieval: "
        f"{'PASS' if result['passed'] else 'FAIL'}; "
        f"test_queries={result['partitions']['test_query_count']}; "
        f"Recall@5={hybrid['recall_at_5']:.2%}; "
        f"nDCG@10={hybrid['ndcg_at_10']:.4f}; "
        f"pairwise={hybrid['hard_negative_pairwise_win_rate']:.2%}; "
        f"no_answer={hybrid['no_answer_accuracy']:.2%}; "
        f"causal_scope={result['retrieval']['release_decision']['causal_scope']['promoted']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
