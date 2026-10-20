"""Offline benchmark sidecar contract. Never imported by the RCA runtime.

Run --schema PATH to export JSON Schema; validate artifacts with --validate PATH.
No API calls, Ground Truth loading, pricing lookup, or metric inference.
"""

from __future__ import annotations

import argparse
import json
import math
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/provider_abstraction/batch0/metrics.json"
METRICS = json.loads(REGISTRY.read_text(encoding="utf-8"))
MetricName = Enum("MetricName", {key: key for key in METRICS}, type=str)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Measurement(Contract):
    availability: Literal["observed", "partial", "unavailable", "not_applicable"]
    value: float | None = Field(strict=True)
    numerator: int | None = Field(default=None, ge=0, strict=True)
    denominator: int | None = Field(default=None, ge=0, strict=True)
    sample_count: int = Field(ge=0, strict=True)
    unit: Literal[
        "fraction",
        "count",
        "tokens",
        "ms",
        "currency",
        "count_per_case",
        "tokens_per_case",
        "currency_per_case",
        "count_per_call",
    ]
    source_refs: list[str]
    missing_reason: str | None

    @model_validator(mode="after")
    def coherent(self) -> Measurement:
        if self.availability in {"unavailable", "not_applicable"}:
            if self.value is not None or not self.missing_reason:
                raise ValueError("missing metrics require null value and an explicit reason")
            if self.numerator is not None or self.denominator is not None or self.sample_count:
                raise ValueError("missing metrics cannot carry observed counts")
        else:
            if self.value is None or not self.source_refs or self.sample_count < 1:
                raise ValueError("observed/partial metrics require value, samples, and sources")
            if self.value < 0:
                raise ValueError("negative measurement")
            if self.availability == "partial" and not self.missing_reason:
                raise ValueError("partial metrics require a coverage limitation")
            if self.availability == "observed" and self.missing_reason is not None:
                raise ValueError("fully observed metrics cannot have a missing reason")
            if self.unit == "fraction":
                if not 0 <= self.value <= 1:
                    raise ValueError("fraction outside [0,1]")
                if self.numerator is None or self.denominator is None or self.denominator == 0:
                    raise ValueError("rate requires nonzero denominator and numerator")
                if self.numerator > self.denominator:
                    raise ValueError("numerator exceeds denominator")
                if not math.isclose(self.value, self.numerator / self.denominator, abs_tol=1e-6):
                    raise ValueError("rate does not match raw counters")
            if self.unit == "count" and not self.value.is_integer():
                raise ValueError("count must be integral")
        return self


class RunIdentity(Contract):
    run_id: str = Field(min_length=1)
    comparison_kind: Literal["architecture_parity", "model_replacement"]
    execution_kind: Literal["historical_real", "offline_mock", "offline_replay", "real"]
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    code_worktree_clean: bool
    source_snapshot_sha256: str | None = Field(pattern=r"^[0-9a-fA-F]{64}$")
    dataset_id: str
    public_input_hashes: dict[str, str]
    prompt_hashes: dict[str, str]
    selected_case_ids: list[str] = Field(min_length=1)
    replicate: int = Field(ge=1)
    provider: str
    model: str
    orchestration_mode: str
    timeout_seconds: float = Field(gt=0)
    max_http_retries: int = Field(ge=0)
    logical_call_cap: int = Field(ge=1)
    action_budget: int | None = Field(ge=1)
    tool_budget: int | None = Field(ge=1)
    ground_truth_loaded: Literal[False]
    pricing_snapshot_ref: str | None
    currency: str | None
    comparison_limitations: list[str]


class ValidationObservation(Contract):
    stage: Literal["json_decode", "dto", "business", "candidate", "semantic", "evidence_reference"]
    outcome: Literal["passed", "failed", "not_observed", "not_applicable"]
    attempted_items: int | None = Field(ge=0)
    failed_items: int | None = Field(ge=0)
    reason_codes: list[str]
    source_ref: str | None

    @model_validator(mode="after")
    def consistent_counts(self) -> ValidationObservation:
        if self.failed_items is not None and self.attempted_items is not None:
            if self.failed_items > self.attempted_items:
                raise ValueError("failed_items exceeds attempted_items")
        if self.outcome in {"passed", "failed"} and not self.source_ref:
            raise ValueError("validation outcome requires evidence")
        return self


class HTTPAttempt(Contract):
    attempt: int = Field(ge=1, strict=True)
    status_code: int | None = Field(ge=100, le=599)
    outcome: Literal["success", "http_error", "transport_error"]
    normalized_category: str | None
    request_id: str | None
    latency_ms: float | None = Field(ge=0)
    source_ref: str


