"""Combine Evaluation V2 data, retrieval, governance, and RCA release gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_RESULTS = ROOT / "outputs" / "evaluation_v2_data_quality" / "results.json"
DEFAULT_RETRIEVAL_RESULTS = (
    ROOT / "outputs" / "evaluation_v2_release" / "retrieval" / "results.json"
)
DEFAULT_RCA_RESULTS = ROOT / "outputs" / "evaluation_v2_release" / "rca" / "results.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "evaluation_v2_release"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _data_gate(data: dict[str, Any]) -> dict[str, Any]:
    passed = bool(data["structural_pass"] and data["human_review_complete"])
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "structural_pass": bool(data["structural_pass"]),
        "human_review_complete": bool(data["human_review_complete"]),
        "pending_qrel_reviews": int(data["metrics"]["pending_qrel_reviews"]),
        "pending_scenario_reviews": int(data["metrics"]["pending_scenario_reviews"]),
        "errors": list(data["errors"]),
    }


def _governance_gate(
    retrieval: dict[str, Any],
    rca: dict[str, Any],
) -> dict[str, Any]:
    retrieval_gate = retrieval["gates"]["governance"]
    rca_gate = rca["gates"]["governance"]
    passed = retrieval_gate["status"] == "PASS" and rca_gate["status"] == "PASS"
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "unapproved_knowledge_leakage": int(
            retrieval_gate["unapproved_knowledge_leakage"]
        )
        + int(rca_gate["unapproved_knowledge_leakage"]),
        "historical_overreach_rate": float(
            retrieval_gate["historical_overreach_rate"]
        ),
        "historical_only_root_cause_promotions": int(
            rca_gate["historical_only_root_cause_promotions"]
        ),
        "unsupported_source_recall": float(rca_gate["unsupported_source_recall"]),
        "source_and_time_scope_boundaries": bool(
            retrieval_gate["source_and_time_scope_boundaries"]
        ),
    }


def _interview_claims(
    retrieval: dict[str, Any],
    rca: dict[str, Any],
) -> list[dict[str, str]]:
    scope = retrieval["retrieval"]["scope_ablation"]
    hybrid = retrieval["retrieval"]["evaluations"]["Hybrid-RRF"]["metrics"]
    fixed_test = rca["modes"]["fixed"]["partitions"]["test"]
    return [
        {
            "claim": (
                "Four-lane causal Scope raised cross-Module Recall@5 from "
                f"{scope['legacy_observed_module']['cross_module']:.2%} to "
                f"{scope['causal_wide']['cross_module']:.2%}, while same-Module "
                f"Recall@5 remained {scope['causal_wide']['same_module']:.2%}."
            ),
            "boundary": "Reviewed Synthetic V2 retrieval Test partition only.",
        },
        {
            "claim": (
                f"Hybrid-RRF measured Recall@5 {hybrid['recall_at_5']:.2%}, "
                f"nDCG@10 {hybrid['ndcg_at_10']:.4f}, hard-negative pairwise "
                f"{hybrid['hard_negative_pairwise_win_rate']:.2%}, and in-scope "
                f"No-answer {hybrid['no_answer_accuracy']:.2%}."
            ),
            "boundary": (
                "Hybrid failed its non-regression gate and is not the selected runtime."
            ),
        },
        {
            "claim": (
                "The deterministic fixed reference achieved "
                f"{fixed_test['root_cause_correct_count']}/"
                f"{fixed_test['supported_scenario_count']} supported root causes, "
                f"{fixed_test['impact_lot_precision']:.2%} Impact precision, "
                f"{fixed_test['impact_lot_recall']:.2%} Impact recall, and "
                f"{fixed_test['correct_abstention_rate']:.2%} correct abstention."
            ),
            "boundary": (
                "Reviewed Synthetic V2 RCA Test partition; this is a deterministic "
                "reference, not a real-Qwen or production-Fab accuracy claim."
            ),
        },
    ]


def _report(result: dict[str, Any]) -> str:
    gates = result["gates"]
    runtime = result["release_decision"]["selected_runtime"]
    lines = [
        "# Evaluation V2 Final Release Decision",
        "",
        "> Synthetic benchmark only. This report is not a production-Fab accuracy claim.",
        "",
        f"- Release status: **{result['release_status']}**",
        f"- Data Quality gate: **{gates['data_quality']['status']}**",
        f"- Governance gate: **{gates['governance']['status']}**",
        f"- Retrieval Quality gate: **{gates['retrieval_quality']['status']}**",
        f"- RCA Quality gate: **{gates['rca_quality']['status']}**",
        "",
        "## Selected runtime",
        "",
        f"- Retriever: `{runtime['retriever']}`",
        f"- Causal Scope: `{'enabled' if runtime['causal_scope_enabled'] else 'disabled'}`",
        f"- Reranker: `{'enabled' if runtime['reranker_enabled'] else 'disabled'}`",
        f"- Reranker reason: {result['release_decision']['reranker']['reason']}",
        "",
        "## Measured, interview-safe claims",
        "",
    ]
    for item in result["interview_safe_claims"]:
        lines.extend([f"- {item['claim']}", f"  Boundary: {item['boundary']}"])
    lines.extend(
        [
            "",
            "## Blocking decisions",
            "",
            "- Hybrid-RRF is implemented but not promoted because it regressed the "
            "hard-negative pairwise metric against Chunk Keyword.",
            "- The optional Reranker was not measured with an available local model, so "
            "it remains disabled behind its Feature Flag.",
            "- The RCA gate remains BLOCKED until the explicitly paid, capped real-Qwen "
            "llm_react Test partition completes without fallback.",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = _load(args.data_results)
    retrieval = _load(args.retrieval_results)
    rca = _load(args.rca_results)
    data_gate = _data_gate(data)
    governance = _governance_gate(retrieval, rca)
    retrieval_gate = dict(retrieval["gates"]["retrieval_quality"])
    rca_gate = dict(rca["gates"]["rca_quality"])
    gates = {
        "data_quality": data_gate,
        "governance": governance,
        "retrieval_quality": retrieval_gate,
        "rca_quality": rca_gate,
    }
    statuses = {gate["status"] for gate in gates.values()}
    release_status = "PASS" if statuses == {"PASS"} else "NOT_READY"
    reranker = retrieval["retrieval"]["release_decision"]["reranker"]
    result: dict[str, Any] = {
        "dataset_id": str(retrieval["dataset_id"]),
        "synthetic": True,
        "release_status": release_status,
        "passed": release_status == "PASS",
        "gates": gates,
        "release_decision": {
            "selected_runtime": retrieval["retrieval"]["release_decision"][
                "selected_runtime"
            ],
            "causal_scope": retrieval["retrieval"]["release_decision"][
                "causal_scope"
            ],
            "hybrid_promoted": bool(
                retrieval["retrieval"]["release_decision"]["hybrid_non_regression"]
            ),
            "reranker": {
                **reranker,
                "enabled": False,
                "reason": (
                    "Local bge-reranker-v2-m3 weights were unavailable and no strict "
                    "measured nDCG uplift was established."
                    if not reranker["evaluated"]
                    else "Measured promotion criteria were not met."
                ),
            },
        },
        "interview_safe_claims": _interview_claims(retrieval, rca),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(_report(result), encoding="utf-8")
    (args.output_dir / "release_decision.json").write_text(
        json.dumps(result["release_decision"], ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-results", type=Path, default=DEFAULT_DATA_RESULTS)
    parser.add_argument(
        "--retrieval-results", type=Path, default=DEFAULT_RETRIEVAL_RESULTS
    )
    parser.add_argument("--rca-results", type=Path, default=DEFAULT_RCA_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    print(
        "Evaluation V2 release: "
        f"{result['release_status']}; "
        + "; ".join(
            f"{name}={gate['status']}" for name, gate in result["gates"].items()
        )
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    # NOT_READY is an evaluated release decision, not a runner failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
