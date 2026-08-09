"""Generate independent Retrieval V2 and RCA V2 Synthetic data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.evaluation_v2_data import (  # noqa: E402
    QwenSurfaceQueryProvider,
    TemplateSurfaceQueryProvider,
    build_evaluation_v2_dataset,
    data_quality_markdown,
    default_incident_catalog,
    human_review_packet_markdown,
    load_incident_catalog,
    validate_evaluation_v2_dataset,
    write_evaluation_v2_dataset,
)
from yield_rca_core.llm_gateway import LLMSettings, build_llm_client  # noqa: E402

DEFAULT_CATALOG = ROOT / "data" / "evaluation" / "incident_families_v2.json"
DEFAULT_KNOWLEDGE_DIR = ROOT / "data" / "knowledge" / "synthetic_v2"
DEFAULT_EVALUATION_DIR = ROOT / "data" / "evaluation"
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "causal_scope_v2"
DEFAULT_REPORT_DIR = ROOT / "outputs" / "evaluation_v2_data_quality"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Generate isolated Synthetic V2 data and stop at the human review checkpoint.")
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--evaluation-dir", type=Path, default=DEFAULT_EVALUATION_DIR)
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--provider", choices=("template", "qwen"), default="template")
    parser.add_argument("--confirm-paid-qwen", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-paid-calls", type=int, default=8)
    parser.add_argument(
        "--refresh-default-catalog",
        action="store_true",
        help="Replace the catalog with the reviewed built-in blueprint before generation.",
    )
    parser.add_argument(
        "--overwrite-reviews",
        action="store_true",
        help="Reset existing human review decisions to PENDING.",
    )
    return parser


def _provider(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    if args.provider == "template":
        return TemplateSurfaceQueryProvider()
    if not args.confirm_paid_qwen:
        raise ValueError("paid Qwen wording requires --confirm-paid-qwen")
    settings = LLMSettings(
        agent_mode="llm",
        api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
        model=os.getenv("YIELD_RCA_LLM_MODEL", "qwen-plus").strip() or "qwen-plus",
        base_url=os.getenv(
            "YIELD_RCA_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip(),
        timeout_seconds=float(os.getenv("YIELD_RCA_LLM_TIMEOUT_SECONDS", "60")),
        max_retries=0,
    )
    client = build_llm_client(settings)
    if client is None:
        raise ValueError("Qwen client is not configured")
    return QwenSurfaceQueryProvider(
        client,
        batch_size=args.batch_size,
        max_paid_calls=args.max_paid_calls,
    )


def main() -> int:
    args = build_parser().parse_args()
    catalog = (
        default_incident_catalog()
        if args.refresh_default_catalog or not args.catalog.exists()
        else load_incident_catalog(args.catalog)
    )
    provider = _provider(args)
    built = build_evaluation_v2_dataset(catalog, provider)
    write_evaluation_v2_dataset(
        built,
        knowledge_dir=args.knowledge_dir,
        evaluation_dir=args.evaluation_dir,
        seed_dir=args.seed_dir,
        preserve_reviews=not args.overwrite_reviews,
    )
    # Regeneration preserves human decisions on disk, so report those persisted
    # decisions rather than the fresh in-memory PENDING templates.
    built["qrel_review"] = json.loads(
        (args.evaluation_dir / "retrieval_qrel_review_v2.json").read_text(encoding="utf-8")
    )
    built["scenario_review"] = json.loads(
        (args.evaluation_dir / "rca_scenario_review_v2.json").read_text(encoding="utf-8")
    )
    report = validate_evaluation_v2_dataset(built)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "results.json").write_text(
        json.dumps(
            {
                "structural_pass": report.structural_pass,
                "human_review_complete": report.human_review_complete,
                "metrics": report.metrics,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.report_dir / "report.md").write_text(data_quality_markdown(report), encoding="utf-8")
    (args.report_dir / "human_review_packet.md").write_text(
        human_review_packet_markdown(built), encoding="utf-8"
    )
    status = "PASS" if report.structural_pass else "FAIL"
    review = "COMPLETE" if report.human_review_complete else "PENDING"
    print(
        "Evaluation V2 data checkpoint: "
        f"structural={status}; human_review={review}; "
        f"retrieval_queries={report.metrics['retrieval_queries']}; "
        f"rca_scenarios={report.metrics['rca_scenarios']}; "
        f"cross_module={report.metrics['cross_module_supported_ratio']:.2%}; "
        f"paid_calls={provider.paid_call_count}"
    )
    print(f"Report: {args.report_dir / 'report.md'}")
    return 0 if report.structural_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
