"""Evaluate the pinned Hybrid/Reranker cutover on an untouched test split."""

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

from run_retrieval_evaluation import _asset_statuses, _load_manifest  # noqa: E402
from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    PythonBM25CandidateSource,
    SentenceTransformerEmbeddingBackend,
)
from yield_rca_core.hypothesis_engine import HypothesisEngine  # noqa: E402
from yield_rca_core.knowledge_lookup import KnowledgeLookupService  # noqa: E402
from yield_rca_core.knowledge_models import (  # noqa: E402
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.models import AgentFinding, AgentKind, FindingKind  # noqa: E402
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
from yield_rca_core.retrieval_evaluation import (  # noqa: E402
    KnowledgeLookupRetrieverEvaluationBackend,
    RetrievalEvaluationQuery,
    RetrievalGroundTruth,
    evaluate_retrieval,
)

DEFAULT_GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
DEFAULT_SPLIT = ROOT / "data" / "evaluation" / "retrieval_calibration_split.json"
DEFAULT_CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "long_task_4_evaluation"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


class CachedRerankerBackend:
    """Persist raw logits so an interrupted paid-GPU evaluation can resume."""

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
        return {str(key): float(value) for key, value in payload.get("logits", {}).items()}

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


def _plan(query: RetrievalEvaluationQuery, top_k: int = 20) -> KnowledgeLookupPlan:
    kind = KnowledgeQuestionKind(query.question_kind)
    return KnowledgeLookupPlan(
        intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
        question_kind=kind.value,
        query=query.text,
        allowed_document_types=(kind.document_type,),
        reason="Fixed offline calibration/evaluation scope.",
        module=query.module,
        equipment_type=query.equipment_type,
        top_k=top_k,
    )


def _load_partitions(
    ground_truth_path: Path,
    split_path: Path,
) -> tuple[RetrievalGroundTruth, RetrievalGroundTruth, dict[str, Any]]:
    ground_truth = RetrievalGroundTruth.load(ground_truth_path)
    split = json.loads(split_path.read_text(encoding="utf-8"))
    if split.get("corpus_version") != ground_truth.corpus_version:
        raise ValueError("calibration split and ground truth corpus versions differ")
    calibration_ids = {str(item) for item in split["calibration_query_ids"]}
    all_ids = {item.query_id for item in ground_truth.queries}
    calibration = ground_truth.subset(calibration_ids)
    test = ground_truth.subset(all_ids - calibration_ids)
    return calibration, test, split


def _calibration_pairs(
    ground_truth: RetrievalGroundTruth,
    hybrid: HybridDocumentChunkRetriever,
    reranker: RerankerBackend,
) -> tuple[list[float], list[int]]:
    logits: list[float] = []
    labels: list[int] = []
    for query in ground_truth.queries:
        hits = hybrid.retrieve(_plan(query), lookup_id=f"KLOOK_CAL_{query.query_id}")
        documents = tuple(reranker_document_text(hit) for hit in hits)
        scores = reranker.score_logits(query.text, documents)
        judgments = {
            item.asset_id: item.relevance for item in ground_truth.qrels[query.query_id]
        }
        for hit, score in zip(hits, scores, strict=True):
            logits.append(score)
            labels.append(
                int(
                    judgments.get(hit.document.evaluation_asset_id, 0)
                    >= ground_truth.relevance_threshold
                )
            )
    return logits, labels


def _historical_overreach(
    evaluation: dict[str, Any],
) -> dict[str, int | float]:
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
            finding_id=f"FIND_OVERREACH_{evaluated:03d}",
            agent=AgentKind.KNOWLEDGE.value,
            finding_kind=FindingKind.KNOWLEDGE_DISCOVERY.value,
            summary="Historical-only retrieval probe.",
            confidence=1.0,
            evidence_ids=[f"KEV_OVERREACH_{evaluated:03d}"],
            details={
                "cases": [
                    {"root_cause": final_ids[0], "similarity": 1.0}
                ],
                "top_case": {"case_id": final_ids[0]},
            },
        )
        decision = engine.analyze(
            request_id=f"OVERREACH_{evaluated:03d}",
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


def _knowledge_agent_boundary(
    ground_truth: RetrievalGroundTruth,
    store: Any,
    retriever: Any,
) -> dict[str, Any]:
    service = KnowledgeLookupService(store, retriever)
    checks: list[dict[str, Any]] = []
    for kind in sorted({item.question_kind for item in ground_truth.queries}):
        query = next(
            item
            for item in ground_truth.queries
            if item.question_kind == kind and not item.no_answer
        )
        result = service.lookup(
            query=query.text,
            question_kind=query.question_kind,
            module=query.module,
            equipment_type=query.equipment_type,
            top_k=5,
        )
        payload = result.to_dict()
        checks.append(
            {
                "question_kind": kind,
                "status": result.status,
                "agent_trace_count": len(result.agent_trace),
                "agents": [item.agent for item in result.agent_trace],
                "root_cause_conclusion": payload["root_cause_conclusion"],
                "passed": (
                    len(result.agent_trace) == 1
                    and result.agent_trace[0].agent == "knowledge"
                    and payload["root_cause_conclusion"] is None
                ),
            }
        )
    return {
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def _report(result: dict[str, Any]) -> str:
    hybrid = result["retrieval"]["hybrid"]["metrics"]
    reranked = result["retrieval"]["reranked"]["metrics"]
    decision = result["reranker_release_decision"]
    overreach = result["historical_overreach"]
    return "\n".join(
        [
            "# Long Task 4 Final Evaluation",
            "",
            f"- Overall: `{'PASS' if result['passed'] else 'FAIL'}`",
            f"- Calibration queries: {result['partitions']['calibration_query_count']}",
            f"- Untouched test queries: {result['partitions']['test_query_count']}",
            f"- Reranker recommended: `{'YES' if decision['recommended_enabled'] else 'NO'}`",
            f"- Selected online strategy: `{result['deployment_decision']['selected_strategy']}`",
            "",
            "## Retrieval comparison",
            "",
            "| Retriever | Recall@5 | MRR@10 | nDCG@10 | Hard-negative | No-answer | Leakage |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| Hybrid-RRF | {hybrid['recall_at_5']:.2%} | {hybrid['mrr_at_10']:.4f} | "
            f"{hybrid['ndcg_at_10']:.4f} | {hybrid['hard_negative_accuracy']:.2%} | "
            f"{hybrid['no_answer_accuracy']:.2%} | {hybrid['unapproved_hit_count']} |",
            f"| Hybrid + CrossEncoder | {reranked['recall_at_5']:.2%} | "
            f"{reranked['mrr_at_10']:.4f} | {reranked['ndcg_at_10']:.4f} | "
            f"{reranked['hard_negative_accuracy']:.2%} | "
            f"{reranked['no_answer_accuracy']:.2%} | "
            f"{reranked['unapproved_hit_count']} |",
            "",
            "## Release boundaries",
            "",
            f"- nDCG strictly improved: `{decision['ndcg_strictly_improved']}`",
            f"- Core metrics did not regress: `{decision['core_metrics_non_regressing']}`",
            f"- Unapproved knowledge leakage: `{reranked['unapproved_hit_count']}`",
            f"- Historical Overreach Rate: {overreach['historical_overreach_rate']:.2%}",
            f"- Independent Knowledge lookup uses only Knowledge Agent and returns no RCA "
            f"conclusion: `{result['knowledge_agent_boundary']['passed']}`",
            f"- Feature Flag decision honored: "
            f"`{result['deployment_decision']['feature_flag_decision_honored']}`",
            "",
            "Calibration was fitted only on the fixed calibration IDs. All reported ranking "
            "metrics use the disjoint test IDs. Retrieval relevance, calibrated relevance, "
            "source confidence, and RCA conclusion confidence remain separate contracts.",
            "",
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    calibration, test, split = _load_partitions(args.ground_truth, args.split)
    manifest = _load_manifest(args.corpus_dir)
    statuses = _asset_statuses(manifest)
    store = load_builtin_knowledge_store(args.corpus_dir / "corpus.json")
    embedding = SentenceTransformerEmbeddingBackend(
        args.embedding_model,
        revision=args.embedding_revision,
        device=args.device,
        batch_size=args.embedding_batch_size,
    )
    vector = ExactVectorCandidateSource(store, embedding)
    vector.prepare_queries(
        tuple(query.text for query in (*calibration.queries, *test.queries))
    )
    hybrid = HybridDocumentChunkRetriever(PythonBM25CandidateSource(store), vector)
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
    logits, labels = _calibration_pairs(calibration, hybrid, cached_reranker)
    artifact = fit_platt_score_calibration(
        logits,
        labels,
        model_name=cached_reranker.model_name,
        model_revision=cached_reranker.model_revision,
        calibration_query_ids=tuple(item.query_id for item in calibration.queries),
    )
    calibrator = PlattScoreCalibrator(
        artifact,
        model_name=cached_reranker.model_name,
        model_revision=cached_reranker.model_revision,
    )
    reranked_retriever = RerankedKnowledgeRetriever(
        hybrid,
        cached_reranker,
        calibrator=calibrator,
    )
    hybrid_evaluation = evaluate_retrieval(
        test,
        KnowledgeLookupRetrieverEvaluationBackend("Hybrid-RRF", hybrid),
        asset_statuses=statuses,
    )
    reranked_evaluation = evaluate_retrieval(
        test,
        KnowledgeLookupRetrieverEvaluationBackend(
            "Hybrid-RRF+CrossEncoder",
            reranked_retriever,
        ),
        asset_statuses=statuses,
    )
    hybrid_metrics = hybrid_evaluation["metrics"]
    reranked_metrics = reranked_evaluation["metrics"]
    ndcg_improved = reranked_metrics["ndcg_at_10"] > hybrid_metrics["ndcg_at_10"]
    non_regressing = all(
        reranked_metrics[name] >= hybrid_metrics[name]
        for name in (
            "recall_at_5",
            "hard_negative_accuracy",
            "no_answer_accuracy",
        )
    )
    leakage_gate = (
        hybrid_metrics["unapproved_hit_count"] == 0
        and reranked_metrics["unapproved_hit_count"] == 0
    )
    decision = {
        "ndcg_strictly_improved": ndcg_improved,
        "core_metrics_non_regressing": non_regressing,
        "unapproved_knowledge_leakage_gate": leakage_gate,
        "recommended_enabled": ndcg_improved and non_regressing and leakage_gate,
    }
    selected = reranked_retriever if decision["recommended_enabled"] else hybrid
    overreach = _historical_overreach(reranked_evaluation)
    knowledge_boundary = _knowledge_agent_boundary(test, store, selected)
    deployment_decision = {
        "selected_strategy": (
            "hybrid_rrf_cross_encoder"
            if decision["recommended_enabled"]
            else "hybrid_rrf"
        ),
        "reranker_feature_flag_enabled": decision["recommended_enabled"],
        "feature_flag_decision_honored": True,
        "reason": (
            "CrossEncoder passed the strict improvement and non-regression gates."
            if decision["recommended_enabled"]
            else "CrossEncoder did not strictly improve test nDCG; keep the Feature Flag off."
        ),
    }
    release_passed = (
        leakage_gate
        and overreach["historical_overreach_rate"] == 0
        and knowledge_boundary["passed"]
        and deployment_decision["feature_flag_decision_honored"]
    )
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "corpus_version": test.corpus_version,
        "partitions": {
            "policy": split["policy"],
            "calibration_query_count": len(calibration.queries),
            "test_query_count": len(test.queries),
            "calibration_query_ids": [item.query_id for item in calibration.queries],
            "overlap_count": 0,
        },
        "models": {
            "embedding": {
                "name": embedding.model_name,
                "revision": embedding.model_revision,
                "device": embedding.device,
            },
            "reranker": {
                "name": cached_reranker.model_name,
                "revision": cached_reranker.model_revision,
                "device": cached_reranker.device,
            },
        },
        "calibration": artifact.to_dict(),
        "retrieval": {
            "hybrid": hybrid_evaluation,
            "reranked": reranked_evaluation,
        },
        "reranker_release_decision": decision,
        "deployment_decision": deployment_decision,
        "historical_overreach": overreach,
        "knowledge_agent_boundary": knowledge_boundary,
        "passed": release_passed,
    }
    result["models"]["embedding"]["device"] = embedding.device
    result["models"]["reranker"]["device"] = cached_reranker.device
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "calibration_artifact.json").write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(_report(result), encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-revision", default=DEFAULT_EMBEDDING_REVISION)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER_MODEL)
    parser.add_argument("--reranker-revision", default=DEFAULT_RERANKER_REVISION)
    parser.add_argument("--reranker-local-path", type=Path)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--reranker-batch-size", type=int, default=16)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    metrics = result["retrieval"]["reranked"]["metrics"]
    print(
        "Long Task 4 final evaluation: "
        f"{'PASS' if result['passed'] else 'FAIL'}; "
        f"test_queries={metrics['query_count']}; "
        f"Recall@5={metrics['recall_at_5']:.2%}; "
        f"nDCG@10={metrics['ndcg_at_10']:.4f}; "
        f"reranker_enabled={result['reranker_release_decision']['recommended_enabled']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
