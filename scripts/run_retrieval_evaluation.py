"""Run the independent KeywordRetriever baseline against governed qrels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.knowledge_retrieval import (  # noqa: E402
    KeywordRetriever,
    KnowledgeAssetRepository,
)
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.retrieval_evaluation import (  # noqa: E402
    KeywordRetrieverEvaluationBackend,
    RetrievalGroundTruth,
    evaluate_retrieval,
    render_retrieval_evaluation_report,
)

DEFAULT_GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"
DEFAULT_CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "retrieval_evaluation"


def _load_manifest(corpus_dir: Path) -> dict[str, Any]:
    manifest = json.loads((corpus_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("generation manifest must be a JSON object")
    return manifest


def _asset_statuses(manifest: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for raw in manifest.get("assets", []):
        item = dict(raw)
        asset_id = str(item.get("asset_id", "")).strip()
        status = str(item.get("validation_status", "")).strip().upper()
        if not asset_id or not status:
            raise ValueError("manifest asset requires asset_id and validation_status")
        if asset_id in statuses:
            raise ValueError(f"duplicate manifest asset ID: {asset_id}")
        statuses[asset_id] = status
    if not statuses:
        raise ValueError("generation manifest contains no assets")
    return statuses


def run_retrieval_evaluation(
    ground_truth_path: Path,
    corpus_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    ground_truth = RetrievalGroundTruth.load(ground_truth_path)
    manifest = _load_manifest(corpus_dir)
    if manifest.get("corpus_version") != ground_truth.corpus_version:
        raise ValueError("ground truth and corpus manifest versions do not match")

    repository = CsvFabRepository(corpus_dir)
    backend = KeywordRetrieverEvaluationBackend(
        KeywordRetriever(KnowledgeAssetRepository(repository))
    )
    evaluation: dict[str, Any] = evaluate_retrieval(
        ground_truth,
        backend,
        asset_statuses=_asset_statuses(manifest),
    )
    evaluation["corpus"] = {
        "synthetic": bool(manifest.get("synthetic")),
        "publication_policy": manifest.get("publication_policy"),
        "counts": manifest.get("counts", {}),
        "generation": manifest.get("generation", {}),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_retrieval_evaluation_report(evaluation),
        encoding="utf-8",
    )
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the independent Synthetic knowledge retrieval baseline."
    )
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    evaluation = run_retrieval_evaluation(
        args.ground_truth,
        args.corpus_dir,
        args.output_dir,
    )
    metrics = evaluation["metrics"]
    print(
        "Keyword retrieval baseline: "
        f"{'PASS' if evaluation['passed'] else 'FAIL'}; "
        f"queries={metrics['query_count']}; "
        f"recall@5={metrics['recall_at_5']:.1%}; "
        f"candidate_recall@20={metrics['candidate_recall_at_20']:.1%}; "
        f"mrr@10={metrics['mrr_at_10']:.3f}; "
        f"ndcg@10={metrics['ndcg_at_10']:.3f}; "
        f"cross_language_recall@5={metrics['cross_language_recall_at_5']:.1%}; "
        f"no_answer_accuracy={metrics['no_answer_accuracy']:.1%}; "
        f"unapproved_hits={metrics['unapproved_hit_count']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
