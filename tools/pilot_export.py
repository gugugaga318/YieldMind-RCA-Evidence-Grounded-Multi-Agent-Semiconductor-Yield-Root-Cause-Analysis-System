"""Benchmark-only exporter identity bridge. Scoring stays in the frozen exporter."""

from datetime import UTC, datetime

from benchmark_observer import sha
from benchmark_record import BenchmarkRecord, export_run
from pilot_execution_binding import export_manifest, validate_context


def run_envelope(context, manifest, authorization, planned, pricing_snapshot_id, *, dry_run):
    data = validate_context(context, manifest, authorization)
    if planned not in manifest["runs"]:
        raise ValueError("Run not authorized")
    missing = {
        "value": None,
        "availability": "missing",
        "reason": "dispatch only; no result",
        "source": "launch envelope",
    }
    identity = {
        "value": data,
        "availability": "available",
        "source": "trusted preflight",
        "reason": None,
    }
    return BenchmarkRecord.model_validate(
        {
            "benchmark_version": manifest["benchmark_version"],
            "scorer_version": manifest["scorer_version"],
            "source_commit": data["execution_head"],
            **{
                k: manifest[k]
                for k in ("prompt_hash", "workflow_hash", "config_hash", "rubric_hash")
            },
            "case_id": planned["case_id"],
            "case_revision": "formal_v1_candidate_r2",
            "exposure_class": "development_calibration"
            if planned["case_id"] in manifest["calibration_cases"]
            else "normal_development",
            "ground_truth_class": "partial_source_with_frozen_rubric",
            "provider": planned["provider"],
            "model": planned["model"],
            "actual_provider": None,
            "actual_model": None,
            "run_id": ("dryrun_" if dry_run else "") + planned["run_id"],
            "execution_order": planned["order_index"],
            "timestamp": datetime.now(UTC).isoformat(),
            "execution_kind": "offline_fixture" if dry_run else "paid_benchmark",
            "pricing_snapshot_id": pricing_snapshot_id,
            "raw_hashes": {"execution_context": sha(data)},
            "job_status": missing,
            "contract_status": missing,
            "quality": missing,
            "metrics": {"execution_identity": identity},
            "funnel": [],
            "diagnostics": [],
            "repairs": {},
            "coverage": {"available": 1, "missing": 3},
            "observer_complete": False,
        }
    )


def export_bound_run(run_dir, sidecar_file, context, manifest, authorization, **kwargs):
    """Evaluator-only, after runtime exits. Does not change any scoring decision."""
    if context.to_dict()["dry_run"]:
        raise ValueError("Dry run cannot export a paid result")
    view = export_manifest(context, manifest, authorization)
    record = export_run(run_dir, sidecar_file, view, execution_kind="paid_benchmark", **kwargs)
    payload = record.model_dump()
    payload["metrics"]["execution_identity"] = {
        "value": context.to_dict(),
        "availability": "available",
        "source": "trusted preflight",
        "reason": None,
    }
    payload["coverage"]["available"] += 1
    return BenchmarkRecord.model_validate(payload)
