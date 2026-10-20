# ruff: noqa: E402
"""Offline phase 0.5 scoring, provenance, isolation and billing contracts."""

import copy
import json
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests/contract")]
from benchmark_execution import observe_once
from benchmark_observer import (
    Capture,
    capture_benchmark,
    clean,
    repair_metrics,
    runtime_read_guard,
    sha,
)
from benchmark_record import (
    BenchmarkRecord,
    cost_for_call,
    cost_ratios,
    export_sidecar,
    write_record,
)
from benchmark_scoring import load_reference, match_candidate, quality_rates, score_run
from test_qwen_intent_planner_contracts import (
    AlwaysInvalidIntentClient,
    InvalidThenValidIntentClient,
)
from yield_rca_core import QwenIntentPlanner, QwenIntentPlannerError


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("No live model in Phase 0.5")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def root():
    return {
        "mechanism_id": "thermal_drift",
        "equivalent_labels": ["chamber temperature drift", "thermal instability"],
        "entity": {"equipment": "E1", "chamber": "C1"},
        "operation": "100",
        "recipe": {
            "allowed_ids": ["R1"],
            "allowed_versions": ["1"],
            "allowed_pairs": [{"recipe": "R1", "version": "1"}],
            "requirement": "scope_component_not_mechanism_identity",
        },
        "proof_granularity": "do not infer an uninstrumented component or sub-step",
        "scope": {"kind": "chamber"},
        "time_window": {"start": "2028-01-01T00:00:00+00:00", "end": "2028-01-02T00:00:00+00:00"},
    }


def candidate():
    return {
        "mechanism_id": "thermal instability",
        "equipment": "E1",
        "chamber": "C1",
        "operation": "100",
        "recipe": "R1",
        "recipe_version": "1",
        "scope_kind": "chamber",
        "time_window": {"start": "2028-01-01T00:00:00+00:00", "end": "2028-01-01T23:00:00+00:00"},
    }


def case(status="supported"):
    return {
        "case_id": "SYNTHETIC",
        "quality_primary_eligible": True,
        "expected_outcome": status,
        "roots": [root()] if status == "supported" else [],
    }


@pytest.mark.parametrize(
    "label", ["thermal_drift", "chamber temperature drift", "thermal instability"]
)
def test_equivalence_is_dimensioned(label):
    c = candidate()
    c["mechanism_id"] = label
    assert match_candidate(c, root())["fully_grounded_hit"] is True
    c["scope_kind"] = "recipe"
    score = match_candidate(c, root())
    assert score["identity_hit"] is True and score["scope_match"] is False


@pytest.mark.parametrize("field", ["equipment", "chamber", "operation"])
def test_wrong_entity_cannot_be_hidden_by_mechanism(field):
    c = candidate()
    c[field] = "wrong"
    score = match_candidate(c, root())
    assert score["mechanism_match"] is True and score["identity_hit"] is False


def test_unknown_negated_and_recipe_time():
    c = candidate()
    c["mechanism_id"] = "not thermal instability"
    assert match_candidate(c, root())["identity_hit"] is None
    c["mechanism_id"] = "brand new explanation"
    assert match_candidate(c, root())["adjudication_required"]
    r = root()
    r["recipe"]["requirement"] = "required_identity"
    c = candidate()
    c["recipe"] = "R2"
    assert match_candidate(c, r)["identity_hit"] is False
    c = candidate()
    c["time_window"] = {"start": "2027-01-01T00:00:00+00:00", "end": "2027-01-02T00:00:00+00:00"}
    assert match_candidate(c, root())["time_match"] is False