class FallbackEvent(Contract):
    stage: str
    logical_call_id: str | None
    reason_code: str
    policy_owner: str
    source_ref: str


class TelemetryCoverage(Contract):
    calls_complete: bool
    fallback_events_complete: bool
    dropped_events: int | None = Field(ge=0)
    limitations: list[str]


class CallRecord(Contract):
    logical_call_id: str
    usage_call_id: str | None
    agent: str
    prompt_name: str
    prompt_version: str
    provider: str
    model: str
    operation_id: str | None
    caller_retry_index: int | None = Field(ge=0)
    output_attempt_index: int | None = Field(ge=1)
    http_attempt_count: int | None = Field(ge=0)
    http_attempts: list[HTTPAttempt]
    http_attempts_complete: bool
    transport_outcome: Literal["success", "failed", "not_observed"]
    failure_category: str | None
    stop_reason: str | None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    latency_ms: float | None = Field(ge=0)
    cost: float | None = Field(ge=0)
    validation: list[ValidationObservation]
    source_refs: list[str]

    @model_validator(mode="after")
    def attempt_ledger(self) -> CallRecord:
        numbers = [attempt.attempt for attempt in self.http_attempts]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate HTTP attempt numbers")
        if self.http_attempts_complete:
            if self.http_attempt_count is None or numbers != list(
                range(1, self.http_attempt_count + 1)
            ):
                raise ValueError("complete attempt ledger must match exact attempt count")
        return self


class FunnelPoint(Contract):
    stage: Literal[
        "initial_raw", "initial_admitted", "competition", "ranking", "confirmation", "finalizer"
    ]
    captured: bool
    candidate_ids: list[str]
    source_ref: str | None
    missing_reason: str | None

    @model_validator(mode="after")
    def evidence_required(self) -> FunnelPoint:
        if self.captured:
            if not self.source_ref or self.missing_reason:
                raise ValueError("captured funnel point requires source and no missing reason")
        elif self.candidate_ids or self.source_ref or not self.missing_reason:
            raise ValueError("uncaptured funnel point cannot pretend to be an empty candidate set")
        return self


class BenchmarkCase(Contract):
    schema_version: Literal["0.1"]
    run: RunIdentity
    case_id: str
    acceptance_contract: Literal["strict_qwen", "provider_contract", "not_evaluated"]
    external_scoring_ref: str | None
    metrics: dict[MetricName, Measurement]
    calls: list[CallRecord]
    fallback_events: list[FallbackEvent]
    telemetry_coverage: TelemetryCoverage
    candidate_funnel: list[FunnelPoint]
    diagnostics: dict[str, list[str]]

    @model_validator(mode="after")
    def validate_case(self) -> BenchmarkCase:
        if self.case_id not in self.run.selected_case_ids:
            raise ValueError("case not selected by run")
        if self.acceptance_contract == "strict_qwen" and (
            self.run.provider not in {"dashscope", "qwen"}
            or not self.run.model.lower().startswith("qwen")
        ):
            raise ValueError("another provider/model must not claim strict_qwen")
        ids = [call.logical_call_id for call in self.calls]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate logical call ids")
        if self.telemetry_coverage.calls_complete and self.telemetry_coverage.dropped_events:
            raise ValueError("dropped events cannot be declared complete")
        for call in self.calls:
            if (call.provider, call.model) != (self.run.provider, self.run.model):
                raise ValueError("single-model run identity mismatch")
            if call.cost is not None and not self.run.pricing_snapshot_ref:
                raise ValueError("cost requires a pricing snapshot")
        stages = [point.stage for point in self.candidate_funnel]
        if len(stages) != len(set(stages)):
            raise ValueError("duplicate funnel stage")
        for name, metric in self.metrics.items():
            definition = METRICS[name.value]
            if metric.unit != definition["unit"]:
                raise ValueError(f"{name.value}: unexpected unit")
            if definition["requires_ground_truth"] and metric.value is not None:
                if not self.external_scoring_ref:
                    raise ValueError("quality metrics require external scoring provenance")
            if definition["unit"].startswith("currency") and metric.value is not None:
                if not self.run.pricing_snapshot_ref or not self.run.currency:
                    raise ValueError("cost requires pricing and currency")
        return self


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schema", type=Path)
    group.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.schema:
        schema = BenchmarkCase.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        args.schema.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    else:
        BenchmarkCase.model_validate_json(args.validate.read_text(encoding="utf-8"))
        print("Benchmark sidecar valid (structural + cross-field checks).")


if __name__ == "__main__":
    main()
