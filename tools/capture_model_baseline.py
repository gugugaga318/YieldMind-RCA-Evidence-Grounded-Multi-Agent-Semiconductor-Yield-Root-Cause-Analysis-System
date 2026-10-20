"""Capture Batch 0 artifacts only on exact frozen source. Offline, no credentials."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "8d9192f2769f0c6843f8944a84dd280a301f8ed9"
DOCS = ROOT / "docs/provider_abstraction/batch0"
RUN = "outputs/transport120_8d9192f_FORMAL012_20260913_213143_0249400"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    if git("rev-parse", "HEAD") != BASE:
        raise SystemExit("Only exact baseline HEAD can generate the frozen oracle.")
    if git("diff", "HEAD", "--name-only", "--", "core", "backend", "scripts", "pyproject.toml"):
        raise SystemExit("Production source differs from baseline; refusing oracle regeneration.")
    untracked = git("ls-files", "--others", "--exclude-standard", "--", "core", "backend")
    if untracked:
        raise SystemExit("Untracked runtime source present; refusing oracle regeneration.")

    harness = load_module(
        "batch0_workflow_capture", ROOT / "tests/integration/test_provider_workflow_baseline.py"
    )
    scenarios = {name: harness.capture(name) for name in harness.SCENARIOS}
    assert scenarios == {name: harness.capture(name) for name in harness.SCENARIOS}, (
        "Nondeterministic capture; do not hide fields to force equality."
    )
    save(harness.FIXTURE, {"base_commit": BASE, "scenarios": scenarios})

    paths = [
        p
        for prefix in ("core", "backend", "scripts")
        for p in (ROOT / prefix).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    paths += list((ROOT / "core/yield_rca_core/prompts").glob("*.md"))
    source_hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}
    source_snapshot = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    prompt_hashes = {k: v for k, v in source_hashes.items() if "/prompts/" in k}
    original_run = source / RUN
    manifest = read(original_run / "run_manifest.json")
    results = read(original_run / "run_results.json")
    state = read(original_run / "states/FORMAL_012.json")
    shell = read(source / (RUN + "_shell") / "baseline.json")
    assert manifest["code_commit"] == BASE and manifest["code_worktree_clean"] is True
    assert results["results"][0]["strict_qwen_accepted"] is True
    result = results["results"][0]
    metadata = state["execution_metadata"]
    finding = next(
        f for f in state["findings"] if f["finding_id"] == state["authoritative_rca_finding_id"]
    )
    candidate = finding["details"]["hypothesis_candidate_generation"]
    registry = read(DOCS / "metrics.json")
    metrics = {}
    for name, definition in registry.items():
        reason = "Not captured with a complete denominator in the historical artifact."
        if definition["requires_ground_truth"]:
            reason = "External scoring unavailable; no private Ground Truth loaded."
        if definition["unit"].startswith("currency"):
            reason = "No reliable pricing snapshot or complete billable-attempt usage."
        metrics[name] = {
            "availability": "unavailable",
            "value": None,
            "numerator": None,
            "denominator": None,
            "sample_count": 0,
            "unit": definition["unit"],
            "source_refs": [],
            "missing_reason": reason,
        }

    def set_metric(name, value, ref, partial=None, n=None, d=None, samples=1):
        metrics[name].update(
            availability="partial" if partial else "observed",
            value=value,
            numerator=n,
            denominator=d,
            sample_count=samples,
            source_refs=[ref],
            missing_reason=partial,
        )

    rr = str(original_run / "run_results.json")
    sr = str(original_run / "states/FORMAL_012.json")
    set_metric("completion_rate", 1.0, rr, n=1, d=1)
    set_metric("strict_accept_rate", 1.0, rr, n=1, d=1)
    set_metric("provider_failure_rate", 0.0, rr, n=0, d=1)
    set_metric(
        "fallback_rate",
        0.0,
        rr,
        partial="No orchestration/candidate fallback; unified all-stage coverage absent.",
        n=0,
        d=1,
    )
    counts = {
        "action_count": result["action_count"],
        "tool_count": metadata["tool_call_count"],
        "evidence_count": result["evidence_count"],
        "logical_llm_calls": metadata["llm_call_count"],
    }
    for name, value in counts.items():
        limitation = (
            "Legacy count is usage events; admitted wrapper calls not persisted."
            if name == "logical_llm_calls"
            else None
        )
        set_metric(name, value, sr, partial=limitation)
        set_metric(name + "_per_case", value, sr, partial=limitation)
    usage = state["llm_usage"]
    for name, key in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        value = sum(event[key] for event in usage)
        limitation = "Reported usage only; missing usage and successful-retry billing unknown."
        set_metric(name, value, sr, partial=limitation, samples=len(usage))
        set_metric(name + "_per_case", value, sr, partial=limitation)
    for scope, values in (
        ("call", sorted(event["latency_ms"] for event in usage)),
        ("case", [metadata["workflow_duration_ms"]]),
    ):
        for stat, fraction in (
            ("min", 0),
            ("median", 0.5),
            ("p50", 0.5),
            ("p95", 0.95),
            ("max", 1),
        ):
            position = (len(values) - 1) * fraction
            lower = int(position)
            upper = min(lower + 1, len(values) - 1)
            value = values[lower] + (values[upper] - values[lower]) * (position - lower)
            set_metric(f"{scope}_latency_{stat}", value, sr, samples=len(values))

    envelope = {
        "schema_version": "0.1",
        "run": {
            "run_id": "historical-qwen-8d9192f-formal012",
            "comparison_kind": "architecture_parity",
            "execution_kind": "historical_real",
            "code_commit": BASE,
            "base_commit": BASE,
            "code_worktree_clean": True,
            "source_snapshot_sha256": source_snapshot,
            "dataset_id": manifest["dataset_id"],
            "public_input_hashes": {
                entry["path"]: entry["sha256"]
                for entry in manifest["input_boundary"]["allowed_files"]
            },
            "prompt_hashes": prompt_hashes,
            "selected_case_ids": ["FORMAL_012"],
            "replicate": 1,
            "provider": "dashscope",
            "model": "qwen-plus",
            "orchestration_mode": "llm_react",
            "timeout_seconds": float(shell["timeout_env"]),
            "max_http_retries": shell["effective_max_retries"],
            "logical_call_cap": 20,
            "action_budget": state.get("investigation_goal", {}).get("max_steps"),
            "tool_budget": state.get("investigation_goal", {}).get("max_tool_calls"),
            "ground_truth_loaded": False,
            "pricing_snapshot_ref": None,
            "currency": None,
            "comparison_limitations": [
                "Historical single-case reference, not a new A/B or model-quality score.",
                "development_regression; prior development exposure recorded by original manifest.",
                "Strict ACCEPT coexists with isolated candidate and semantic validation errors.",
                "Source snapshot recaptured; historical environment not reconstructed.",
                "Call-stage, successful HTTP attempts, retry/repair ledger and pricing are absent.",
                "Small-n quantiles are descriptive only; no performance threshold is established.",
            ],
        },
        "case_id": "FORMAL_012",
        "acceptance_contract": "strict_qwen",
        "external_scoring_ref": None,
        "metrics": metrics,
        "calls": [],
        "fallback_events": [],
        "telemetry_coverage": {
            "calls_complete": False,
            "fallback_events_complete": False,
            "dropped_events": None,
            "limitations": ["Historical usage is not a complete stage/attempt event ledger."],
        },
        "candidate_funnel": [
            {
                "stage": stage,
                "captured": False,
                "candidate_ids": [],
                "source_ref": None,
                "missing_reason": "Complete stage snapshot and lineage absent.",
            }
            for stage in (
                "initial_raw",
                "initial_admitted",
                "competition",
                "ranking",
                "confirmation",
            )
        ]
        + [
            {
                "stage": "finalizer",
                "captured": True,
                "candidate_ids": [],
                "source_ref": sr + "#/authoritative_rca_result",
                "missing_reason": None,
            }
        ],
        "diagnostics": {
            "candidate_validation_errors": candidate["validation_errors"],
            "semantic_profile_validation_errors": candidate["semantic_validation_errors"],
            "strict_qwen_rejection_reasons": result["strict_qwen_rejection_reasons"],
            "funnel_note": [
                "Inconclusive Finalizer: no published root cause or inferred GT match."
            ],
        },
    }
    contract = load_module("batch0_benchmark_contract", ROOT / "tools/model_benchmark_contract.py")
    validated = contract.BenchmarkCase.model_validate(envelope)
    save(DOCS / "historical_scorecard.json", validated.model_dump(mode="json"))
    schema = contract.BenchmarkCase.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    save(DOCS / "benchmark.schema.json", schema)
    artifacts = [
        original_run / "run_manifest.json",
        original_run / "run_results.json",
        original_run / "states/FORMAL_012.json",
        source / (RUN + "_shell") / "baseline.json",
    ]
    save(
        DOCS / "baseline.json",
        {
            "base_commit": BASE,
            "branch": git("branch", "--show-current"),
            "worktree": str(ROOT),
            "initial_clean_verified": True,
            "production_diff": [],
            "runtime_import_root": str(ROOT / "core"),
            "python": platform.python_version(),
            "source_snapshot_sha256": source_snapshot,
            "source_sha256": source_hashes,
            "historical_evidence": {str(path): sha(path) for path in artifacts},
            "workflow_fixture_sha256": sha(harness.FIXTURE),
            "approved_scope_sha256": sha(DOCS / "APPROVED_SCOPE.md"),
            "real_api_calls_this_batch": 0,
            "notes": [
                "No Batch 1 implementation; baseline is not proof of new-architecture parity.",
                "Full payload hashes; clocks/UUID fixed at generation sources.",
                "Replay inputs are local public fixtures, not proposed commit content.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "workflow_scenarios": len(scenarios),
                "metrics": len(metrics),
                "historical_llm_usage_events": len(usage),
                "real_api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