@pytest.mark.parametrize("status", ["inconclusive", "insufficient_evidence"])
def test_terminal_categories_separate_and_provider_failure_not_correct(status):
    authority = {"root_cause": None, "confirmation_status": status}
    scored = score_run(case(status), job_status="completed", authority=authority, snapshots=[])
    assert scored["correct_" + status] is True and scored["root_cause_hit"] is None
    failed = score_run(case(status), job_status="failed", authority=authority, snapshots=[])
    assert failed["correct_" + status] is False
    wrong = score_run(
        case(status),
        job_status="completed",
        authority={**authority, "root_cause": "asserted"},
        snapshots=[],
    )
    assert wrong["correct_" + status] is False


def test_initial_recall_not_reconstructed_from_late_ranking():
    snaps = [{"stage": "ranking_survival", "candidates": [candidate()]}]
    scored = score_run(
        case(), job_status="completed", authority={"root_cause": None}, snapshots=snaps
    )
    assert scored["candidate_recall"] is None
    assert scored["funnel"]["ranking_survival"]["hit"] is True
    assert scored["root_cause_hit"] is False


def test_calibration_excluded_and_unknown_not_zero():
    a = score_run(case(), job_status="completed", authority={"root_cause": None}, snapshots=[])
    calibration = {**a, "primary_eligible": False, "root_cause_hit": True}
    rates = quality_rates([a, calibration])
    assert rates["root_cause_hit"]["denominator"] == 1
    assert rates["candidate_recall"]["value"] is None


@pytest.mark.parametrize("method", ["pathlib", "builtin", "osopen"])
def test_runtime_cannot_read_scoring_gt(tmp_path, method):
    folder = tmp_path / "benchmark_scoring_v1"
    folder.mkdir()
    secret = folder / "rubric.json"
    secret.write_text('{"hidden":"truth"}')
    with runtime_read_guard(), pytest.raises(PermissionError):
        if method == "pathlib":
            secret.read_text()
        elif method == "builtin":
            open(secret).read()
        else:
            os.close(os.open(secret, os.O_RDONLY))
    assert "truth" in secret.read_text()  # evaluator outside runtime boundary still works


def test_runtime_sidecar_no_retry_and_failure_persistence(tmp_path):
    calls = []

    def execute():
        calls.append(1)
        raise RuntimeError("simulated failure")

    with pytest.raises(RuntimeError):
        observe_once(execute, tmp_path / "run")
    saved = json.loads((tmp_path / "run/sidecar.json").read_text())
    assert calls == [1] and saved["failure_type"] == "RuntimeError"
    with pytest.raises(FileExistsError):
        observe_once(execute, tmp_path / "run")
    assert calls == [1]


def test_runtime_envelope_automatically_guards_gt(tmp_path):
    folder = tmp_path / "benchmark_scoring_v1"
    folder.mkdir()
    f = folder / "rubric.json"
    f.write_text("truth")
    with pytest.raises(PermissionError):
        observe_once(lambda: f.read_text(), tmp_path / "run")


@pytest.mark.parametrize("accepts", [True, False])
def test_real_intent_repair_lineage(accepts):
    client = InvalidThenValidIntentClient() if accepts else AlwaysInvalidIntentClient()
    with capture_benchmark() as cap:
        if accepts:
            QwenIntentPlanner(client).plan("Investigate Cu CMP scratch", lot_id="LOT_A_001")
        else:
            with pytest.raises(QwenIntentPlannerError):
                QwenIntentPlanner(client).plan("Investigate Cu CMP scratch", lot_id="LOT_A_001")
    c = cap.to_dict()
    assert c["complete"]
    ops = [o for o in c["operations"] if o["stage"] == "intent"]
    assert len(ops) == 1 and len(ops[0]["attempts"]) == 2
    metrics = repair_metrics(c)
    assert metrics["first_pass_success_rate"]["value"] == 0
    assert metrics["repair_success_rate"]["value"] == int(accepts)
    assert metrics["repeated_same_error_rate"]["value"] == int(not accepts)


def prices():
    return json.loads((ROOT / "docs/model_benchmark/phase05/pricing_snapshot.json").read_text())[
        "models"
    ]


