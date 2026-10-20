"""Offline-only Benchmark plan and evidence exporter. Never invokes a workflow/model.

Answer labels stay in a separate scorer. Missing observations are never zero-filled.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from statistics import mean, median
from typing import Any

from model_benchmark_telemetry import runtime_call_records

SCHEMA_VERSION = "model-benchmark/1.0-phase0"
PAIRS = (("dashscope", "qwen-plus"), ("deepseek", "deepseek-v4-pro"))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def measure(value: Any, source: str, reason: str | None = None) -> dict:
    return {
        "value": value,
        "availability": "unavailable" if value is None else "partial" if reason else "available",
        "source": source,
        "reason": reason,
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_child(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("artifact path escapes run directory")
    return path


def make_plan(
    case_ids: list[str],
    *,
    plan_id: str,
    seed: str,
    source_commit: str,
    prompt_hash: str,
    config: dict,
    repeats: int = 1,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", plan_id):
        raise ValueError("unsafe plan id")
    if not case_ids or len(case_ids) != len(set(case_ids)) or repeats not in {1, 2, 3}:
        raise ValueError("unique nonempty cases and 1-3 repeats required")
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", c) for c in case_ids):
        raise ValueError("unsafe case id")
    if not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("exact commit required")
    if not re.fullmatch(r"[a-f0-9]{64}", prompt_hash):
        raise ValueError("prompt hash required")
    rows = []
    cases = sorted(case_ids, key=lambda c: digest([seed, c]))
    for replicate in range(1, repeats + 1):
        for index, case_id in enumerate(cases):
            pairs = PAIRS if (index + replicate - 1) % 2 == 0 else tuple(reversed(PAIRS))
            for provider, model in pairs:
                rows.append(
                    {
                        "run_id": f"{plan_id}_{case_id}_{provider}_r{replicate}",
                        "case_id": case_id,
                        "provider": provider,
                        "model": model,
                        "replicate": replicate,
                        "order_index": len(rows) + 1,
                        "scheduled_status": "not_run",
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "seed": seed,
        "source_commit": source_commit,
        "prompt_hash": prompt_hash,
        "config": config,
        "config_hash": digest(config),
        "runs": rows,
        "paid_execution_authorized": False,
        "runner_auto_reruns": 0,
    }


def export_record(
    run_dir: Path, *, case_id: str, run_id: str, execution_kind: str = "historical_artifact"
) -> dict:
    """Read explicit files only. Does not discover or load Ground Truth/credentials."""
    manifest = read_json(run_dir / "run_manifest.json")
    results = read_json(run_dir / "run_results.json")
    selected = manifest.get("selected_case_ids", [])
    if case_id not in selected:
        raise ValueError("case not declared in run manifest")
    matching = [r for r in results.get("results", []) if r.get("case_id") == case_id]
    if len(matching) > 1:
        raise ValueError("duplicate case result")
    result = matching[0] if matching else {}
    state = {}
    if result.get("state_file"):
        path = safe_child(run_dir, result["state_file"])
        if path.exists():
            state = read_json(path)
    meta = state.get("execution_metadata", {})
    config = manifest.get("configuration", {})
    provider, model = config.get("provider"), config.get("model")
    identity = meta.get("model_identity") or {}
    if identity.get("basis") == "runtime_config":
        if provider and identity.get("provider") != provider:
            raise ValueError("requested/actual provider mismatch")
        if model and identity.get("model") != model:
            raise ValueError("requested/actual model mismatch")
    else:
        identity = {}
    telemetry = meta.get("model_telemetry", {})
    calls = runtime_call_records(telemetry) if telemetry.get("events") else []
    complete = telemetry.get("complete") is True and telemetry.get("dropped_count") == 0
    # Complete capture is necessary, but an unfinished logical call remains partial.
    results_by_id = {
        e["logical_call_id"]: e for e in telemetry.get("events", []) if e["kind"] == "model_result"
    }
    terminal = complete and len(results_by_id) == len(calls)
    for call in calls:
        if provider and call.provider != provider or model and call.model != model:
            raise ValueError("call identity differs from manifest")
    observations: dict[str, dict] = {}

    def add(name, value, source, reason=None):
        if value is None and reason is None:
            reason = "not recorded"
        observations[name] = measure(value, source, reason)

    add("job_status", result.get("job_status"), "run_results.results.job_status")
    accepted = result.get("model_contract_accepted")
    add(
        "contract_status",
        "ACCEPT" if accepted is True else "REJECT" if accepted is False else None,
        "run_results.results.model_contract_accepted",
    )
    add(
        "strict_qwen",
        result.get("strict_qwen_accepted") if provider == "dashscope" else None,
        "run_results.results.strict_qwen_accepted",
        None
        if provider == "dashscope"
        else "Qwen-only compatibility metric; not cross-provider acceptance",
    )
    add("provider_failure", result.get("provider_failure"), "run_results.results.provider_failure")
    add(
        "logical_calls",
        len(calls) if complete else None,
        "model_telemetry.logical_call",
        None if complete else "missing/incomplete Runtime capture; usage rows are not call counts",
    )
    attempts = [e for e in telemetry.get("events", []) if e["kind"] == "http_attempt"]
    add("http_attempts", len(attempts) if complete else None, "model_telemetry.http_attempt")
    add(
        "http_retries",
        sum(max(0, len(c.http_attempts) - 1) for c in calls) if complete else None,
        "model_telemetry.http_attempt grouped by logical_call_id",
    )
    add(
        "json_decoded_calls",
        sum(r.get("outcome") == "success" for r in results_by_id.values()) if terminal else None,
        "model_telemetry.model_result",
    )
    for field, attr in [
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ]:
        values = [getattr(c, attr) for c in calls]
        valid = (
            terminal
            and all(v is not None for v in values)
            and all(e.get("outcome") == "success" for e in attempts)
        )
        add(
            field,
            sum(values) if valid else None,
            "model_result.usage.reported_fields",
            None
            if valid
            else "unreported usage, failed HTTP attempt or incomplete ledger; never zero-fill",
        )
    add(
        "model_http_latency_ms",
        sum(e["latency_ms"] for e in attempts)
        if terminal and all(e.get("latency_ms") is not None for e in attempts)
        else None,
        "model_telemetry.http_attempt.latency_ms",
    )
    add("workflow_latency_ms", meta.get("workflow_duration_ms"), "execution_metadata")
    add(
        "local_processing_latency_ms",
        None,
        "none",
        "workflow minus HTTP includes waiting/overhead; not measured pure local processing",
    )
    add(
        "actions",
        len(state["action_history"]) if "action_history" in state else None,
        "state.action_history",
    )
    add("tools", meta.get("tool_call_count"), "execution_metadata.tool_call_count")
    add("evidence_count", len(state["evidence"]) if "evidence" in state else None, "state.evidence")
    findings = [
        f
        for f in state.get("findings", [])
        if f.get("finding_id") == state.get("authoritative_rca_finding_id")
    ]
    if len(findings) > 1:
        raise ValueError("duplicate authority pointer")
    details = findings[0].get("details", {}) if findings else {}
    gen = details.get("hypothesis_candidate_generation", {})
    challenge = details.get("adversarial_challenge_generation", {})
    add("candidate_count", gen.get("candidate_count"), "authoritative finding candidate generation")
    add(
        "candidate_attempt_count_reported",
        gen.get("attempt_count"),
        "candidate generation",
        "caller field, not automatically actual model calls"
        if gen.get("attempt_count") is not None
        else None,
    )
    for name, key in [
        ("candidate_error_messages", "validation_errors"),
        ("semantic_error_messages", "semantic_validation_errors"),
    ]:
        add(
            name,
            len(gen[key]) if key in gen else None,
            "authoritative candidate generation",
            "diagnostic message count; not failed-item rate" if key in gen else None,
        )
    invalid = challenge.get("output_invalid")
    add(
        "challenge_status",
        "REJECT"
        if invalid is True
        else "ACCEPT"
        if invalid is False and challenge.get("challenge_count", 0) > 0
        else "NOT_ENTERED"
        if invalid is False
        else None,
        "authoritative finding challenge generation",
    )
    add(
        "challenge_calls",
        sum(c.prompt_name == "causal_adversarial_challenge" for c in calls) if complete else None,
        "Runtime logical calls filtered by stage",
    )
    add("challenge_attempt_count", challenge.get("attempt_count"), "challenge generation")
    authority = state.get("authoritative_rca_result") or {}
    add("confirmation_status", authority.get("confirmation_status"), "authoritative_rca_result")
    add(
        "published_root_cause",
        authority.get("root_cause"),
        "authoritative_rca_result",
        "no published root" if authority and authority.get("root_cause") is None else None,
    )
    if "root_cause" in authority:
        observations["published_root_cause"]["availability"] = "available"
        observations["published_root_cause"]["reason"] = None
    add("competition_status", authority.get("competition_status"), "authoritative_rca_result")
    add(
        "estimated_cost",
        None,
        "pricing snapshot + billable usage",
        "no validated run-time pricing/cache/billable-failure coverage supplied",
    )
    for key in [
        "caller_retries",
        "output_repairs",
        "first_pass_json",
        "fallback",
        "unknown_evidence_id_rate",
        "candidate_hit",
        "root_cause_hit",
        "correct_conservative",
        "initial_candidate_recall",
    ]:
        add(
            key,
            None,
            "not captured / separate scorer",
            "requires operation/fallback ledger or frozen scoring; never infer from final state",
        )
    diagnostic_path = safe_child(run_dir, f"diagnostics/{case_id}.challenge.json")
    diagnostic = read_json(diagnostic_path) if diagnostic_path.exists() else {}
    taxonomy = []
    for generation in diagnostic.get("records", []):
        for attempt in generation.get("attempts", []):
            taxonomy.append(
                {
                    k: attempt.get(k)
                    for k in [
                        "attempt",
                        "is_repair",
                        "logical_call_id",
                        "outcome",
                        "rejection_codes",
                    ]
                }
            )
    raw_hashes = {
        name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
        for name in ["run_manifest.json", "run_results.json"]
    }
    if result.get("state_file") and state:
        raw_hashes[result["state_file"]] = hashlib.sha256(
            safe_child(run_dir, result["state_file"]).read_bytes()
        ).hexdigest()
    if diagnostic:
        raw_hashes[str(diagnostic_path.relative_to(run_dir.resolve()))] = hashlib.sha256(
            diagnostic_path.read_bytes()
        ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_kind": execution_kind,
        "case_id": case_id,
        "run_id": run_id,
        "provider": provider,
        "model": model,
        "actual_provider": identity.get("provider"),
        "actual_model": identity.get("model"),
        "source_commit": manifest.get("code_commit"),
        "timestamp": manifest.get("created_at"),
        "dataset_id": manifest.get("dataset_id"),
        "prompt_hash": manifest.get("prompt_hash"),
        "config_hash": digest(config),
        "raw_hashes": raw_hashes,
        "ground_truth_loaded": False,
        "ground_truth_class": None,
        "expected_root_cause": None,
        "external_scoring_ref": None,
        "pricing_snapshot": None,
        "observations": observations,
        "calls": [c.model_dump() for c in calls],
        "challenge_diagnostics": taxonomy,
        "diagnostic_coverage": {
            k: diagnostic.get(k) for k in ["complete", "dropped_count", "truncation_count"]
        },
        "funnel": {
            stage: {
                "captured": False,
                "value": None,
                "reason": "requires stage snapshots and external scoring",
            }
            for stage in [
                "initial_raw",
                "initial_admitted",
                "competition",
                "ranking",
                "confirmation",
                "finalizer",
            ]
        },
    }


def distribution(values: list[float], *, p95_min_samples: int = 20) -> dict:
    if p95_min_samples < 2:
        raise ValueError("P95 requires a declared multi-sample policy")
    if any(not math.isfinite(x) for x in values):
        raise ValueError("nonfinite measurement")
    ordered = sorted(values)
    p95 = None
    if len(ordered) >= p95_min_samples:
        h = (len(ordered) - 1) * 0.95
        low, high = math.floor(h), math.ceil(h)
        p95 = ordered[low] + (ordered[high] - ordered[low]) * (h - low)
    return {
        "n": len(ordered),
        "mean": mean(ordered) if ordered else None,
        "median": median(ordered) if ordered else None,
        "p50": median(ordered) if ordered else None,
        "p95": p95,
        "min": min(ordered) if ordered else None,
        "max": max(ordered) if ordered else None,
        "p95_policy": f"linear interpolation; n>={p95_min_samples}; case clustering still applies",
    }


def write_export(record: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)  # Refuse overwrites, including partial exports.
    (output / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    row = {
        k: record.get(k)
        for k in [
            "schema_version",
            "case_id",
            "provider",
            "model",
            "run_id",
            "source_commit",
            "timestamp",
            "execution_kind",
        ]
    }
    for name, observation in record["observations"].items():
        for key in ["value", "availability", "reason"]:
            row[f"{name}.{key}"] = observation[key]
    with (output / "record.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    write_export(
        export_record(args.run_dir, case_id=args.case_id, run_id=args.run_id), args.output_dir
    )


if __name__ == "__main__":
    main()
