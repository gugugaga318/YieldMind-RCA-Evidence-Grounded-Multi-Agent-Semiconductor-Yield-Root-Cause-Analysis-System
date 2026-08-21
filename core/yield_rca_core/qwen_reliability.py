"""Secret-free reliability summaries for repeated real-Qwen RCA runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from yield_rca_core.investigation_models import (
    DecisionType,
    EvidenceGapStatus,
    QuestionUpdateDisposition,
)
from yield_rca_core.models import RCAState, TaskStatus

QUESTION_UPDATE_FIELDS = frozenset(
    {
        "question_id",
        "status",
        "answer",
        "evidence_ids",
        "unavailable_reason",
    }
)


def _is_question_update_validation_error(error: str) -> bool:
    normalized = error.lower()
    return (
        "question_update" in normalized
        or "updated to terminal status" in normalized
    )


def _compact_question_update_reviews(state: RCAState) -> list[dict[str, Any]]:
    """Serialize bounded review facts without prompts or raw model output."""

    return [
        {
            "decision_id": review.decision_id,
            "disposition": review.disposition,
            "reason_code": review.reason_code,
            "update_index": review.update_index,
            "question_id": review.question_id,
            "claimed_status": review.claimed_status,
        }
        for review in state.question_update_reviews
    ]


def _review_trace_complete(state: RCAState) -> bool:
    accepted_by_decision: dict[str, list[tuple[str | None, str | None]]] = {}
    for review in state.question_update_reviews:
        if review.disposition != QuestionUpdateDisposition.ACCEPTED.value:
            continue
        accepted_by_decision.setdefault(review.decision_id, []).append(
            (review.question_id, review.claimed_status)
        )
    for decision in state.planner_decisions:
        committed = [
            (update.question_id, update.status)
            for update in decision.question_updates
        ]
        if accepted_by_decision.get(decision.decision_id, []) != committed:
            return False
    return True


def _rejected_action_decisions_were_preserved(state: RCAState) -> bool:
    decisions_by_id = {
        decision.decision_id: decision for decision in state.planner_decisions
    }
    completed_action_ids = {
        record.action.action_id
        for record in state.action_history
        if record.status == "completed"
    }
    for review in state.question_update_reviews:
        if review.disposition != QuestionUpdateDisposition.REJECTED.value:
            continue
        decision = decisions_by_id.get(review.decision_id)
        if decision is None:
            return False
        if decision.decision_type != DecisionType.ACT.value:
            continue
        if (
            decision.next_action is None
            or decision.next_action.action_id not in completed_action_ids
        ):
            return False
    return True


def summarize_qwen_reliability_run(
    state: RCAState,
    *,
    run_number: int,
    paid_llm_call_count: int,
    max_llm_calls: int,
    call_limit_exceeded: bool = False,
    planner_call_failure_count: int = 0,
    recovered_planner_call_retry_count: int = 0,
) -> dict[str, Any]:
    """Convert one terminal state into bounded, secret-free acceptance facts."""

    if type(run_number) is not int or run_number < 1:
        raise ValueError("run_number must be a positive integer")
    if type(paid_llm_call_count) is not int or paid_llm_call_count < 0:
        raise ValueError("paid_llm_call_count must be a non-negative integer")
    if type(max_llm_calls) is not int or max_llm_calls < 1:
        raise ValueError("max_llm_calls must be a positive integer")

    metadata = state.execution_metadata
    fallback_errors = [
        value
        for value in metadata.get("orchestration_fallback_validation_errors", [])
        if isinstance(value, str) and value.strip()
    ]
    raw_validation_categories = metadata.get(
        "orchestration_fallback_validation_error_categories",
        [],
    )
    validation_categories = (
        [value for value in raw_validation_categories if isinstance(value, str)]
        if isinstance(raw_validation_categories, list)
        else []
    )
    output_parse_errors = [
        error
        for error, category in zip(
            fallback_errors,
            validation_categories,
            strict=False,
        )
        if category == "output_parse"
    ]
    question_update_errors = [
        error
        for error in fallback_errors
        if _is_question_update_validation_error(error)
    ]
    core_validation_errors = [
        error
        for error, category in zip(
            fallback_errors,
            validation_categories,
            strict=False,
        )
        if category == "core_decision_validation"
    ]
    if (
        not validation_categories
        and metadata.get("orchestration_fallback_reason")
        == "qwen_next_action_output_invalid"
    ):
        core_validation_errors = [
            error for error in fallback_errors if error not in question_update_errors
        ]
    serialized_updates = [
        update.to_dict()
        for decision in state.planner_decisions
        for update in decision.question_updates
    ]
    serialized_reviews = _compact_question_update_reviews(state)
    accepted_reviews = [
        review
        for review in serialized_reviews
        if review["disposition"] == QuestionUpdateDisposition.ACCEPTED.value
    ]
    rejected_reviews = [
        review
        for review in serialized_reviews
        if review["disposition"] == QuestionUpdateDisposition.REJECTED.value
    ]
    rejection_reason_counts: dict[str, int] = {}
    for review in rejected_reviews:
        reason_code = str(review["reason_code"])
        rejection_reason_counts[reason_code] = (
            rejection_reason_counts.get(reason_code, 0) + 1
        )
    compact_updates = all(
        set(update) == QUESTION_UPDATE_FIELDS
        and update["status"]
        in {
            EvidenceGapStatus.CLOSED.value,
            EvidenceGapStatus.UNAVAILABLE.value,
        }
        for update in serialized_updates
    )
    action_chain = [record.action.kind for record in state.action_history]
    terminal_decision = state.planner_decisions[-1] if state.planner_decisions else None
    run_evaluation = state.run_evaluation
    checks = {
        "workflow_completed": state.job.status == TaskStatus.COMPLETED.value,
        "requested_llm_react": (
            metadata.get("orchestration_requested_mode") == "llm_react"
        ),
        "actual_llm_react": metadata.get("orchestration_mode") == "llm_react",
        "no_controlled_fallback": not metadata.get("orchestration_fallback_reason"),
        "scratch_starts_with_defect_inspection": bool(action_chain)
        and action_chain[0] == "inspect_defect_pattern",
        "observation_replanned": len(action_chain) >= 2,
        "terminal_stop": terminal_decision is not None
        and terminal_decision.decision_type == DecisionType.STOP.value,
        "question_updates_present": bool(serialized_updates),
        "compact_question_updates": compact_updates,
        "question_update_reviews_present": bool(serialized_reviews),
        "question_update_review_trace_complete": _review_trace_complete(state),
        "rejected_updates_preserved_core_action": (
            _rejected_action_decisions_were_preserved(state)
        ),
        "no_question_update_validation_error": not question_update_errors,
        "within_llm_call_limit": (
            not call_limit_exceeded and paid_llm_call_count <= max_llm_calls
        ),
        "goal_success": bool(run_evaluation and run_evaluation.goal_success),
        "stop_correct": bool(run_evaluation and run_evaluation.stop_correct),
    }
    return {
        "run_number": run_number,
        "passed": all(checks.values()),
        "checks": checks,
        "job_status": state.job.status,
        "actual_mode": metadata.get("orchestration_mode"),
        "stop_reason": state.stop_reason,
        "fallback_reason": metadata.get("orchestration_fallback_reason"),
        "fallback_stage": metadata.get("orchestration_fallback_stage"),
        "fallback_failure_category": metadata.get(
            "orchestration_fallback_failure_category"
        ),
        "fallback_after_action_count": metadata.get(
            "orchestration_fallback_after_action_count"
        ),
        "fallback_attempt_count": metadata.get(
            "orchestration_fallback_attempt_count"
        ),
        "fallback_call_attempt_count": metadata.get(
            "orchestration_fallback_call_attempt_count"
        ),
        "fallback_status_code": metadata.get(
            "orchestration_fallback_status_code"
        ),
        "fallback_provider_code": metadata.get(
            "orchestration_fallback_provider_code"
        ),
        "fallback_provider_message": metadata.get(
            "orchestration_fallback_provider_message"
        ),
        "fallback_request_id": metadata.get(
            "orchestration_fallback_request_id"
        ),
        "fallback_validation_errors": fallback_errors,
        "fallback_validation_error_categories": validation_categories,
        "output_parse_validation_errors": output_parse_errors,
        "core_planner_validation_errors": core_validation_errors,
        "question_update_validation_errors": question_update_errors,
        "action_chain": action_chain,
        "planner_decision_count": len(state.planner_decisions),
        "question_update_count": len(serialized_updates),
        "question_updates": serialized_updates,
        "question_update_review_count": len(serialized_reviews),
        "accepted_question_update_count": len(accepted_reviews),
        "rejected_question_update_count": len(rejected_reviews),
        "question_update_reviews": serialized_reviews,
        "rejection_reason_counts": rejection_reason_counts,
        "paid_llm_call_count": paid_llm_call_count,
        "max_llm_calls": max_llm_calls,
        "call_limit_exceeded": call_limit_exceeded,
        "planner_call_failure_count": planner_call_failure_count,
        "recovered_planner_call_retry_count": recovered_planner_call_retry_count,
        "goal_success": run_evaluation.goal_success if run_evaluation else None,
        "stop_correct": run_evaluation.stop_correct if run_evaluation else None,
        "error_type": None,
        "error_message": None,
    }


def qwen_reliability_failure(
    *,
    run_number: int,
    paid_llm_call_count: int,
    max_llm_calls: int,
    call_limit_exceeded: bool,
    error: Exception,
    redact_values: list[str] | None = None,
    planner_call_failure_count: int = 0,
    recovered_planner_call_retry_count: int = 0,
) -> dict[str, Any]:
    """Return the same report shape when a bounded workflow raises."""

    message = str(error).strip() or type(error).__name__
    for value in redact_values or []:
        if value:
            message = message.replace(value, "[REDACTED]")
    message = message[:500]
    return {
        "run_number": run_number,
        "passed": False,
        "checks": {"workflow_completed": False},
        "job_status": "failed",
        "actual_mode": None,
        "stop_reason": None,
        "fallback_reason": None,
        "fallback_stage": None,
        "fallback_failure_category": getattr(error, "failure_category", None),
        "fallback_after_action_count": None,
        "fallback_attempt_count": None,
        "fallback_call_attempt_count": getattr(error, "call_attempt_count", None),
        "fallback_status_code": getattr(error, "status_code", None),
        "fallback_provider_code": getattr(error, "provider_code", None),
        "fallback_provider_message": getattr(error, "provider_message", None),
        "fallback_request_id": getattr(error, "request_id", None),
        "fallback_validation_errors": [],
        "fallback_validation_error_categories": [],
        "output_parse_validation_errors": [],
        "core_planner_validation_errors": [],
        "question_update_validation_errors": [],
        "action_chain": [],
        "planner_decision_count": 0,
        "question_update_count": 0,
        "question_updates": [],
        "question_update_review_count": 0,
        "accepted_question_update_count": 0,
        "rejected_question_update_count": 0,
        "question_update_reviews": [],
        "rejection_reason_counts": {},
        "paid_llm_call_count": paid_llm_call_count,
        "max_llm_calls": max_llm_calls,
        "call_limit_exceeded": call_limit_exceeded,
        "planner_call_failure_count": planner_call_failure_count,
        "recovered_planner_call_retry_count": recovered_planner_call_retry_count,
        "goal_success": None,
        "stop_correct": None,
        "error_type": type(error).__name__,
        "error_message": message,
    }


def aggregate_qwen_reliability_runs(
    runs: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    query: str,
    max_llm_calls_per_run: int,
) -> dict[str, Any]:
    """Require every requested consecutive run to remain on the Qwen path."""

    if not runs:
        raise ValueError("runs must contain at least one reliability result")
    passed_count = sum(bool(run.get("passed")) for run in runs)
    fallback_count = sum(bool(run.get("fallback_reason")) for run in runs)
    question_update_error_count = sum(
        len(run.get("question_update_validation_errors", [])) for run in runs
    )
    review_count = sum(
        int(run.get("question_update_review_count", 0)) for run in runs
    )
    accepted_update_count = sum(
        int(run.get("accepted_question_update_count", 0)) for run in runs
    )
    rejected_update_count = sum(
        int(run.get("rejected_question_update_count", 0)) for run in runs
    )
    core_validation_error_count = sum(
        len(run.get("core_planner_validation_errors", [])) for run in runs
    )
    output_parse_error_count = sum(
        len(run.get("output_parse_validation_errors", [])) for run in runs
    )
    transport_provider_failure_count = sum(
        run.get("fallback_failure_category")
        in {"transport_error", "provider_http_error"}
        for run in runs
    )
    planner_call_failure_count = sum(
        int(run.get("planner_call_failure_count", 0)) for run in runs
    )
    recovered_planner_call_retry_count = sum(
        int(run.get("recovered_planner_call_retry_count", 0)) for run in runs
    )
    rejection_reason_counts: dict[str, int] = {}
    for run in runs:
        raw_counts = run.get("rejection_reason_counts", {})
        if not isinstance(raw_counts, dict):
            continue
        for reason_code, count in raw_counts.items():
            if isinstance(reason_code, str) and type(count) is int:
                rejection_reason_counts[reason_code] = (
                    rejection_reason_counts.get(reason_code, 0) + count
                )
    return {
        "schema_version": "2.1",
        "suite": "qwen_planner_review_reliability",
        "generated_at": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "query": query,
        "required_consecutive_runs": len(runs),
        "max_llm_calls_per_run": max_llm_calls_per_run,
        "passed": passed_count == len(runs),
        "passed_run_count": passed_count,
        "pass_rate": passed_count / len(runs),
        "controlled_fallback_count": fallback_count,
        "core_planner_validation_error_count": core_validation_error_count,
        "output_parse_error_count": output_parse_error_count,
        "transport_provider_failure_count": transport_provider_failure_count,
        "planner_call_failure_count": planner_call_failure_count,
        "recovered_planner_call_retry_count": recovered_planner_call_retry_count,
        "question_update_validation_error_count": question_update_error_count,
        "question_update_review_count": review_count,
        "accepted_question_update_count": accepted_update_count,
        "rejected_question_update_count": rejected_update_count,
        "rejection_reason_counts": rejection_reason_counts,
        "runs": runs,
    }


def render_qwen_reliability_report(evaluation: dict[str, Any]) -> str:
    """Render a compact report without prompts, raw responses, or credentials."""

    lines = [
        "# Qwen Planner Review Reliability Evaluation",
        "",
        f"Acceptance: **{'PASS' if evaluation['passed'] else 'FAIL'}**",
        "",
        (
            f"Model `{evaluation['model']}` completed "
            f"{evaluation['passed_run_count']}/"
            f"{evaluation['required_consecutive_runs']} required consecutive runs "
            f"without a controlled fallback."
        ),
        "",
        "This report stores bounded state summaries only. It does not store API keys, "
        "Planner prompts, or raw Qwen responses.",
        "",
        "## Run summary",
        "",
        (
            "| Run | Status | Actual path | Actions | Accepted / rejected updates "
            "| Recovered retries | Paid calls |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in evaluation["runs"]:
        chain = " -> ".join(run["action_chain"]) or "(none)"
        lines.append(
            f"| {run['run_number']} | {'PASS' if run['passed'] else 'FAIL'} | "
            f"{run['actual_mode'] or '(none)'} | {chain} | "
            f"{run['accepted_question_update_count']} / "
            f"{run['rejected_question_update_count']} | "
            f"{run['recovered_planner_call_retry_count']} | "
            f"{run['paid_llm_call_count']}/{run['max_llm_calls']} |"
        )

    rejected_runs = [
        run for run in evaluation["runs"] if run["rejected_question_update_count"]
    ]
    if rejected_runs:
        lines.extend(["", "## Rejected QuestionUpdate audit", ""])
        lines.append(
            "Rejected ancillary status claims are acceptable when the core decision "
            "remains legal, the Agent action is committed, and the run stays on "
            "`llm_react`."
        )
        for run in rejected_runs:
            reason_counts = ", ".join(
                f"`{reason}`={count}"
                for reason, count in sorted(run["rejection_reason_counts"].items())
            )
            lines.extend(
                [
                    "",
                    f"- Run {run['run_number']}: {reason_counts or 'no reason code'}",
                ]
            )

    failed_runs = [run for run in evaluation["runs"] if not run["passed"]]
    if failed_runs:
        lines.extend(["", "## Failure diagnostics", ""])
        for run in failed_runs:
            failed_checks = [
                name for name, passed in run["checks"].items() if not passed
            ]
            lines.append(
                f"### Run {run['run_number']}: "
                f"{run['error_type'] or run['fallback_reason'] or 'acceptance failure'}"
            )
            lines.append("")
            lines.append(
                "Failed checks: "
                + (", ".join(f"`{name}`" for name in failed_checks) or "none")
            )
            if run["error_message"]:
                lines.extend(["", f"Error: `{run['error_message']}`"])
            diagnostic_parts = [
                ("category", run.get("fallback_failure_category")),
                ("call attempts", run.get("fallback_call_attempt_count")),
                ("status", run.get("fallback_status_code")),
                ("provider code", run.get("fallback_provider_code")),
                ("provider message", run.get("fallback_provider_message")),
                ("request id", run.get("fallback_request_id")),
            ]
            rendered_diagnostics = ", ".join(
                f"{label}=`{value}`"
                for label, value in diagnostic_parts
                if value is not None
            )
            if rendered_diagnostics:
                lines.extend(["", f"Provider diagnostics: {rendered_diagnostics}"])
            for error in run["fallback_validation_errors"]:
                lines.extend(["", f"- `{error}`"])

    lines.extend(
        [
            "",
            "## Acceptance boundary",
            "",
            (
                f"All {evaluation['required_consecutive_runs']} runs must complete on "
                "`llm_react`, re-plan after the first Scratch observation, emit only "
                "compact terminal QuestionUpdate deltas, audit every accepted or "
                "rejected update claim, preserve legal Agent actions after ancillary "
                "rejections, finish within the per-run LLM-call cap, and pass the "
                "existing goal/stop evaluation. A core Planner validation failure "
                "still fails this reliability boundary."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "QUESTION_UPDATE_FIELDS",
    "aggregate_qwen_reliability_runs",
    "qwen_reliability_failure",
    "render_qwen_reliability_report",
    "summarize_qwen_reliability_run",
]
