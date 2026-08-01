"""Run an explicitly approved, bounded real-Qwen reliability evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    build_llm_client,
)
from yield_rca_core.qwen_reliability import (  # noqa: E402
    aggregate_qwen_reliability_runs,
    qwen_reliability_failure,
    render_qwen_reliability_report,
    summarize_qwen_reliability_run,
)
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "qwen_question_update_reliability"
ROOT_CAUSE_QUERY = "Investigate the root cause of LOT_A_001 scratch in Cu CMP."
DEFAULT_RUNS = 3
DEFAULT_MAX_LLM_CALLS_PER_RUN = 20


class CallCappedLLMClient:
    """Enforce a hard paid-call ceiling independently for each workflow run."""

    def __init__(self, delegate: LLMClient, *, max_calls: int) -> None:
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
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
                "Qwen reliability run exceeded its paid LLM-call limit"
            )
        self.call_count += 1
        return self.delegate.complete_json(request)


def run_qwen_reliability(
    *,
    settings: LLMSettings,
    seed_dir: Path,
    output_dir: Path,
    runs: int = DEFAULT_RUNS,
    max_llm_calls_per_run: int = DEFAULT_MAX_LLM_CALLS_PER_RUN,
    client_factory: Callable[[LLMSettings], LLMClient | None] = build_llm_client,
) -> dict[str, Any]:
    """Execute consecutive bounded runs and always write a diagnostic report."""

    if type(runs) is not int or not 1 <= runs <= 10:
        raise ValueError("runs must be between 1 and 10")
    if (
        type(max_llm_calls_per_run) is not int
        or not 1 <= max_llm_calls_per_run <= 30
    ):
        raise ValueError("max_llm_calls_per_run must be between 1 and 30")

    run_results: list[dict[str, Any]] = []
    for run_number in range(1, runs + 1):
        delegate = client_factory(settings)
        if delegate is None:
            raise ValueError("Qwen reliability evaluation requires an LLM client")
        client = CallCappedLLMClient(
            delegate,
            max_calls=max_llm_calls_per_run,
        )
        workflow = build_csv_workflow(
            seed_dir,
            llm_settings=settings,
            llm_client=client,
            orchestration_mode="llm_react",
        )
        try:
            state = workflow.run(
                ROOT_CAUSE_QUERY,
                job_id=f"JOB_QWEN_RELIABILITY_{run_number:02d}",
                lot_id="LOT_A_001",
            )
        except Exception as exc:  # noqa: BLE001 - report every bounded run failure
            run_results.append(
                qwen_reliability_failure(
                    run_number=run_number,
                    paid_llm_call_count=client.call_count,
                    max_llm_calls=max_llm_calls_per_run,
                    call_limit_exceeded=client.limit_exceeded,
                    error=exc,
                    redact_values=[settings.api_key],
                )
            )
            continue
        run_results.append(
            summarize_qwen_reliability_run(
                state,
                run_number=run_number,
                paid_llm_call_count=client.call_count,
                max_llm_calls=max_llm_calls_per_run,
                call_limit_exceeded=client.limit_exceeded,
            )
        )

    evaluation = aggregate_qwen_reliability_runs(
        run_results,
        provider=settings.provider,
        model=settings.model,
        query=ROOT_CAUSE_QUERY,
        max_llm_calls_per_run=max_llm_calls_per_run,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_qwen_reliability_report(evaluation),
        encoding="utf-8",
    )
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run consecutive paid Qwen Scratch/Cu CMP investigations with a hard "
            "per-run call cap."
        )
    )
    parser.add_argument(
        "--confirm-paid-qwen",
        action="store_true",
        help="Confirm that paid DashScope calls are explicitly authorized.",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument(
        "--max-llm-calls-per-run",
        type=int,
        default=DEFAULT_MAX_LLM_CALLS_PER_RUN,
    )
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.confirm_paid_qwen:
        parser.error("--confirm-paid-qwen is required before paid model calls")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        parser.error("DASHSCOPE_API_KEY must be set in the current process")

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
    evaluation = run_qwen_reliability(
        settings=settings,
        seed_dir=args.seed_dir,
        output_dir=args.output_dir,
        runs=args.runs,
        max_llm_calls_per_run=args.max_llm_calls_per_run,
    )
    print(
        "Qwen Planner review reliability: "
        f"{'PASS' if evaluation['passed'] else 'FAIL'}; "
        f"runs={evaluation['passed_run_count']}/"
        f"{evaluation['required_consecutive_runs']}; "
        f"fallbacks={evaluation['controlled_fallback_count']}; "
        f"reviews={evaluation['question_update_review_count']}; "
        f"rejected_updates={evaluation['rejected_question_update_count']}; "
        "core_validation_errors="
        f"{evaluation['core_planner_validation_error_count']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
