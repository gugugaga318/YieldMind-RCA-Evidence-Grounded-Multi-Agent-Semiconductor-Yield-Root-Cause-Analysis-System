"""Run reviewed Evaluation V2 end-to-end RCA and compatibility evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.causal_retrieval import CausalLaneKnowledgeRetriever  # noqa: E402
from yield_rca_core.evaluation_v2_rca import (  # noqa: E402
    RCAV2Scenario,
    evaluate_mode,
    evidence_type_catalog,
    governance_gate,
    load_scenarios,
    rca_quality_gate,
    render_report,
)
from yield_rca_core.investigation_models import OrchestrationMode  # noqa: E402
from yield_rca_core.knowledge_lookup import DocumentChunkKeywordRetriever  # noqa: E402
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    build_llm_client,
)
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

DEFAULT_SCENARIOS = ROOT / "data" / "evaluation" / "rca_scenarios_v2.json"
DEFAULT_INCIDENTS = ROOT / "data" / "evaluation" / "incident_families_v2.json"
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "causal_scope_v2"
DEFAULT_CORPUS = ROOT / "data" / "knowledge" / "synthetic_v2" / "corpus.json"
DEFAULT_RETRIEVAL_RESULTS = (
    ROOT / "outputs" / "evaluation_v2_release" / "retrieval" / "results.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "evaluation_v2_release" / "rca"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


class CappedLLMClient:
    """Enforce a hard paid-call boundary for one RCA scenario."""

    def __init__(self, delegate: LLMClient, *, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self.delegate = delegate
        self.max_calls = max_calls
        self.call_count = 0
        self.limit_exceeded = False
        self.provider = delegate.provider
        self.model = delegate.model

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if self.call_count >= self.max_calls:
            self.limit_exceeded = True
            raise LLMCallError(
                "Evaluation V2 Qwen scenario exceeded its paid LLM-call cap",
                failure_category="evaluation_call_cap",
            )
        self.call_count += 1
        return self.delegate.complete_json(request)


def _selected_retriever(
    corpus: Path,
    retrieval_results: dict[str, Any],
) -> CausalLaneKnowledgeRetriever:
    decision = retrieval_results["retrieval"]["release_decision"]
    selected = decision["selected_runtime"]
    if selected != {
        "causal_scope_enabled": True,
        "reranker_enabled": False,
        "retriever": "chunk_keyword",
    }:
        raise ValueError(
            "RCA V2 runner supports the measured Chunk Keyword + Causal Scope release "
            "decision only"
        )
    threshold = float(
        retrieval_results["calibrations"]["Chunk-Keyword"]["threshold"]
    )
    store = load_builtin_knowledge_store(corpus)
    return CausalLaneKnowledgeRetriever(
        DocumentChunkKeywordRetriever(store, abstain_threshold=threshold)
    )


def _deterministic_mode(
    scenarios: list[RCAV2Scenario],
    *,
    expected_types: dict[str, str],
    seed_dir: Path,
    retriever: CausalLaneKnowledgeRetriever,
    mode: str,
) -> dict[str, Any]:
    workflow = build_csv_workflow(
        seed_dir,
        orchestration_mode=mode,
        knowledge_retriever=retriever,
    )
    return evaluate_mode(
        scenarios,
        expected_types=expected_types,
        requested_mode=mode,
        run_scenario=lambda scenario: workflow.run(
            scenario.query,
            job_id=f"EVAL_V2_{mode.upper()}_{scenario.scenario_id}",
            lot_id=scenario.source_lot_id,
        ),
    )


def _real_qwen_mode(
    scenarios: list[RCAV2Scenario],
    *,
    expected_types: dict[str, str],
    seed_dir: Path,
    corpus: Path,
    retrieval_results: dict[str, Any],
    max_calls_per_scenario: int,
) -> dict[str, Any]:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY is required for --run-real-qwen")
    settings = LLMSettings(
        agent_mode="llm",
        api_key=api_key,
        model=os.getenv("YIELD_RCA_LLM_MODEL", "qwen-plus").strip() or "qwen-plus",
        base_url=os.getenv(
            "YIELD_RCA_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip(),
        timeout_seconds=float(os.getenv("YIELD_RCA_LLM_TIMEOUT_SECONDS", "60")),
        max_retries=0,
    )
    delegate = build_llm_client(settings)
    assert delegate is not None
    call_counts: dict[str, int] = {}
    cap_exceeded: dict[str, bool] = {}

    def run(scenario: RCAV2Scenario) -> Any:
        capped = CappedLLMClient(delegate, max_calls=max_calls_per_scenario)
        workflow = build_csv_workflow(
            seed_dir,
            llm_settings=settings,
            llm_client=capped,
            orchestration_mode=OrchestrationMode.LLM_REACT.value,
            knowledge_retriever=_selected_retriever(corpus, retrieval_results),
        )
        state = workflow.run(
            scenario.query,
            job_id=f"EVAL_V2_REAL_QWEN_{scenario.scenario_id}",
            lot_id=scenario.source_lot_id,
        )
        call_counts[scenario.scenario_id] = capped.call_count
        cap_exceeded[scenario.scenario_id] = capped.limit_exceeded
        return state

    evaluation = evaluate_mode(
        [item for item in scenarios if item.partition == "test"],
        expected_types=expected_types,
        requested_mode=OrchestrationMode.LLM_REACT.value,
        run_scenario=run,
    )
    evaluation["paid_call_boundary"] = {
        "max_calls_per_scenario": max_calls_per_scenario,
        "actual_calls_by_scenario": call_counts,
        "cap_exceeded_scenario_ids": sorted(
            scenario_id
            for scenario_id, exceeded in cap_exceeded.items()
            if exceeded
        ),
    }
    return evaluation


def run(args: argparse.Namespace) -> dict[str, Any]:
    scenario_catalog = _load(args.scenarios)
    incident_catalog = _load(args.incidents)
    retrieval_results = _load(args.retrieval_results)
    scenarios = load_scenarios(scenario_catalog, incident_catalog)
    expected_types = evidence_type_catalog(incident_catalog)
    selected_retriever = _selected_retriever(args.corpus, retrieval_results)

    fixed = _deterministic_mode(
        scenarios,
        expected_types=expected_types,
        seed_dir=args.seed_dir,
        retriever=selected_retriever,
        mode=OrchestrationMode.FIXED.value,
    )
    controlled = _deterministic_mode(
        scenarios,
        expected_types=expected_types,
        seed_dir=args.seed_dir,
        retriever=selected_retriever,
        mode=OrchestrationMode.CONTROLLED_REACT.value,
    )
    if args.run_real_qwen:
        if not args.confirm_paid_qwen:
            raise ValueError("--confirm-paid-qwen is required before paid model calls")
        real_qwen = _real_qwen_mode(
            scenarios,
            expected_types=expected_types,
            seed_dir=args.seed_dir,
            corpus=args.corpus,
            retrieval_results=retrieval_results,
            max_calls_per_scenario=args.max_qwen_calls_per_scenario,
        )
    else:
        real_qwen = {
            "status": "NOT_RUN",
            "reason": (
                "Paid real-Qwen evaluation requires both --run-real-qwen and "
                "--confirm-paid-qwen with DASHSCOPE_API_KEY."
            ),
        }

    unsupported_count = sum(bool(item.unavailable_data_sources) for item in scenarios)
    result: dict[str, Any] = {
        "dataset_id": str(scenario_catalog["dataset_id"]),
        "synthetic": True,
        "limitations": str(scenario_catalog["limitations"]),
        "selected_runtime": retrieval_results["retrieval"]["release_decision"][
            "selected_runtime"
        ],
        "partitions": {
            "calibration_scenario_count": sum(
                item.partition == "calibration" for item in scenarios
            ),
            "test_scenario_count": sum(item.partition == "test" for item in scenarios),
        },
        "modes": {
            "fixed": fixed,
            "controlled_react": controlled,
            "llm_react": real_qwen,
        },
        "gates": {
            "governance": governance_gate(
                fixed,
                expected_unsupported_scenarios=unsupported_count,
            ),
            "rca_quality": rca_quality_gate(
                real_qwen,
                fixed_reference=fixed,
                controlled_reference=controlled,
            ),
        },
    }
    result["passed"] = all(
        gate["status"] == "PASS" for gate in result["gates"].values()
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "report.md").write_text(
        render_report(result),
        encoding="utf-8",
    )
    failed_cases = {
        mode: evaluation.get("failed_scenario_ids", [])
        for mode, evaluation in result["modes"].items()
        if isinstance(evaluation, dict)
    }
    (args.output_dir / "failed_cases.json").write_text(
        json.dumps(failed_cases, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--incidents", type=Path, default=DEFAULT_INCIDENTS)
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--retrieval-results",
        type=Path,
        default=DEFAULT_RETRIEVAL_RESULTS,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-real-qwen", action="store_true")
    parser.add_argument("--confirm-paid-qwen", action="store_true")
    parser.add_argument("--max-qwen-calls-per-scenario", type=int, default=16)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    fixed = result["modes"]["fixed"]["partitions"]["test"]
    print(
        "Evaluation V2 RCA: "
        f"gate={result['gates']['rca_quality']['status']}; "
        f"fixed_root={fixed['root_cause_correctness']:.2%}; "
        f"fixed_evidence={fixed['evidence_completeness']:.2%}; "
        f"impact_precision={fixed['impact_lot_precision']:.2%}; "
        f"impact_recall={fixed['impact_lot_recall']:.2%}; "
        f"abstention={fixed['correct_abstention_rate']:.2%}; "
        f"real_qwen={result['modes']['llm_react']['status']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    # BLOCKED is an honest release state, not an evaluator crash. Return nonzero
    # only for an executed failing real-Qwen gate or a governance failure.
    if result["gates"]["governance"]["status"] != "PASS":
        return 1
    if result["modes"]["llm_react"]["status"] == "COMPLETE":
        return 0 if result["gates"]["rca_quality"]["status"] == "PASS" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
