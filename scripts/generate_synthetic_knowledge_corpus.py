"""Generate the governed Synthetic knowledge corpus and retrieval ground truth."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.llm_gateway import LLMSettings, build_llm_client  # noqa: E402
from yield_rca_core.synthetic_knowledge import (  # noqa: E402
    QwenSyntheticKnowledgeTextProvider,
    SyntheticKnowledgeError,
    TemplateSyntheticKnowledgeTextProvider,
    build_synthetic_knowledge_corpus,
    canonical_file_sha256,
    load_canonical_facts,
    write_synthetic_knowledge_corpus,
)

DEFAULT_CANONICAL = ROOT / "data" / "knowledge" / "synthetic_v1" / "canonical_facts.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
DEFAULT_GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Synthetic knowledge and deterministic retrieval qrels."
    )
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--provider", choices=("template", "qwen"), default="template")
    parser.add_argument("--confirm-paid-qwen", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-paid-calls", type=int, default=20)
    return parser


def _provider(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    if args.provider == "template":
        return TemplateSyntheticKnowledgeTextProvider()
    if not args.confirm_paid_qwen:
        raise SyntheticKnowledgeError(
            "Qwen generation can make paid calls; pass --confirm-paid-qwen explicitly"
        )
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
        raise SyntheticKnowledgeError("Qwen client was not configured")
    return QwenSyntheticKnowledgeTextProvider(
        client,
        batch_size=args.batch_size,
        max_paid_calls=args.max_paid_calls,
    )


def main() -> int:
    args = build_parser().parse_args()
    canonical = load_canonical_facts(args.canonical)
    provider = _provider(args)
    built = build_synthetic_knowledge_corpus(
        canonical,
        provider,
        canonical_sha256=canonical_file_sha256(args.canonical),
    )
    write_synthetic_knowledge_corpus(
        built,
        output_dir=args.output_dir,
        ground_truth_path=args.ground_truth,
    )
    counts = built["manifest"]["counts"]
    print(
        "Synthetic knowledge corpus generated: "
        f"provider={provider.provider_name}; "
        f"rca={counts['confirmed_rca_cases']}; "
        f"sop={counts['confirmed_sops']}; "
        f"notes={counts['confirmed_engineering_notes']}; "
        f"queries={counts['total_queries']}; "
        f"paid_calls={provider.paid_call_count}"
    )
    print(f"Corpus:       {args.output_dir}")
    print(f"Ground truth: {args.ground_truth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
