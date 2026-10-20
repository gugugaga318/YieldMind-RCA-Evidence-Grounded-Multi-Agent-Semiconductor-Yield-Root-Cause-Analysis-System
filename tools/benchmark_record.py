"""Frozen evaluator/export schema and cost rules; no model execution."""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from typing import Any, Literal

from benchmark_observer import TAXONOMY, repair_metrics
from pydantic import BaseModel, ConfigDict, Field, model_validator

BENCHMARK_VERSION = "development-rca/1.1"
SCORER_VERSION = "formal-r2-scorer/1.1"


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: Any
    availability: Literal["available", "partial", "missing", "not_applicable"]
    reason: str | None = None
    source: str

    @model_validator(mode="after")
    def check(self):
        if self.availability in {"missing", "not_applicable"} and self.value is not None:
            raise ValueError("Unobserved values must be null")
        if self.availability != "available" and not self.reason:
            raise ValueError("Coverage limitation needs a reason")
        return self


class BenchmarkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    benchmark_version: Literal["development-rca/1.1"]
    scorer_version: Literal["formal-r2-scorer/1.1"]
    source_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workflow_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_id: str
    case_revision: str
    exposure_class: Literal["normal_development", "high_exposure", "development_calibration"]
    ground_truth_class: str
    provider: Literal["dashscope", "deepseek"]
    model: str
    actual_provider: str | None
    actual_model: str | None
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    execution_order: int = Field(ge=1)
    timestamp: str
    execution_kind: Literal["offline_fixture", "historical_artifact", "paid_benchmark"]
    pricing_snapshot_id: str
    raw_hashes: dict[str, str]
    job_status: Observation
    contract_status: Observation
    funnel: list[dict]
    quality: Observation
    diagnostics: list[dict]
    repairs: dict
    metrics: dict[str, Observation]
    coverage: dict[str, int]
    observer_complete: bool

    @model_validator(mode="after")
    def paired_identity(self):
        if (self.provider, self.model) not in {
            ("dashscope", "qwen-plus"),
            ("deepseek", "deepseek-v4-pro"),
        }:
            raise ValueError("Invalid provider/model pair")
        return self


def cost_for_call(usage, price, *, cache_reported=False, tariff=None, billable_complete=True):
    """Return a point only with complete billing categories; otherwise explicit range/null."""
    total_in, total_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    base = {
        "currency": price["currency"],
        "snapshot_id": price["snapshot_id"],
        "value": None,
        "lower": None,
        "upper": None,
        "availability": "missing",
        "reason": None,
    }
    if not billable_complete or total_in is None or total_out is None:
        return {**base, "reason": "missing usage or possibly billable failed HTTP attempt"}
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (total_in, total_out)):
        raise ValueError("Invalid usage")
    cached = usage.get("cached_tokens") if cache_reported else None
    if cached is not None and (
        not isinstance(cached, int) or isinstance(cached, bool) or not 0 <= cached <= total_in
    ):
        raise ValueError("Invalid cached tokens")
    if "tiers" in price:
        rates = [next((t for t in price["tiers"] if total_in <= t["input_max"]), None)]
        if rates == [None]:
            return {**base, "reason": "outside frozen price tiers"}
    else:
        if tariff is not None and tariff not in price["rates"]:
            raise ValueError("Unknown price tariff")
        rates = [price["rates"][tariff]] if tariff else list(price["rates"].values())
    values = [
        ((total_in - c) * r["input"] + c * r["cache_hit"] + total_out * r["output"])
        / price["unit_tokens"]
        for r in rates
        for c in ([cached] if cached is not None else [0, total_in])
    ]
    low, high = min(values), max(values)
    exact = cached is not None and len(rates) == 1
    return {
        **base,
        "value": low if exact else None,
        "lower": low,
        "upper": high,
        "availability": "available" if exact else "partial",
        "reason": None if exact else "cache and/or peak tariff unobserved; bounds only",
    }


