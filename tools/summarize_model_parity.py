"""Summarize the two explicitly authorized local smoke outputs, without scoring GT."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(root, arm):
    telemetry = read(root / (arm + ".telemetry.json"))
    calls = telemetry["calls"]
    results = read(root / arm / "run_results.json")["results"][0]
    state_path = root / arm / "states/FORMAL_012.json"
    state = read(state_path) if state_path.exists() else {}
    finding = next(
        (
            f
            for f in state.get("findings", [])
            if f["finding_id"] == state.get("authoritative_rca_finding_id")
        ),
        {},
    )
    hypothesis = next(
        (
            h
            for h in state.get("hypotheses", [])
            if h["hypothesis_id"] == state.get("authoritative_hypothesis_id")
        ),
        {},
    )
    details = finding.get("details", {})
    candidate = details.get("hypothesis_candidate_generation", {})
    authority = state.get("authoritative_rca_result") or {}
    impact = state.get("impact_publication_result") or {}
    known = {e["evidence_id"] for e in state.get("evidence", [])}
    accepted_refs = set(authority.get("evidence_refs", [])) | set(impact.get("evidence_refs", []))
    for name in (
        "evidence_ids",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
        "neutral_evidence_ids",
    ):
        accepted_refs.update(hypothesis.get(name, []))
    unknown_refs = sorted(accepted_refs - known)
    usage = [u for c in calls for u in c["usage_events"]]
    diagnostic_errors = candidate.get("validation_errors", [])
    semantic_errors = candidate.get("semantic_validation_errors", [])
    # Keep full diagnostics; regex is an index, not a semantic validator or success metric.
    ref_diagnostics = [
        e
        for e in diagnostic_errors + semantic_errors
        if any(
            x in str(e).lower()
            for x in (
                "unknown evidence",
                "invalid reference",
                "unknown_evidence",
                "unreferenceable",
            )
        )
    ]
    stage_rows = {}
    for stage in sorted({c["stage"] for c in calls}):
        selected = [c for c in calls if c["stage"] == stage]
        stage_rows[stage] = {
            "logical_calls": len(selected),
            "http_attempts": sum(len(c["http_attempts"]) for c in selected),
            "planner_transient_retries": sum(c["planner_transient_retry"] for c in selected),
            "bounded_output_retries": sum(c["bounded_output_retry"] for c in selected),
            "input_tokens": sum(u["prompt_tokens"] for c in selected for u in c["usage_events"]),
            "output_tokens": sum(
                u["completion_tokens"] for c in selected for u in c["usage_events"]
            ),
            "latency_ms": round(sum(c["latency_ms"] for c in selected), 3),
            "call_ordinals": [c["ordinal"] for c in selected],
        }
    checks = {
        "observer_no_drop": telemetry["observer_dropped"] == 0,
        "logical_count_matches_cap_wrapper": telemetry["capped_call_counts"] == [len(calls)],
        "within_logical_cap": len(calls) <= 20 and not any(telemetry["capped_limit_exceeded"]),
        "within_http_attempt_budget": all(len(c["http_attempts"]) <= 2 for c in calls),
        "logical_ids_present_unique": len({c.get("logical_call_id") for c in calls}) == len(calls)
        and all(c.get("logical_call_id") for c in calls),
        "correlation_ids_unique": len({c["correlation_id"] for c in calls}) == len(calls),
        "case_completed": results.get("job_status") == "completed",
        "strict_accepted": results.get("strict_qwen_accepted") is True,
        "authority_pointers_consistent": bool(authority)
        and authority.get("source_finding_id") == state.get("authoritative_rca_finding_id")
        and authority.get("source_hypothesis_id") == state.get("authoritative_hypothesis_id")
        and impact.get("rca_result_id") == authority.get("result_id"),
        "authority_reference_closure": not unknown_refs,
    }
    runtime_checks = None
    events = telemetry["runtime_events"]
    if events is not None:
        runtime_checks = {
            "no_drop": telemetry["runtime_dropped"] == 0,
            "event_count": len(events),
            "calls": [],
        }
        for c in calls:
            own = [e for e in events if e["logical_call_id"] == c["logical_call_id"]]
            http = [e for e in own if e["kind"] == "http_attempt"]
            outcomes = [e for e in own if e["kind"] == "model_result"]
            row = {
                "ordinal": c["ordinal"],
                "identity_matches": all(
                    e["provider"] == c["provider"] and e["model"] == c["model"] for e in own
                ),
                "logical_event_once": sum(e["kind"] == "logical_call" for e in own) == 1,
                "http_count_matches": len(http) == len(c["http_attempts"]),
                "http_status_matches": [e["status_code"] for e in http]
                == [a["status_code"] for a in c["http_attempts"]],
                "outcome_once": len(outcomes) == 1,
            }
            if c["error"] is None and len(outcomes) == 1:
                u = c["usage_events"][0]
                row["json_outcome_matches"] = outcomes[0]["outcome"] == "success"
                row["usage_matches"] = all(
                    outcomes[0]["usage"][k] == u[k]
                    for k in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                    )
                )
                row["stop_reason_matches"] = outcomes[0]["stop_reason"] == c["http_attempts"][
                    -1
                ].get("stop_reason")
                row["reported_model_matches"] = outcomes[0]["reported_model"] == c["http_attempts"][
                    -1
                ].get("reported_model")
            runtime_checks["calls"].append(row)
        checks["runtime_matches_external_observer"] = runtime_checks["no_drop"] and all(
            all(value for key, value in row.items() if key != "ordinal")
            for row in runtime_checks["calls"]
        )
    if state:
        from yield_rca_core.models import RCAState

        checks["state_roundtrip"] = RCAState.from_dict(state).to_dict() == state
    return {
        "arm": arm,
        "commit": telemetry["preflight"]["head"],
        "metrics": {
            "job_status": results.get("job_status"),
            "error": results.get("error"),
            "strict_accept": results.get("strict_qwen_accepted"),
            "strict_rejection_reasons": results.get("strict_qwen_rejection_reasons"),
            "provider_failure": results.get("provider_failure"),
            "logical_calls": len(calls),
            "http_attempts": sum(len(c["http_attempts"]) for c in calls),
            "planner_transient_retries": sum(c["planner_transient_retry"] for c in calls),
            "bounded_output_retries": sum(c["bounded_output_retry"] for c in calls),
            "candidate_calls_all_rounds": sum(
                c["stage"] == "hypothesis_candidate_generator" for c in calls
            ),
            "candidate_attempts_final_round": candidate.get("attempt_count"),
            "candidate_count": candidate.get("candidate_count"),
            "ranked_candidate_count": results.get("ranked_candidate_count"),
            "candidate_validation_errors": len(diagnostic_errors),
            "semantic_errors": len(semantic_errors),
            "unknown_authority_or_hypothesis_evidence_refs": unknown_refs,
            "reference_diagnostic_entries": len(ref_diagnostics),
            "actions": results.get("action_count"),
            "tools": state.get("execution_metadata", {}).get("tool_call_count"),
            "evidence": results.get("evidence_count"),
            "hypothesis_status": hypothesis.get("status"),
            "conclusion_status": authority.get("conclusion_status"),
            "root_cause": authority.get("root_cause"),
            "hypothesis_root_cause": hypothesis.get("root_cause"),
            "competition_status": authority.get("competition_status"),
            "competition_terminal_reason": results.get("competition_terminal_reason"),
            "confirmation_status": authority.get("confirmation_status"),
            "impact_status": impact.get("publication_status"),
            "fallback_reason": results.get("fallback_reason"),
            "candidate_fallback_reason": candidate.get("fallback_reason"),
            "input_tokens": sum(e["prompt_tokens"] for e in usage),
            "output_tokens": sum(e["completion_tokens"] for e in usage),
            "workflow_ms": state.get("execution_metadata", {}).get("workflow_duration_ms"),
            "shell_seconds": telemetry["elapsed_seconds"],
            "logical_call_latency_ms": sum(c["latency_ms"] for c in calls),
            "http_latency_ms": sum(a["latency_ms"] for c in calls for a in c["http_attempts"]),
            "json_outcomes": dict(Counter(c["outcome"] for c in calls)),
            "http_statuses": dict(
                Counter(str(a["status_code"]) for c in calls for a in c["http_attempts"])
            ),
            "stop_reasons": dict(
                Counter(str(a.get("stop_reason")) for c in calls for a in c["http_attempts"])
            ),
        },
        "stages": stage_rows,
        "checks": checks,
        "runtime_checks": runtime_checks,
        "diagnostics": {
            "candidate": diagnostic_errors,
            "semantic": semantic_errors,
            "reference_index": ref_diagnostics,
            "confirmation": details.get("confirmation_gate"),
            "intent_attempts": state.get("execution_metadata", {}).get(
                "intent_planner_attempt_diagnostics"
            ),
            "provider_failures": results.get("provider_failures"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.input_root.resolve()
    a = read(root / "A_old.started.json")
    b = read(root / "B_runtime.started.json")
    same = {
        key: a[key] == b[key]
        for key in (
            "configuration",
            "budgets",
            "logical_cap",
            "public_hashes",
            "prompt_hashes",
            "runner_sha256",
            "harness_sha256",
            "python",
            "case_id",
        )
    }
    if not all(same.values()):
        raise ValueError("A/B preconditions differ")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
    result = {
        "input_root": str(root),
        "comparability": same,
        "old": summarize(root, "A_old"),
        "new": summarize(root, "B_runtime"),
    }
    old_calls = read(root / "A_old.telemetry.json")["calls"]
    new_calls = read(root / "B_runtime.telemetry.json")["calls"]
    if old_calls and new_calls:
        result["first_call_comparison"] = {
            "logical_request_sha256_equal": old_calls[0]["request_sha256"]
            == new_calls[0]["request_sha256"],
            "wire_body_sha256_equal": old_calls[0]["http_attempts"][0]["request_body_sha256"]
            == new_calls[0]["http_attempts"][0]["request_body_sha256"],
            "parsed_response_sha256_equal": old_calls[0].get("parsed_response_sha256")
            == new_calls[0].get("parsed_response_sha256"),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                arm: {"metrics": result[arm]["metrics"], "checks": result[arm]["checks"]}
                for arm in ("old", "new")
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
