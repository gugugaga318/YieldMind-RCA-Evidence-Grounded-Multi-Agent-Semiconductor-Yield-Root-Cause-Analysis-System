"""Benchmark artifact validity: missing data must never look like a passing score."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "model_benchmark_contract", ROOT / "tools/model_benchmark_contract.py"
)
assert spec and spec.loader
contract = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)

DOCS = ROOT / "docs/provider_abstraction/batch0"


def example():
    return json.loads((DOCS / "historical_scorecard.json").read_text(encoding="utf-8"))


def test_historical_artifact_and_complete_metric_inventory():
    data = example()
    validated = contract.BenchmarkCase.model_validate(data)
    assert set(data["metrics"]) == set(contract.METRICS)
    assert validated.run.ground_truth_loaded is False
    assert data["metrics"]["root_cause_hit_rate"]["value"] is None
    assert data["metrics"]["first_pass_json_success_rate"]["value"] is None


def test_exported_schema_matches_model():
    generated = contract.BenchmarkCase.model_json_schema()
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert json.loads((DOCS / "benchmark.schema.json").read_text()) == generated


@pytest.mark.parametrize("bad_value", [0, 1, 0.5])
def test_unavailable_cannot_become_a_number(bad_value):
    data = example()
    data["metrics"]["candidate_recall"]["value"] = bad_value
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_zero_denominator_cannot_be_perfect():
    data = example()
    data["metrics"]["completion_rate"]["denominator"] = 0
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_rate_requires_matching_raw_counters():
    data = example()
    data["metrics"]["completion_rate"]["numerator"] = 0
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_other_provider_cannot_claim_strict_qwen():
    data = example()
    data["run"]["provider"] = "deepseek"
    data["run"]["model"] = "another-model"
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_missing_funnel_is_not_an_empty_observed_candidate_set():
    data = example()
    data["candidate_funnel"][0]["captured"] = True
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_cost_requires_pricing_and_currency():
    data = example()
    value = copy.deepcopy(data["metrics"]["completion_rate"])
    value.update(unit="currency", numerator=None, denominator=None)
    data["metrics"]["cost"] = value
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_ground_truth_metric_requires_external_scoring():
    data = example()
    data["metrics"]["candidate_recall"] = copy.deepcopy(data["metrics"]["completion_rate"])
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_unknown_metric_rejected():
    data = example()
    data["metrics"]["invented_score"] = data["metrics"]["completion_rate"]
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_partial_value_requires_coverage_reason():
    data = example()
    data["metrics"]["completion_rate"]["availability"] = "partial"
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_boolean_is_not_a_numeric_score():
    data = example()
    data["metrics"]["completion_rate"]["value"] = True
    with pytest.raises(ValidationError):
        contract.BenchmarkCase.model_validate(data)


def test_incomplete_attempt_ledger_cannot_claim_complete():
    attempt = {
        "attempt": 2,
        "status_code": 200,
        "outcome": "success",
        "normalized_category": None,
        "request_id": None,
        "latency_ms": 10.0,
        "source_ref": "mock#http2",
    }
    call = {
        "logical_call_id": "call1",
        "usage_call_id": None,
        "agent": "planner",
        "prompt_name": "planner",
        "prompt_version": "v1",
        "provider": "dashscope",
        "model": "qwen-plus",
        "operation_id": None,
        "caller_retry_index": None,
        "output_attempt_index": None,
        "http_attempt_count": 2,
        "http_attempts": [attempt],
        "http_attempts_complete": True,
        "transport_outcome": "success",
        "failure_category": None,
        "stop_reason": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "latency_ms": 20.0,
        "cost": None,
        "validation": [],
        "source_refs": ["mock"],
    }
    with pytest.raises(ValidationError):
        contract.CallRecord.model_validate(call)
    call["http_attempts_complete"] = False
    assert contract.CallRecord.model_validate(call).http_attempt_count == 2