def cost_ratios(records):
    currencies = {r["cost"]["currency"] for r in records}
    complete = (
        bool(records)
        and len(currencies) == 1
        and all(r["cost"]["value"] is not None for r in records)
    )
    total = sum(r["cost"]["value"] for r in records) if complete else None
    quality = [
        r for r in records if r.get("quality_eligible") and r.get("expected_outcome") == "supported"
    ]
    qc = (
        bool(quality)
        and len({r["cost"]["currency"] for r in quality}) == 1
        and all(
            r["cost"]["value"] is not None and r.get("correct_root") is not None for r in quality
        )
    )
    qtotal = sum(r["cost"]["value"] for r in quality) if qc else None

    def divide(value, n):
        return value / n if value is not None and n else None

    return {
        "cost_per_run": divide(total, len(records)),
        "cost_per_completed_case": divide(
            total, sum(r["job_status"] == "completed" for r in records)
        ),
        "cost_per_contract_accepted_case": divide(
            total, sum(r["contract_status"] == "ACCEPT" for r in records)
        ),
        "cost_per_correct_case": divide(
            qtotal, sum(r.get("correct_root") is True for r in quality)
        ),
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
    }


def coverage_summary(observations):
    return {
        s: sum(o["availability"] == s for o in observations.values())
        for s in ("available", "partial", "missing", "not_applicable")
    }


def export_sidecar(base, capture, challenge):
    """Merge existing Challenge diagnostics without revalidating any evidence."""
    diagnostics = list(capture["diagnostics"])
    for generation in challenge.get("records", []):
        for attempt in generation.get("attempts", []):
            diagnostics.append(
                {
                    "stage": "challenge",
                    "generation": generation.get("generation"),
                    **{
                        k: attempt.get(k)
                        for k in (
                            "attempt",
                            "is_repair",
                            "logical_call_id",
                            "outcome",
                            "rejection_codes",
                            "validations",
                        )
                    },
                }
            )
    capture = copy.deepcopy(capture)
    indexed = {
        a.get("logical_call_id"): a
        for o in capture["operations"]
        for a in o["attempts"]
        if a.get("logical_call_id")
    }
    for d in diagnostics:
        if d.get("logical_call_id") in indexed and d.get("rejection_codes"):
            indexed[d["logical_call_id"]]["validation_codes"] = d["rejection_codes"]
    metrics = dict(base["metrics"])
    positives = [o for o in capture["operations"] if o["outcome"] == "fallback"]
    final = [s for s in capture["snapshots"] if s["stage"] == "finalizer_output"]
    facts = final[-1].get("fallback_ledger") if final else None
    observed = bool(positives or facts)
    known_negative = capture["complete"] and facts is not None
    metrics["fallback"] = {
        "value": observed if observed or known_negative else None,
        "availability": "available" if observed or known_negative else "missing",
        "reason": None if observed or known_negative else "no complete final fallback ledger",
        "source": "observed operation outcomes + persisted executed fallback markers",
    }
    metrics["fallback_count"] = {
        "value": max(len(positives), len(facts or [])) if observed or known_negative else None,
        "availability": "partial" if observed else "available" if known_negative else "missing",
        "reason": "observed lower bound; overlapping operation/finding markers not added"
        if observed
        else None
        if known_negative
        else "no complete final fallback ledger",
        "source": "operation/final ledger; boolean rate is canonical",
    }
    # Core KPI coverage, never force all 63 metrics to available.
    seen = {s["stage"] for s in capture["snapshots"]}
    for stage in (
        "initial_raw_candidate",
        "admitted_candidate",
        "competition_survival",
        "ranking_survival",
        "confirmation_status",
        "finalizer_output",
    ):
        observed = stage in seen
        metrics[stage] = {
            "value": observed if capture["complete"] else None,
            "availability": "available" if observed and capture["complete"] else "missing",
            "reason": None
            if observed and capture["complete"]
            else "stage not reached or incomplete capture",
            "source": "benchmark sidecar boundary",
        }
        if metrics[stage]["availability"] == "missing":
            metrics[stage]["value"] = None
    for code in TAXONOMY:
        hits = sum(code in (d.get("rejection_codes") or d.get("codes") or []) for d in diagnostics)
        metrics[code] = {
            "value": hits,
            "availability": "partial",
            "reason": (
                "observed diagnostic counts; not a globally complete evaluated-relation denominator"
            ),
            "source": "existing diagnostics; no independent validator",
        }
    return BenchmarkRecord.model_validate(
        {
            **base,
            "funnel": capture["snapshots"],
            "diagnostics": diagnostics,
            "repairs": repair_metrics(capture),
            "metrics": metrics,
            "observer_complete": capture["complete"],
            "coverage": coverage_summary(metrics),
        }
    )


