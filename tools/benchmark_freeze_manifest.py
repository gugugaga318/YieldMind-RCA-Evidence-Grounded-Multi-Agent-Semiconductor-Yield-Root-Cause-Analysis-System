"""Build evaluator-side Phase 0.5 freeze artifacts, without running a workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from benchmark_observer import sha
from benchmark_record import BENCHMARK_VERSION, SCORER_VERSION, BenchmarkRecord

PRIMARY = ["FORMAL_" + x for x in ("001", "003", "005", "010", "015", "019", "021", "024")]
PAIRS = [("dashscope", "qwen-plus"), ("deepseek", "deepseek-v4-pro")]


def build(repo: Path, reference: dict):
    folder = repo / "docs/model_benchmark"
    freeze = json.loads((folder / "phase0/freeze.json").read_text(encoding="utf-8"))
    plan = json.loads((folder / "phase0/pilot_plan.json").read_text(encoding="utf-8"))
    inventory = json.loads((folder / "phase0/dataset_inventory.json").read_text(encoding="utf-8"))
    packet = next(x for x in inventory["packets"] if x["packet"] == "formal_v1_candidate_r2")
    pricing = json.loads((folder / "phase05/pricing_snapshot.json").read_text(encoding="utf-8"))
    schema = BenchmarkRecord.model_json_schema()
    config = {
        **plan["config"],
        "pricing_snapshot": pricing["version"],
        "observation": "dedicated single-thread workflow with outer sidecar",
        "gt_runtime_access": "Python audit guard; scorer never imported by runtime",
    }
    runs = []
    for i, case_id in enumerate([*PRIMARY, "FORMAL_012"]):
        for provider, model in PAIRS if i % 2 == 0 else reversed(PAIRS):
            runs.append(
                {
                    "run_id": f"pilot_v1_{case_id}_{provider}_r1",
                    "case_id": case_id,
                    "provider": provider,
                    "model": model,
                    "replicate": 1,
                    "order_index": len(runs) + 1,
                    "scheduled_status": "not_run",
                }
            )
    instrumentation = {
        str(p.relative_to(repo)).replace("\\", "/"): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in sorted((repo / "tools").glob("benchmark_*.py"))
    }
    manifest = {
        "benchmark_version": BENCHMARK_VERSION,
        "scorer_version": SCORER_VERSION,
        "source_commit": "cdbc07fc37c8544c56ae7b9090166edaff838c37",
        "production_source_commit": freeze["source_commit"],
        "branch": freeze["branch"],
        "requires_commit_binding_before_paid_run": True,
        "paid_execution_authorized": False,
        "source_hashes": {**freeze["source_hashes"], **instrumentation},
        "public_hashes": packet["public_hashes"],
        "public_snapshot_hash": packet["public_snapshot_sha256"],
        "prompt_hash": freeze["prompt_bundle_sha256"],
        "workflow_hash": sha(
            {k: v for k, v in freeze["source_hashes"].items() if k.startswith("core/")}
        ),
        "rubric_hash": sha(reference),
        "pricing_hash": sha(pricing),
        "schema_hash": sha(schema),
        "adjudication_protocol_hash": hashlib.sha256(
            (folder / "phase05/ADJUDICATION_PROTOCOL.md").read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "config": config,
        "config_hash": sha(config),
        "primary_cases": PRIMARY,
        "calibration_cases": ["FORMAL_012"],
        "runs": runs,
        "order_policy": "ascending case blocks, alternating first provider, calibration last",
        "infrastructure_hashes": instrumentation,
    }
    audit = json.loads((folder / "phase0/telemetry_audit.json").read_text(encoding="utf-8"))
    for name, metric in audit["metrics"].items():
        metric["phase0_status"] = metric["status"]
        metric["status"] = {"partially_available": "partial"}.get(
            metric["status"], metric["status"]
        )
        if metric["status"] == "missing":
            metric["status"] = "partial"
            if name in {
                "candidate_recall",
                "admitted_candidate_recall",
                "competition_survival",
                "ranking_survival",
                "confirmation_survival",
            }:
                metric["source"] = "tools/benchmark_observer.py + tools/benchmark_scoring.py"
                metric["limitation"] = (
                    "Captured boundaries; unknown semantics need review. GT approval pending."
                )
            elif "cost" in name:
                metric["source"] = "tools/benchmark_record.py:cost_for_call/cost_ratios"
                metric["limitation"] = (
                    "Rates frozen; incomplete cache/tariff gives bounds; missing usage gives null."
                )
            else:
                metric["source"] = "tools/benchmark_observer.py:repair_metrics"
                metric["limitation"] = (
                    "Observed operation lineage only; requires complete dedicated-thread capture."
                )
        if name.startswith("output_repairs"):
            metric["source"] = "tools/benchmark_observer.py:repair_metrics"
            metric["limitation"] = (
                "Observed initial/repair/retry lineage; unobserved paths remain unknown."
            )
    audit["counts"] = {
        s: sum(m["status"] == s for m in audit["metrics"].values())
        for s in ("available", "partial", "missing", "not_applicable")
    }
    audit["scope"] = "63 capability definitions; actual per-record coverage exported separately."
    audit["source_commit"] = manifest["source_commit"]
    readiness = {
        "scorer_version": SCORER_VERSION,
        "rubric_hash": sha(reference),
        "counts": dict(Counter(c["expected_outcome"] for c in reference["cases"])),
        "exposure_counts": dict(Counter(c["exposure_class"] for c in reference["cases"])),
        "cases": [
            {
                k: c[k]
                for k in (
                    "case_id",
                    "case_revision",
                    "expected_outcome",
                    "exposure_class",
                    "ground_truth_class",
                    "source_review_status",
                    "rubric_review_status",
                    "quality_primary_eligible",
                )
            }
            | {"readiness": "rubric ready for review; unmapped prose requires adjudication"}
            for c in reference["cases"]
        ],
        "primary_category_counts": dict(
            Counter(c["expected_outcome"] for c in reference["cases"] if c["case_id"] in PRIMARY)
        ),
        "gt_answers_omitted": True,
    }
    return {
        "manifest.json": manifest,
        "record.schema.json": schema,
        "telemetry_audit.json": audit,
        "scoring_readiness.json": readiness,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    artifacts = build(args.repo, reference)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        path = args.output_dir / name
        with path.open("x", encoding="utf-8") as f:
            f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(
        "Generated freeze artifacts; no model execution. Coverage:",
        artifacts["telemetry_audit.json"]["counts"],
    )


if __name__ == "__main__":
    main()
