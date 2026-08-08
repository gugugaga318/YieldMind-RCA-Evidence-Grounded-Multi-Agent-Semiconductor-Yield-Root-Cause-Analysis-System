"""Run Keyword/BM25/Vector/Hybrid ablation on one governed ground truth."""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from run_retrieval_evaluation import _asset_statuses, _load_manifest  # noqa: E402
from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    BM25DocumentChunkRetriever,
    DeterministicHashEmbeddingBackend,
    EmbeddingBackend,
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    PythonBM25CandidateSource,
    SentenceTransformerEmbeddingBackend,
    VectorDocumentChunkRetriever,
)
from yield_rca_core.knowledge_lookup import DocumentChunkKeywordRetriever  # noqa: E402
from yield_rca_core.knowledge_retrieval import (  # noqa: E402
    KeywordRetriever,
    KnowledgeAssetRepository,
)
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.retrieval_evaluation import (  # noqa: E402
    KeywordRetrieverEvaluationBackend,
    KnowledgeLookupRetrieverEvaluationBackend,
    RetrievalEvaluationBackend,
    RetrievalGroundTruth,
    evaluate_retrieval,
    render_retrieval_ablation_report,
)

DEFAULT_GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
DEFAULT_CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "hybrid_retrieval_evaluation"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def _embedding_runtime(embedding_backend: EmbeddingBackend) -> dict[str, str | None]:
    if not isinstance(embedding_backend, SentenceTransformerEmbeddingBackend):
        return {
            "backend": "builtin",
            "sentence_transformers_version": None,
            "torch_version": None,
            "cuda_runtime": None,
        }
    torch = import_module("torch")
    return {
        "backend": "sentence-transformers",
        "sentence_transformers_version": version("sentence-transformers"),
        "torch_version": str(torch.__version__),
        "cuda_runtime": str(torch.version.cuda) if torch.version.cuda else None,
    }


def _build_backends(
    corpus_dir: Path,
    embedding_backend: EmbeddingBackend,
    *,
    query_texts: tuple[str, ...],
) -> list[RetrievalEvaluationBackend]:
    repository = CsvFabRepository(corpus_dir)
    store = load_builtin_knowledge_store(corpus_dir / "corpus.json")
    lexical_source = PythonBM25CandidateSource(store)
    vector_source = ExactVectorCandidateSource(store, embedding_backend)
    vector_source.prepare_queries(query_texts)
    return [
        KeywordRetrieverEvaluationBackend(
            KeywordRetriever(KnowledgeAssetRepository(repository)),
            name="Legacy-Case-Keyword",
        ),
        KnowledgeLookupRetrieverEvaluationBackend(
            "Chunk-Keyword",
            DocumentChunkKeywordRetriever(store),
        ),
        KnowledgeLookupRetrieverEvaluationBackend(
            "BM25-only",
            BM25DocumentChunkRetriever(lexical_source),
        ),
        KnowledgeLookupRetrieverEvaluationBackend(
            "Vector-only",
            VectorDocumentChunkRetriever(vector_source),
        ),
        KnowledgeLookupRetrieverEvaluationBackend(
            "Hybrid-RRF",
            HybridDocumentChunkRetriever(lexical_source, vector_source),
        ),
    ]


def run_hybrid_retrieval_evaluation(
    ground_truth_path: Path,
    corpus_dir: Path,
    output_dir: Path,
    *,
    embedding_backend: EmbeddingBackend,
    requested_device: str,
) -> dict[str, Any]:
    ground_truth = RetrievalGroundTruth.load(ground_truth_path)
    manifest = _load_manifest(corpus_dir)
    if manifest.get("corpus_version") != ground_truth.corpus_version:
        raise ValueError("ground truth and corpus manifest versions do not match")
    statuses = _asset_statuses(manifest)
    backends = _build_backends(
        corpus_dir,
        embedding_backend,
        query_texts=tuple(item.text for item in ground_truth.queries),
    )
    evaluations = {
        backend.name: evaluate_retrieval(
            ground_truth,
            backend,
            asset_statuses=statuses,
        )
        for backend in backends
    }
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "corpus_version": ground_truth.corpus_version,
        "order": [backend.name for backend in backends],
        "embedding": {
            "model_name": embedding_backend.model_name,
            "model_revision": embedding_backend.model_revision,
            "requested_device": requested_device,
            "resolved_device": embedding_backend.device,
            "exact_vector_search": True,
            "query_embeddings_precomputed": True,
            "runtime": _embedding_runtime(embedding_backend),
        },
        "passed": all(item["passed"] for item in evaluations.values()),
        "acceptance": {
            "same_ground_truth_for_all_retrievers": True,
            "unapproved_knowledge_leakage_gate": all(
                item["metrics"]["unapproved_hit_count"] == 0
                for item in evaluations.values()
            ),
            "online_retriever_cutover": False,
        },
        "evaluations": evaluations,
    }
    # SentenceTransformer resolves device lazily during Vector evaluation.
    result["embedding"]["resolved_device"] = embedding_backend.device
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_retrieval_ablation_report(result),
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run governed Hybrid retrieval ablation.")
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--embedding-backend",
        choices=("sentence-transformers", "deterministic-hash"),
        default="sentence-transformers",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-revision", default=DEFAULT_EMBEDDING_REVISION)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    embedding_backend: EmbeddingBackend
    if args.embedding_backend == "deterministic-hash":
        embedding_backend = DeterministicHashEmbeddingBackend()
    else:
        embedding_backend = SentenceTransformerEmbeddingBackend(
            args.embedding_model,
            device=args.device,
            batch_size=args.batch_size,
            revision=args.embedding_revision,
        )
    result = run_hybrid_retrieval_evaluation(
        args.ground_truth,
        args.corpus_dir,
        args.output_dir,
        embedding_backend=embedding_backend,
        requested_device=args.device,
    )
    print(
        "Hybrid retrieval ablation: "
        f"{'PASS' if result['passed'] else 'FAIL'}; "
        + "; ".join(
            f"{name} Recall@5={result['evaluations'][name]['metrics']['recall_at_5']:.1%}"
            for name in result["order"]
        )
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