def write_record(record: BenchmarkRecord, directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    data = record.model_dump(mode="json")
    (directory / "record.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    flat = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
    for name, obs in data["metrics"].items():
        flat[name + ".value"] = obs["value"]
        flat[name + ".availability"] = obs["availability"]
        flat[name + ".reason"] = obs["reason"]
    with (directory / "record.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(flat))
        w.writeheader()
        w.writerow(flat)


def export_run(
    run_dir: Path,
    sidecar_file: Path,
    manifest: dict,
    *,
    run_id: str,
    reference_file: Path,
    pricing: dict,
    execution_kind="offline_fixture",
):
    from benchmark_observer import sha
    from benchmark_scoring import canonicalize_funnel, load_reference, score_run
    from model_benchmark_phase0 import export_record
    from model_benchmark_telemetry import runtime_call_records

    if (
        manifest.get("schema_hash")
        and sha(BenchmarkRecord.model_json_schema()) != manifest["schema_hash"]
    ):
        raise ValueError("Record schema differs from freeze")
    if "config" in manifest and sha(manifest["config"]) != manifest["config_hash"]:
        raise ValueError("Run config differs from freeze")
    planned = next(r for r in manifest["runs"] if r["run_id"] == run_id)
    if sha(pricing) != manifest["pricing_hash"]:
        raise ValueError("Pricing snapshot differs from freeze")
    reference = load_reference(reference_file, manifest["rubric_hash"])
    case = next(c for c in reference["cases"] if c["case_id"] == planned["case_id"])
    row = export_record(
        run_dir, case_id=planned["case_id"], run_id=run_id, execution_kind=execution_kind
    )
    if (row["provider"], row["model"]) != (planned["provider"], planned["model"]):
        raise ValueError("Run differs from planned pair")
    if row["source_commit"] != manifest["source_commit"]:
        raise ValueError("Historical/mixed source must not enter this iteration")
    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    verification = sidecar.get("source_verification", {})
    if execution_kind == "paid_benchmark" and (
        verification.get("verified") is not True
        or verification.get("manifest_hash") != sha(manifest)
    ):
        raise ValueError("Paid record requires execution-time source/data freeze verification")
    if execution_kind == "paid_benchmark" and (
        manifest.get("requires_commit_binding_before_paid_run", True)
        or not manifest.get("paid_execution_authorized", False)
    ):
        raise ValueError("Paid manifest is not authorized and bound to a committed iteration")
    capture = sidecar["capture"]
    metrics = {
        k: {
            **v,
            "availability": {"unavailable": "missing"}.get(v["availability"], v["availability"]),
        }
        for k, v in row["observations"].items()
    }
    telemetry = sidecar["model_telemetry"]
    complete = telemetry.get("complete") is True and telemetry.get("dropped_count") == 0
    calls = runtime_call_records(telemetry) if telemetry.get("events") else []

    def add(key, value, source, availability=None, reason=None):
        metrics[key] = {
            "value": value,
            "availability": availability or ("available" if value is not None else "missing"),
            "reason": reason if reason else None if value is not None else "not observed",
            "source": source,
        }

    attempts = [e for e in telemetry.get("events", []) if e["kind"] == "http_attempt"]
    add("logical_calls", len(calls) if complete else None, "outer Runtime sidecar")
    add("http_attempts", len(attempts) if complete else None, "outer HTTP sidecar")
    add(
        "http_retries",
        sum(max(0, len(c.http_attempts) - 1) for c in calls) if complete else None,
        "outer HTTP sidecar",
    )
    billable = complete and all(e.get("outcome") == "success" for e in attempts)
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [getattr(c, field) for c in calls]
        add(
            field,
            sum(values) if billable and all(v is not None for v in values) else None,
            "outer usage sidecar",
        )
    price = next(
        p
        for p in pricing["models"]
        if (p["provider"], p["model"]) == (planned["provider"], planned["model"])
    )
    costs = [
        cost_for_call(
            {"prompt_tokens": c.input_tokens, "completion_tokens": c.output_tokens},
            price,
            billable_complete=billable,
        )
        for c in calls
    ]
    valid = bool(costs) and all(c["lower"] is not None for c in costs)
    cost = {
        "currency": price["currency"],
        "snapshot_id": price["snapshot_id"],
        "value": None,
        "lower": sum(c["lower"] for c in costs) if valid else None,
        "upper": sum(c["upper"] for c in costs) if valid else None,
    }
    add(
        "cost",
        cost,
        "frozen prices + outer usage",
        "partial" if valid else "missing",
        "cache presence/tariff not guaranteed by frozen Adapter usage; no point cost",
    )
    if not valid:
        metrics["cost"]["value"] = None
    add("workflow_latency_ms", sidecar.get("workflow_latency_ms"), "outer workflow timer")
    finals = [s for s in capture["snapshots"] if s["stage"] == "finalizer_output"]
    authority = (finals[-1].get("authoritative_rca_result") or {}) if finals else {}
    roots = [root for c in reference["cases"] for root in c["roots"]]
    quality = score_run(
        case,
        job_status=metrics["job_status"]["value"],
        authority=authority,
        snapshots=capture["snapshots"],
        all_roots=roots,
    )
    capture = copy.deepcopy(capture)
    capture["snapshots"] = canonicalize_funnel(case, capture["snapshots"], roots)
    quality_obs = {
        "value": quality,
        "availability": "partial"
        if any(
            v is None
            for k, v in quality.items()
            if k in {"correct_terminal_decision", "candidate_recall"}
            and case["expected_outcome"] == "supported"
        )
        else "available",
        "source": "external frozen rubric",
        "reason": None,
    }
    if not capture["complete"]:
        quality_obs.update(
            value=None, availability="missing", reason="incomplete observer; no quality inference"
        )
    if quality_obs["availability"] == "partial":
        quality_obs["reason"] = (
            "unmapped mechanism or missing dimension/stage; adjudication required"
        )
    base = {
        "benchmark_version": manifest["benchmark_version"],
        "scorer_version": reference["scorer_version"],
        "source_commit": manifest["source_commit"],
        "prompt_hash": manifest["prompt_hash"],
        "workflow_hash": manifest["workflow_hash"],
        "config_hash": manifest["config_hash"],
        "rubric_hash": manifest["rubric_hash"],
        "case_id": case["case_id"],
        "case_revision": case["case_revision"],
        "exposure_class": case["exposure_class"],
        "ground_truth_class": case["ground_truth_class"],
        "provider": row["provider"],
        "model": row["model"],
        "actual_provider": row["actual_provider"],
        "actual_model": row["actual_model"],
        "run_id": run_id,
        "execution_order": planned["order_index"],
        "timestamp": row["timestamp"],
        "execution_kind": execution_kind,
        "pricing_snapshot_id": price["snapshot_id"],
        "raw_hashes": {**row["raw_hashes"], "benchmark_sidecar": sha(sidecar)},
        "job_status": metrics["job_status"],
        "contract_status": metrics["contract_status"],
        "quality": quality_obs,
        "metrics": metrics,
    }
    return export_sidecar(base, capture, sidecar["challenge"])