@pytest.mark.parametrize("provider_index", [0, 1])
def test_cost_exact_requires_cache_and_tariff(provider_index):
    price = prices()[provider_index]
    usage = {"prompt_tokens": 10000, "completion_tokens": 1000, "cached_tokens": 2000}
    a = cost_for_call(usage, price, cache_reported=True, tariff="peak")
    assert a["availability"] == "available" and a["value"] > 0
    b = cost_for_call(usage, price)
    assert b["value"] is None and b["lower"] <= a["value"] <= b["upper"]
    c = cost_for_call(usage, price, billable_complete=False)
    assert c["value"] is None and c["availability"] == "missing"


def test_cost_correct_excludes_conservative_and_zero_denominator():
    records = [
        {
            "cost": {"currency": "CNY", "value": 2},
            "job_status": "completed",
            "contract_status": "ACCEPT",
            "expected_outcome": "supported",
            "quality_eligible": True,
            "correct_root": True,
        },
        {
            "cost": {"currency": "CNY", "value": 3},
            "job_status": "failed",
            "contract_status": "REJECT",
            "expected_outcome": "supported",
            "quality_eligible": True,
            "correct_root": False,
        },
        {
            "cost": {"currency": "CNY", "value": 100},
            "job_status": "completed",
            "contract_status": "ACCEPT",
            "expected_outcome": "inconclusive",
            "quality_eligible": True,
            "correct_root": None,
        },
    ]
    assert cost_ratios(records)["cost_per_correct_case"] == 5
    records[0]["correct_root"] = False
    assert cost_ratios(records)["cost_per_correct_case"] is None


def test_capture_redaction_and_size_limit():
    cleaned = clean({"api_key": "danger", "x": "Bearer dangerous-secret-token"})
    assert "danger" not in json.dumps(cleaned)
    cap = Capture(max_bytes=5)
    cap.append(cap.snapshots, {"x": "too long"})
    assert cap.to_dict()["complete"] is False and cap.snapshots == []


def base_record():
    obs = {"value": None, "availability": "missing", "source": "offline", "reason": "not observed"}
    return {
        "benchmark_version": "development-rca/1.1",
        "scorer_version": "formal-r2-scorer/1.1",
        "source_commit": "a" * 40,
        "prompt_hash": "b" * 64,
        "workflow_hash": "c" * 64,
        "config_hash": "d" * 64,
        "rubric_hash": "e" * 64,
        "case_id": "SYNTHETIC",
        "case_revision": "r2",
        "exposure_class": "normal_development",
        "ground_truth_class": "partial_source_with_frozen_rubric",
        "provider": "dashscope",
        "model": "qwen-plus",
        "actual_provider": "dashscope",
        "actual_model": "qwen-plus",
        "run_id": "offline_1",
        "execution_order": 1,
        "timestamp": "2026-10-08T00:00:00Z",
        "execution_kind": "offline_fixture",
        "pricing_snapshot_id": "test-v1",
        "raw_hashes": {},
        "job_status": obs,
        "contract_status": obs,
        "quality": obs,
        "metrics": {},
    }


def test_schema_taxonomy_coverage_and_immutable_export(tmp_path):
    cap = {"operations": [], "snapshots": [], "diagnostics": [], "complete": True}
    challenge = {
        "records": [
            {"generation": 1, "attempts": [{"attempt": 1, "rejection_codes": ["lane_mismatch"]}]}
        ]
    }
    row = export_sidecar(base_record(), cap, challenge)
    assert row.metrics["lane_mismatch"].value == 1
    assert row.metrics["initial_raw_candidate"].availability == "missing"
    write_record(row, tmp_path / "out")
    with pytest.raises(FileExistsError):
        write_record(row, tmp_path / "out")
    data = row.model_dump()
    data["model"] = "deepseek-v4-pro"
    with pytest.raises(ValueError):
        BenchmarkRecord.model_validate(data)
    data = row.model_dump()
    data["secret"] = "forbidden"
    with pytest.raises(ValueError):
        BenchmarkRecord.model_validate(data)


