"""Offline exporter correctness; no workflow, Ground Truth or provider call."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from model_benchmark_phase0 import distribution, export_record, make_plan, write_export


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 0 cannot call a provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def fixture(tmp_path, *, reported=True, complete=True, outcome="success"):
    identity = {"provider": "deepseek", "model": "deepseek-v4-pro"}
    events = []
    for kind in ["logical_call", "http_attempt", "model_result"]:
        event = {
            **identity,
            "logical_call_id": "call_1",
            "kind": kind,
            "stage": "causal_adversarial_challenge",
            "agent": "rca_reasoning",
            "prompt_version": "v1",
        }
        if kind == "http_attempt":
            event.update(http_attempt=1, outcome="success", status_code=200, latency_ms=20)
        if kind == "model_result":
            event.update(
                outcome=outcome,
                latency_ms=21,
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "reported_fields": ["prompt_tokens", "completion_tokens", "total_tokens"]
                    if reported
                    else [],
                },
            )
        events.append(event)
    state = {
        "execution_metadata": {
            "model_telemetry": {
                "events": events,
                "complete": complete,
                "dropped_count": 0 if complete else 1,
            },
            "model_identity": {**identity, "basis": "runtime_config", "author": "model"},
            "tool_call_count": 2,
            "workflow_duration_ms": 30,
        },
        "action_history": [],
        "evidence": [],
        "authoritative_rca_finding_id": "good",
        "findings": [
            {
                "finding_id": "good",
                "details": {
                    "hypothesis_candidate_generation": {"candidate_count": 2},
                    "adversarial_challenge_generation": {
                        "challenge_count": 1,
                        "output_invalid": False,
                        "attempt_count": 1,
                    },
                },
            },
            {
                "finding_id": "historical-last",
                "details": {"hypothesis_candidate_generation": {"candidate_count": 99}},
            },
        ],
        "authoritative_rca_result": {"root_cause": None, "confirmation_status": "inconclusive"},
    }
    manifest = {
        "code_commit": "a" * 40,
        "selected_case_ids": ["CASE_A"],
        "configuration": identity,
        "created_at": "2026-10-07T00:00:00Z",
    }
    result = {
        "case_id": "CASE_A",
        "state_file": "state.json",
        "job_status": "completed",
        "model_contract_accepted": True,
        "strict_qwen_accepted": True,
        "provider_failure": False,
    }
    for name, obj in [
        ("run_manifest.json", manifest),
        ("run_results.json", {"results": [result]}),
        ("state.json", state),
    ]:
        (tmp_path / name).write_text(json.dumps(obj))
    return state, result


def export(tmp_path):
    return export_record(tmp_path, case_id="CASE_A", run_id="offline_1")


def test_available_facts_and_authority_pointer(tmp_path):
    fixture(tmp_path)
    row = export(tmp_path)
    o = row["observations"]
    assert o["logical_calls"]["value"] == o["http_attempts"]["value"] == 1
    assert o["candidate_count"]["value"] == 2  # Not the last historical finding.
    assert o["input_tokens"]["value"] == 10
    assert o["challenge_calls"]["value"] == o["challenge_attempt_count"]["value"] == 1
    assert o["strict_qwen"]["value"] is None  # Never transfer Qwen-only acceptance.
    assert o["published_root_cause"]["availability"] == "available"
    assert o["published_root_cause"]["value"] is None
    assert o["root_cause_hit"]["value"] is None
    assert o["estimated_cost"]["value"] is None
    assert not row["ground_truth_loaded"]
    assert not any(v["captured"] for v in row["funnel"].values())


@pytest.mark.parametrize("reported,complete", [(False, True), (True, False), (False, False)])
def test_missing_usage_or_capture_does_not_become_zero(tmp_path, reported, complete):
    fixture(tmp_path, reported=reported, complete=complete)
    o = export(tmp_path)["observations"]
    assert o["input_tokens"]["value"] is None
    assert o["estimated_cost"]["value"] is None
    if not complete:
        assert o["logical_calls"]["value"] is None


def test_valid_transport_invalid_json_still_has_usage(tmp_path):
    fixture(tmp_path, outcome="invalid_json")
    o = export(tmp_path)["observations"]
    assert o["json_decoded_calls"]["value"] == 0
    assert o["input_tokens"]["value"] == 10
    assert o["first_pass_json"]["value"] is None


def test_failed_no_state_is_preserved_as_unknown(tmp_path):
    _, result = fixture(tmp_path)
    result.update(state_file=None, job_status="failed", provider_failure=True)
    (tmp_path / "run_results.json").write_text(json.dumps({"results": [result]}))
    o = export(tmp_path)["observations"]
    assert o["job_status"]["value"] == "failed"
    assert o["provider_failure"]["value"] is True
    assert o["logical_calls"]["value"] is None
    assert o["evidence_count"]["value"] is None


def test_absent_selected_result_not_silently_dropped(tmp_path):
    fixture(tmp_path)
    (tmp_path / "run_results.json").write_text('{"results": []}')
    assert export(tmp_path)["observations"]["job_status"]["value"] is None


def test_invalid_identity_fails_closed(tmp_path):
    state, _ = fixture(tmp_path)
    state["execution_metadata"]["model_identity"]["model"] = "qwen-plus"
    (tmp_path / "state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="mismatch"):
        export(tmp_path)


def test_path_traversal_rejected(tmp_path):
    _, result = fixture(tmp_path)
    result["state_file"] = "../ground_truth.json"
    (tmp_path / "run_results.json").write_text(json.dumps({"results": [result]}))
    with pytest.raises(ValueError, match="escapes"):
        export(tmp_path)


def test_immutable_export_and_csv_null_coverage(tmp_path):
    fixture(tmp_path)
    row = export(tmp_path)
    out = tmp_path / "export"
    write_export(row, out)
    original = (out / "record.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_export(row, out)
    assert (out / "record.json").read_bytes() == original
    assert "estimated_cost.availability" in (out / "record.csv").read_text()


def test_plan_balanced_interleaved_reproducible():
    kwargs = dict(
        plan_id="pilot",
        seed="frozen",
        source_commit="a" * 40,
        prompt_hash="b" * 64,
        config={"actions": 8, "tools": 20, "logical_calls": 20},
    )
    a = make_plan(["C", "B", "A"], **kwargs)
    assert a == make_plan(["A", "B", "C"], **kwargs)
    assert len({r["run_id"] for r in a["runs"]}) == 6
    assert a["runs"][0]["case_id"] == a["runs"][1]["case_id"]
    assert a["runs"][0]["provider"] != a["runs"][2]["provider"]
    assert a["runner_auto_reruns"] == 0 and not a["paid_execution_authorized"]
    with pytest.raises(ValueError):
        make_plan(["A", "A"], **kwargs)


def test_distribution_empty_and_pilot_p95():
    assert distribution([])["mean"] is None
    assert distribution([1, 2, 9])["median"] == 2
    assert distribution([1, 2, 9])["p95"] is None
    assert distribution(list(range(20)))["p95"] == pytest.approx(18.05)
    with pytest.raises(ValueError):
        distribution([float("nan")])


def test_failed_http_attempt_has_incomplete_billable_usage(tmp_path):
    state, _ = fixture(tmp_path)
    events = state["execution_metadata"]["model_telemetry"]["events"]
    failed = dict(events[1], outcome="http_error", status_code=503, http_attempt=1)
    events[1]["http_attempt"] = 2
    events.insert(1, failed)
    (tmp_path / "state.json").write_text(json.dumps(state))
    o = export(tmp_path)["observations"]
    assert o["http_attempts"]["value"] == 2
    assert o["http_retries"]["value"] == 1
    assert o["input_tokens"]["value"] is None


def test_rejected_attempt_and_repair_are_distinct(tmp_path):
    fixture(tmp_path)
    diagnostic = {
        "complete": True,
        "records": [
            {
                "attempts": [
                    {
                        "attempt": 1,
                        "is_repair": False,
                        "logical_call_id": "a",
                        "outcome": "validation_rejected",
                        "rejection_codes": ["lane_mismatch"],
                    },
                    {
                        "attempt": 2,
                        "is_repair": True,
                        "logical_call_id": "b",
                        "outcome": "accepted",
                        "rejection_codes": [],
                    },
                ]
            }
        ],
    }
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    (folder / "CASE_A.challenge.json").write_text(json.dumps(diagnostic))
    row = export(tmp_path)
    assert row["challenge_diagnostics"][0]["rejection_codes"] == ["lane_mismatch"]
    assert row["challenge_diagnostics"][1]["is_repair"] is True
    assert row["diagnostic_coverage"]["complete"] is True
    assert len(row["raw_hashes"]) == 4


def test_orphan_logical_call_keeps_counts_but_not_usage(tmp_path):
    state, _ = fixture(tmp_path)
    state["execution_metadata"]["model_telemetry"]["events"].pop()
    (tmp_path / "state.json").write_text(json.dumps(state))
    o = export(tmp_path)["observations"]
    assert o["logical_calls"]["value"] == 1
    assert o["input_tokens"]["value"] is None
    assert o["json_decoded_calls"]["value"] is None
