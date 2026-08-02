"""Run a bounded real-Qwen diagnosis of the Golden Intent Planner handoff."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.intent_planner import (  # noqa: E402
    QwenIntentPlanner,
    QwenIntentPlannerError,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    build_llm_client,
)

DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "qwen_intent_diagnosis"
GOLDEN_QUERY = "Investigate the root cause of LOT_A_001 scratch in Cu CMP."
GOLDEN_LOT_ID = "LOT_A_001"
DEFAULT_RUNS = 3
MAX_INTENT_CALLS_PER_RUN = 2
RESULT_SCHEMA_VERSION = "qwen_intent_diagnosis_v1"


class IntentCallCappedLLMClient:
    """Prevent diagnosis from calling anything beyond two Intent attempts."""

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
        if request.prompt_name != "intent_planner":
            raise LLMCallError(
                "Intent diagnosis blocked a non-Intent Planner call",
                failure_category="diagnostic_scope_violation",
            )
        if self.call_count >= self.max_calls:
            self.limit_exceeded = True
            raise LLMCallError(
                "Intent diagnosis exceeded its paid call limit",
                failure_category="call_limit",
            )
        self.call_count += 1
        return self.delegate.complete_json(request)


def _plan_summary(plan: object) -> dict[str, Any]:
    goal = getattr(plan, "goal", None)
    questions = getattr(plan, "questions", [])
    notices = getattr(plan, "capability_notices", [])
    known_facts = getattr(goal, "known_facts", {})
    return {
        "intent": getattr(goal, "intent", None),
        "known_fact_keys": sorted(known_facts) if isinstance(known_facts, dict) else [],
        "required_evidence": list(getattr(goal, "required_evidence", [])),
        "question_count": len(questions) if isinstance(questions, list) else 0,
        "question_kinds": [
            question.question_kind
            for question in questions
            if getattr(question, "question_kind", None)
        ],
        "capability_notices": [
            notice.capability
            for notice in notices
            if getattr(notice, "capability", None)
        ],
    }


def _provider_failure(error: LLMCallError) -> dict[str, Any]:
    values = {
        "failure_category": error.failure_category,
        "status_code": error.status_code,
        "provider_code": error.provider_code,
        "provider_message": error.provider_message,
        "request_id": error.request_id,
        "call_attempt_count": error.call_attempt_count,
    }
    return {key: value for key, value in values.items() if value is not None}


def _count_attempt_failures(
    runs: list[dict[str, Any]],
    field_name: str,
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for run in runs:
        for attempt in run.get("attempt_diagnostics", []):
            if attempt.get("outcome") != "failure":
                continue
            value = attempt.get(field_name)
            if isinstance(value, str) and value:
                counter[value] += 1
    return dict(sorted(counter.items()))


def _dominant_value(counts: dict[str, int]) -> str | None:
    if not counts:
        return None
    return sorted(counts, key=lambda value: (-counts[value], value))[0]


def _diagnosis_summary(
    *,
    accepted_count: int,
    rejected_count: int,
    provider_failure_count: int,
    category_counts: dict[str, int],
    reason_counts: dict[str, int],
    path_counts: dict[str, int],
) -> tuple[str, str]:
    if provider_failure_count:
        return (
            "transport_or_provider_failure",
            "At least one run did not reach Intent output validation because the "
            "provider call failed; the Planner contract root cause is not yet isolated.",
        )
    if rejected_count == 0:
        return (
            "intent_plan_accepted",
            "Every real-Qwen run produced an IntentPlan accepted by the Python contract.",
        )
    category = _dominant_value(category_counts) or "unknown_failure"
    reason = _dominant_value(reason_counts) or "unknown_reason"
    field_path = _dominant_value(path_counts) or "unknown field"
    stability = "all" if accepted_count == 0 else "some"
    return (
        f"{category}:{reason}",
        f"Qwen failed {stability} sampled Intent handoffs primarily at {field_path} "
        f"with {category}/{reason}. This failure occurs before Next Action Planning.",
    )


def aggregate_intent_diagnosis(
    runs: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    accepted_count = sum(run["status"] == "accepted" for run in runs)
    rejected_count = sum(run["status"] == "rejected" for run in runs)
    provider_failure_count = sum(
        run["status"] == "provider_failure" for run in runs
    )
    category_counts = _count_attempt_failures(runs, "failure_category")
    reason_counts = _count_attempt_failures(runs, "reason_code")
    path_counts = _count_attempt_failures(runs, "field_path")
    primary_diagnosis, diagnosis = _diagnosis_summary(
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        provider_failure_count=provider_failure_count,
        category_counts=category_counts,
        reason_counts=reason_counts,
        path_counts=path_counts,
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "scenario": "golden_scratch_cu_cmp_root_cause",
        "provider": provider,
        "model": model,
        "run_count": len(runs),
        "accepted_run_count": accepted_count,
        "rejected_run_count": rejected_count,
        "provider_failure_count": provider_failure_count,
        "diagnosis_complete": provider_failure_count == 0 and bool(runs),
        "paid_llm_call_count": sum(run["paid_llm_call_count"] for run in runs),
        "max_paid_llm_calls_per_run": MAX_INTENT_CALLS_PER_RUN,
        "failure_category_counts": category_counts,
        "reason_code_counts": reason_counts,
        "field_path_counts": path_counts,
        "primary_diagnosis": primary_diagnosis,
        "diagnosis": diagnosis,
        "runs": runs,
        "security": {
            "raw_model_response_stored": False,
            "prompt_stored": False,
            "api_key_stored": False,
        },
    }


def render_intent_diagnosis_report(evaluation: dict[str, Any]) -> str:
    lines = [
        "# Real Qwen Intent Planner Diagnosis",
        "",
        f"- Scenario: `{evaluation['scenario']}`",
        f"- Provider/model: `{evaluation['provider']}` / `{evaluation['model']}`",
        f"- Runs: {evaluation['run_count']}",
        f"- Accepted: {evaluation['accepted_run_count']}",
        f"- Rejected: {evaluation['rejected_run_count']}",
        f"- Provider failures: {evaluation['provider_failure_count']}",
        f"- Paid Intent calls: {evaluation['paid_llm_call_count']}",
        "",
        "## Diagnosis",
        "",
        f"**{evaluation['primary_diagnosis']}**",
        "",
        str(evaluation["diagnosis"]),
        "",
        "This diagnostic boundary ends before Next Action Planning, Specialist Agents, "
        "Tools, and Evidence collection.",
        "",
        "## Aggregate failures",
        "",
        f"- Categories: `{json.dumps(evaluation['failure_category_counts'], sort_keys=True)}`",
        f"- Reason codes: `{json.dumps(evaluation['reason_code_counts'], sort_keys=True)}`",
        f"- Field paths: `{json.dumps(evaluation['field_path_counts'], sort_keys=True)}`",
        "",
        "## Runs",
        "",
        "| Run | Status | Paid calls | Attempts |",
        "|---:|---|---:|---:|",
    ]
    for run in evaluation["runs"]:
        lines.append(
            f"| {run['run_number']} | {run['status']} | "
            f"{run['paid_llm_call_count']} | {run['attempt_count']} |"
        )
    for run in evaluation["runs"]:
        lines.extend(["", f"### Run {run['run_number']}", ""])
        if run["status"] == "provider_failure":
            lines.append(
                f"Provider failure: `{json.dumps(run['provider_failure'], sort_keys=True)}`"
            )
            continue
        if run.get("plan_summary"):
            lines.append(
                f"Accepted plan summary: `{json.dumps(run['plan_summary'], sort_keys=True)}`"
            )
        for attempt in run["attempt_diagnostics"]:
            label = f"Attempt {attempt['attempt']}: {attempt['outcome']}"
            lines.extend(["", f"- **{label}**"])
            if attempt["outcome"] == "failure":
                lines.append(
                    "  - "
                    f"`{attempt['failure_category']}/{attempt['reason_code']}` "
                    f"at `{attempt['field_path']}`"
                )
                lines.append(f"  - {attempt['message']}")
            lines.append(
                "  - Candidate summary: "
                f"`{json.dumps(attempt['candidate_summary'], sort_keys=True)}`"
            )
            lines.append(
                "  - Baseline diff: "
                f"`{json.dumps(attempt['baseline_diff'], sort_keys=True)}`"
            )
    lines.extend(
        [
            "",
            "## Security boundary",
            "",
            "The report stores no API key, complete prompt, user-query payload, or raw "
            "model response. Candidate summaries contain only bounded structural fields.",
            "",
        ]
    )
    return "\n".join(lines)


def run_qwen_intent_diagnosis(
    *,
    settings: LLMSettings,
    output_dir: Path,
    runs: int = DEFAULT_RUNS,
    client_factory: Callable[[LLMSettings], LLMClient | None] = build_llm_client,
) -> dict[str, Any]:
    if type(runs) is not int or not 1 <= runs <= 10:
        raise ValueError("runs must be between 1 and 10")
    run_results: list[dict[str, Any]] = []
    for run_number in range(1, runs + 1):
        delegate = client_factory(settings)
        if delegate is None:
            raise ValueError("Qwen Intent diagnosis requires an LLM client")
        client = IntentCallCappedLLMClient(
            delegate,
            max_calls=MAX_INTENT_CALLS_PER_RUN,
        )
        planner = QwenIntentPlanner(client)
        try:
            outcome = planner.plan_with_diagnostics(
                GOLDEN_QUERY,
                lot_id=GOLDEN_LOT_ID,
            )
        except QwenIntentPlannerError as exc:
            run_results.append(
                {
                    "run_number": run_number,
                    "status": "rejected",
                    "paid_llm_call_count": client.call_count,
                    "call_limit_exceeded": client.limit_exceeded,
                    "attempt_count": exc.attempts,
                    "validation_errors": list(exc.validation_errors),
                    "attempt_diagnostics": [
                        diagnostic.to_dict()
                        for diagnostic in exc.attempt_diagnostics
                    ],
                    "plan_summary": None,
                }
            )
        except LLMCallError as exc:
            run_results.append(
                {
                    "run_number": run_number,
                    "status": "provider_failure",
                    "paid_llm_call_count": client.call_count,
                    "call_limit_exceeded": client.limit_exceeded,
                    "attempt_count": 0,
                    "validation_errors": [],
                    "attempt_diagnostics": [],
                    "plan_summary": None,
                    "provider_failure": _provider_failure(exc),
                }
            )
        else:
            run_results.append(
                {
                    "run_number": run_number,
                    "status": "accepted",
                    "paid_llm_call_count": client.call_count,
                    "call_limit_exceeded": client.limit_exceeded,
                    "attempt_count": len(outcome.attempt_diagnostics),
                    "validation_errors": [],
                    "attempt_diagnostics": [
                        diagnostic.to_dict()
                        for diagnostic in outcome.attempt_diagnostics
                    ],
                    "plan_summary": _plan_summary(outcome.plan),
                }
            )
    evaluation = aggregate_intent_diagnosis(
        run_results,
        provider=settings.provider,
        model=settings.model,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_intent_diagnosis_report(evaluation),
        encoding="utf-8",
    )
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the real-Qwen Golden Intent Planner contract with a hard "
            "two-call limit per run."
        )
    )
    parser.add_argument(
        "--confirm-paid-qwen",
        action="store_true",
        help="Confirm that paid DashScope calls are explicitly authorized.",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
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
    evaluation = run_qwen_intent_diagnosis(
        settings=settings,
        output_dir=args.output_dir,
        runs=args.runs,
    )
    print(
        "Real Qwen Intent diagnosis: "
        f"{'COMPLETE' if evaluation['diagnosis_complete'] else 'INCOMPLETE'}; "
        f"accepted={evaluation['accepted_run_count']}/{evaluation['run_count']}; "
        f"rejected={evaluation['rejected_run_count']}; "
        f"provider_failures={evaluation['provider_failure_count']}; "
        f"primary={evaluation['primary_diagnosis']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if evaluation["diagnosis_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