def test_reference_hash_mutation_rejected(tmp_path):
    f = tmp_path / "reference.json"
    doc = {"version": "v1"}
    f.write_text(json.dumps(doc))
    assert load_reference(f, sha(doc)) == doc
    f.write_text('{"version":"changed"}')
    with pytest.raises(ValueError):
        load_reference(f, sha(doc))


def test_actual_next_action_caller_retry_is_not_repair():
    from test_qwen_next_action_planner_contracts import QwenNextActionPlannerContractTest

    test = QwenNextActionPlannerContractTest(
        "test_two_call_failures_exhaust_retry_with_sanitized_diagnostics"
    )
    test.setUp()
    with capture_benchmark() as cap:
        test.test_two_call_failures_exhaust_retry_with_sanitized_diagnostics()
    metrics = repair_metrics(cap.to_dict())
    assert metrics["caller_retries"] == 1
    assert metrics["output_repairs"] == 0
    assert metrics["repair_rate"]["value"] == 0


def test_comparator_has_no_invented_repair():
    from test_result_identity_contracts import test_comparator_identity_keeps_original_validation

    with capture_benchmark() as cap:
        test_comparator_identity_keeps_original_validation("dashscope", "qwen")
    ops = [o for o in cap.to_dict()["operations"] if o["stage"] == "comparator"]
    assert ops and all(len(o["attempts"]) == 1 for o in ops)
    assert repair_metrics(cap.to_dict())["output_repairs"] == 0


def test_specialist_repair_and_fallback_lineage():
    from test_specialist_v2 import (
        test_analysis_evidence_must_be_exact_closure_or_python_analysis_is_used,
        test_cross_domain_candidate_is_rejected_then_valid_same_domain_retry_runs,
    )

    with capture_benchmark() as cap:
        test_cross_domain_candidate_is_rejected_then_valid_same_domain_retry_runs()
        test_analysis_evidence_must_be_exact_closure_or_python_analysis_is_used()
    c = cap.to_dict()
    assert c["complete"]
    ops = c["operations"]
    assert any(
        o["stage"] == "specialist_tool" and len(o["attempts"]) == 2 and o["outcome"] == "accepted"
        for o in ops
    )
    assert any(
        o["stage"] == "specialist_analysis"
        and len(o["attempts"]) == 2
        and o["outcome"] == "fallback"
        for o in ops
    )


def test_full_export_joins_sidecar_without_gt_entering_runtime(tmp_path):
    from benchmark_record import export_run
    from test_model_benchmark_phase0 import fixture

    state, _ = fixture(tmp_path)
    refcase = {
        **case("inconclusive"),
        "case_id": "CASE_A",
        "case_revision": "r2",
        "exposure_class": "normal_development",
        "ground_truth_class": "partial_source_with_frozen_rubric",
    }
    reference = {"scorer_version": "formal-r2-scorer/1.1", "cases": [refcase]}
    f = tmp_path / "reference.json"
    f.write_text(json.dumps(reference))
    pricing = json.loads((ROOT / "docs/model_benchmark/phase05/pricing_snapshot.json").read_text())
    manifest = {
        "benchmark_version": "development-rca/1.1",
        "source_commit": "a" * 40,
        "prompt_hash": "b" * 64,
        "workflow_hash": "c" * 64,
        "config_hash": "d" * 64,
        "rubric_hash": sha(reference),
        "pricing_hash": sha(pricing),
        "runs": [
            {
                "case_id": "CASE_A",
                "run_id": "joined",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "order_index": 1,
            }
        ],
    }
    capture = {
        "complete": True,
        "operations": [],
        "diagnostics": [],
        "snapshots": [
            {
                "stage": "finalizer_output",
                "authoritative_rca_result": {
                    "root_cause": None,
                    "confirmation_status": "inconclusive",
                },
            }
        ],
    }
    sidecar = {
        "capture": capture,
        "challenge": {"records": []},
        "workflow_latency_ms": 30,
        "model_telemetry": state["execution_metadata"]["model_telemetry"],
    }
    side = tmp_path / "sidecar.json"
    side.write_text(json.dumps(sidecar))
    row = export_run(tmp_path, side, manifest, run_id="joined", reference_file=f, pricing=pricing)
    assert row.quality.value["correct_inconclusive"] is True
    assert row.metrics["logical_calls"].value == 1
    assert row.metrics["cost"].availability == "partial"
    with pytest.raises(ValueError, match="execution-time"):
        export_run(
            tmp_path,
            side,
            manifest,
            run_id="joined",
            reference_file=f,
            pricing=pricing,
            execution_kind="paid_benchmark",
        )
    changed = copy.deepcopy(pricing)
    changed["version"] = "new rate"
    with pytest.raises(ValueError, match="Pricing"):
        export_run(tmp_path, side, manifest, run_id="joined", reference_file=f, pricing=changed)


def test_frozen_inputs_refuse_drift(tmp_path):
    import hashlib

    from benchmark_execution import verify_freeze

    repo = tmp_path / "repo"
    public = tmp_path / "public"
    repo.mkdir()
    public.mkdir()
    (repo / "module.py").write_bytes(b"x=1\n")
    (public / "data.csv").write_bytes(b"a,b\n")
    manifest = {
        "source_hashes": {"module.py": hashlib.sha256(b"x=1\n").hexdigest()},
        "public_hashes": {"data.csv": hashlib.sha256(b"a,b\n").hexdigest()},
    }
    assert verify_freeze(manifest, repo, public)["verified"]
    (public / "data.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="drift"):
        verify_freeze(manifest, repo, public)


def test_gt_guard_covers_child_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from benchmark_observer import runtime_read_guard

    secret = tmp_path / "benchmark_scoring_v1" / "rubric.json"
    secret.parent.mkdir()
    secret.write_text("scoring only")
    with runtime_read_guard(), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(PermissionError):
            pool.submit(secret.read_text).result()
    assert secret.read_text() == "scoring only"


def test_observer_raw_profiles_follow_explicit_index():
    from benchmark_observer import candidates

    profiles = [
        {"candidate_index": 1, "primary_mechanism": "B"},
        {"candidate_index": 0, "primary_mechanism": "A"},
    ]
    observed = candidates([{"root_cause": "A"}, {"root_cause": "B"}], profiles, raw=True)
    assert [c["semantic_profile"]["primary_mechanism"] for c in observed] == ["A", "B"]
    duplicate = candidates([{"root_cause": "A"}], [profiles[1], profiles[1]], raw=True)
    assert "semantic_profile" not in duplicate[0]


def test_raw_scope_and_publication_candidate_id():
    c = {
        "hypothesis_id": "candidate1",
        "root_cause": "thermal instability",
        "semantic_profile": {
            "primary_mechanism": "thermal instability",
            "claimed_scope": {
                "operation": "100",
                "equipment": "E1",
                "chamber": "C1",
                "recipe": "R1",
                "scope_kind": "chamber",
            },
        },
    }
    assert match_candidate(c, root())["identity_hit"] is True
    c.update(recipe_version="1", time_window=candidate()["time_window"])
    scored = score_run(
        case(),
        job_status="completed",
        authority={
            "root_cause": "publication prose",
            "confirmation_status": "supported",
            "source_hypothesis_id": "finding1",
            "root_cause_candidate_id": "candidate1",
        },
        snapshots=[{"stage": "ranking_survival", "candidates": [c]}],
    )
    assert scored["root_cause_hit"] is True


def test_capture_bounds_invalidate_coverage():
    from benchmark_observer import Capture

    cap = Capture(max_bytes=100)
    cap.operations = [{"large": "x" * 200}]
    assert not cap.to_dict()["complete"]
    assert cap.to_dict()["operations"] == []
    cap = Capture()
    cap.append(cap.snapshots, {"text": "x" * 6001})
    assert not cap.to_dict()["complete"]


def test_sidecar_io_failure_preserves_business_exception(tmp_path, monkeypatch):
    from benchmark_execution import observe_once

    original = Path.write_text

    def broken(path, *args, **kwargs):
        if path.name == "sidecar.json":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", broken)

    def fail():
        raise ValueError("business error")

    with pytest.raises(ValueError, match="business error"):
        observe_once(fail, tmp_path / "failed")
    with pytest.raises(OSError, match="disk full"):
        observe_once(lambda: 1, tmp_path / "success")


def test_stage_lineage_and_json_are_separate_from_acceptance():
    test = InvalidThenValidIntentClient()
    with capture_benchmark() as cap:
        QwenIntentPlanner(test).plan_with_diagnostics("Investigate LOT_001 yield loss")
    metric = repair_metrics(cap.to_dict())
    assert "intent" in metric["by_stage"]
    assert metric["first_pass_json_success_rate"]["value"] == 1
    assert metric["first_pass_success_rate"]["value"] == 0


def test_confirmation_survival_is_not_candidate_presence():
    c = {**candidate(), "hypothesis_id": "C1"}
    snap = {
        "stage": "confirmation_status",
        "candidates": [c],
        "selected_candidate_id": "C1",
        "decision_gate": {"status": "inconclusive"},
    }
    scored = score_run(
        case(), job_status="completed", authority={"root_cause": None}, snapshots=[snap]
    )
    assert scored["supported_root_cause_recall"] is False
    snap["decision_gate"]["status"] = "supported"
    scored = score_run(
        case(), job_status="completed", authority={"root_cause": None}, snapshots=[snap]
    )
    assert scored["supported_root_cause_recall"] is True


def test_checked_in_freeze_consistency():
    from benchmark_freeze_manifest import PAIRS, PRIMARY

    folder = ROOT / "docs/model_benchmark/scorer_closure"
    manifest = json.loads((folder / "manifest.json").read_text())
    schema = json.loads((folder / "record.schema.json").read_text())
    readiness = json.loads((folder / "scoring_readiness.json").read_text())
    audit = json.loads((folder / "telemetry_audit.json").read_text())
    assert sha(schema) == manifest["schema_hash"] == sha(BenchmarkRecord.model_json_schema())
    assert sha(manifest["config"]) == manifest["config_hash"]
    assert manifest["primary_cases"] == PRIMARY and "FORMAL_012" not in PRIMARY
    assert len(manifest["runs"]) == 18
    for index in range(9):
        pair = manifest["runs"][index * 2 : index * 2 + 2]
        assert [(r["provider"], r["model"]) for r in pair] == (
            PAIRS if index % 2 == 0 else PAIRS[::-1]
        )
        assert len({r["case_id"] for r in pair}) == 1
        assert all(r["replicate"] == 1 and r["scheduled_status"] == "not_run" for r in pair)
    assert readiness["counts"] == {"supported": 14, "inconclusive": 6, "insufficient_evidence": 4}
    assert readiness["primary_category_counts"] == {
        "supported": 4,
        "inconclusive": 2,
        "insufficient_evidence": 2,
    }
    assert sum(audit["counts"].values()) == len(audit["metrics"]) == 63
    assert all(
        c["ground_truth_class"] == "partial_source_with_frozen_rubric" for c in readiness["cases"]
    )
    assert not manifest["paid_execution_authorized"]


def test_execution_modules_have_no_scoring_dependency():
    import ast

    for name in ("benchmark_execution.py", "benchmark_observer.py"):
        tree = ast.parse((ROOT / "tools" / name).read_text(encoding="utf-8"))
        imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(x and ("scoring" in x or "reference" in x) for x in imported)
    for package in ("core", "backend"):
        for path in (ROOT / package).rglob("*.py"):
            assert "benchmark_scoring" not in path.read_text(encoding="utf-8")
