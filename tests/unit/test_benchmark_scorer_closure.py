"""Regression contracts for the six accepted scorer-closure blockers; no model calls."""

import copy

from benchmark_record import export_sidecar
from benchmark_scoring import match_candidate, score_run
from test_benchmark_freeze import base_record, candidate, case, root


def reference():
    r = root()
    r["recipe"].update(allowed_versions=["1"], allowed_pairs=[{"recipe": "R1", "version": "1"}])
    r["proof_granularity"] = (
        "lane_parameter_equipment_or_recipe_scope_only; "
        "do not infer an uninstrumented component or sub-step"
    )
    return r


def proposal():
    return {**candidate(), "recipe_version": "1", "hypothesis_id": "C1"}


def publication(c):
    return score_run(
        {**case(), "roots": [reference()]},
        job_status="completed",
        authority={
            "root_cause": "thermal instability",
            "confirmation_status": "supported",
            "root_cause_candidate_id": "C1",
        },
        snapshots=[
            {
                "stage": "ranking_survival",
                "operation_id": "op_1",
                "attempt_index": 0,
                "candidates": [c],
            }
        ],
    )


def test_wrong_version_cannot_publish_hit():
    c = proposal()
    c["recipe_version"] = "wrong"
    assert match_candidate(c, reference())["mechanism_match"] is True
    assert publication(c)["root_cause_hit"] is False


def test_disjoint_time_preserves_mechanism_but_not_final_hit():
    c = proposal()
    c["time_window"] = {"start": "2099-01-01T00:00:00+00:00", "end": "2099-01-02T00:00:00+00:00"}
    assert match_candidate(c, reference())["mechanism_match"] is True
    assert publication(c)["root_cause_hit"] is False


def test_equipment_gt_does_not_infer_chamber():
    r = reference()
    r["entity"]["chamber"] = None
    r["scope"]["kind"] = "equipment"
    c = proposal()
    c.pop("chamber")
    c["scope_kind"] = "equipment"
    d = match_candidate(c, r)
    assert d["chamber_match"] is None
    assert d["dimension_applicability"]["chamber_match"] == "not_applicable"
    assert d["fully_grounded_hit"] is True


def test_unobserved_component_or_substep_cannot_get_full_hit():
    for field in ("component", "sub_step"):
        c = proposal()
        c[field] = "unobserved internal failure"
        assert match_candidate(c, reference())["fully_grounded_hit"] is False
    c = proposal()
    c["causal_explanation"] = "An unobserved valve spring broke inside the chamber."
    assert match_candidate(c, reference())["fully_grounded_hit"] is not True
    assert publication(c)["root_cause_hit"] is not True


def test_initial_miss_and_repair_hit_are_distinct():
    c = proposal()
    snaps = [
        {
            "stage": "initial_raw_candidate",
            "operation_id": "op_1",
            "attempt_index": 0,
            "candidates": [],
        },
        {
            "stage": "initial_raw_candidate",
            "operation_id": "op_1",
            "attempt_index": 1,
            "candidates": [c],
        },
        {
            "stage": "admitted_candidate",
            "operation_id": "op_1",
            "attempt_index": 1,
            "candidates": [c],
        },
    ]
    s = score_run(
        {**case(), "roots": [reference()]},
        job_status="completed",
        authority={"root_cause": None},
        snapshots=snaps,
    )
    assert s["candidate_recall"] is False
    assert s["candidate_funnel"][0]["attempts"][1]["admitted_candidate"]["hit"] is True
    assert s["candidate_funnel"][0]["attempts"][0]["initial_raw_candidate"]["hit"] is False
    only_repair = score_run(
        {**case(), "roots": [reference()]},
        job_status="completed",
        authority={"root_cause": None},
        snapshots=snaps[1:],
    )
    assert only_repair["candidate_recall"] is None


def test_canonical_fallback_exports_observed_positive():
    cap = {
        "complete": True,
        "snapshots": [],
        "diagnostics": [],
        "operations": [
            {
                "operation_id": "op_1",
                "stage": "specialist_analysis",
                "outcome": "fallback",
                "attempts": [],
            }
        ],
    }
    r = export_sidecar(base_record(), cap, {"records": []})
    assert r.metrics["fallback"].value is True
    assert r.metrics["fallback_count"].value == 1
    assert r.metrics["fallback"].availability == "available"
    cap = copy.deepcopy(cap)
    cap["complete"] = False
    # An observed positive remains known even if other events are missing.
    assert export_sidecar(base_record(), cap, {"records": []}).metrics["fallback"].value is True


def test_recipe_version_pairs_are_not_cross_product():
    r = reference()
    r["recipe"]["allowed_ids"] = ["R1", "R2"]
    r["recipe"]["allowed_versions"] = ["1", "2"]
    r["recipe"]["allowed_pairs"] = [
        {"recipe": "R1", "version": "1"},
        {"recipe": "R2", "version": "2"},
    ]
    c = proposal()
    c["recipe_version"] = "2"
    assert match_candidate(c, r)["recipe_version_match"] is False
    assert match_candidate(c, r)["fully_grounded_hit"] is False


def test_missing_dimensions_remain_reviewable_not_invented():
    for key in ("recipe_version", "time_window"):
        c = proposal()
        c.pop(key)
        d = match_candidate(c, reference())
        assert d["fully_grounded_hit"] is None and d["adjudication_required"]
    c = proposal()
    c["causal_explanation"] = "A novel but possibly equivalent explanation"
    assert publication(c)["root_cause_hit"] is None


def test_later_operation_initial_hit_does_not_rewrite_first_operation():
    c = proposal()
    snaps = [
        {
            "stage": "initial_raw_candidate",
            "operation_id": "op_1",
            "attempt_index": 1,
            "candidates": [c],
        },
        {
            "stage": "initial_raw_candidate",
            "operation_id": "op_2",
            "attempt_index": 0,
            "candidates": [c],
        },
        {
            "stage": "admitted_candidate",
            "operation_id": "op_2",
            "attempt_index": 0,
            "candidates": [c],
        },
        {"stage": "ranking_survival", "operation_id": "op_1", "attempt_index": 1, "candidates": []},
    ]
    scored = score_run(
        {**case(), "roots": [reference()]},
        job_status="completed",
        authority={"root_cause": None},
        snapshots=snaps,
    )
    assert scored["candidate_recall"] is None
    groups = {g["operation_id"]: g["attempts"] for g in scored["candidate_funnel"]}
    assert groups["op_1"][0]["ranking_survival"]["hit"] is False
    assert groups["op_2"][0]["admitted_candidate"]["hit"] is True
    assert "admitted_candidate" not in groups["op_1"][0]


def test_fallback_negative_requires_complete_final_ledger():
    cap = {"complete": True, "snapshots": [], "diagnostics": [], "operations": []}
    assert export_sidecar(base_record(), cap, {"records": []}).metrics["fallback"].value is None
    cap["snapshots"] = [{"stage": "finalizer_output", "fallback_ledger": []}]
    assert export_sidecar(base_record(), cap, {"records": []}).metrics["fallback"].value is False
    cap["snapshots"][0]["fallback_ledger"] = [
        {"source": "orchestration", "reason": "planner_invalid"}
    ]
    assert export_sidecar(base_record(), cap, {"records": []}).metrics["fallback"].value is True


def test_fallback_existing_metadata_markers():
    from benchmark_observer import fallback_facts

    state = {
        "execution_metadata": {"orchestration_fallback_reason": "intent_invalid"},
        "findings": [
            {
                "finding_id": "F1",
                "details": {"specialist_v2": {"fallback_reason": "analysis_output_invalid"}},
            }
        ],
    }
    assert len(fallback_facts(state)) == 2
    assert fallback_facts({"execution_metadata": {}, "findings": []}) == []


def test_undecoded_initial_output_is_observed_before_repair():
    from types import SimpleNamespace

    from benchmark_observer import capture_benchmark
    from yield_rca_core.model_contracts import LLMOutputValidationError

    # Exercise the actual observer return/exception boundary with a tiny synthetic
    # frame bearing the frozen Candidate operation name, no provider calls.
    namespace = {
        "__name__": "yield_rca_core.hypothesis_candidate_generator",
        "LLMOutputValidationError": LLMOutputValidationError,
        "SimpleNamespace": SimpleNamespace,
    }
    exec(
        """
def complete_json(request):
    if request.payload['output_attempt'] == 1:
        raise LLMOutputValidationError('invalid JSON')
    return SimpleNamespace(data={'candidates': []}, usage=SimpleNamespace(call_id='repair_call'))
def generate():
    validation_errors=[]
    for attempt in (1,2):
        request=SimpleNamespace(payload={'output_attempt':attempt},prompt_name='candidate')
        try:
            complete_json(request)
        except LLMOutputValidationError:
            validation_errors.append('invalid JSON')
    return SimpleNamespace(candidates=[],candidate_semantic_profiles=[])
""",
        namespace,
    )
    with capture_benchmark() as cap:
        namespace["generate"]()
    rows = [s for s in cap.snapshots if s["stage"] == "initial_raw_candidate"]
    assert cap.to_dict()["complete"]
    assert [s["attempt_index"] for s in rows] == [0, 1]
    assert rows[0]["json_outcome"] == "no_decoded_response" and rows[0]["candidates"] == []


def test_reference_preserves_source_granularity_for_all_cases(tmp_path):
    import csv

    from benchmark_build_reference import build

    rows = []
    sources = []
    public = tmp_path / "public"
    (public / "fab_data").mkdir(parents=True)
    for number in range(1, 25):
        supported = number <= 14
        lane = {
            "equipment_id": "E1",
            "chamber_id": None,
            "operation": "100",
            "scope_kind": "equipment",
            "parameter_names": ["p"],
        }
        rows.append(
            {
                "packet": "formal_v1_candidate_r2",
                "case_id": f"FORMAL_{number:03d}",
                "expected_status": "supported"
                if supported
                else "inconclusive"
                if number <= 20
                else "insufficient_evidence",
                "expected_lanes": [lane] if supported else [],
                "source_lot_id": "L1",
                "expected_impact_lots": [],
                "family_id": "synthetic",
                "expected_root_cause": "synthetic mechanism",
                "scope_rule": "equipment only",
                "scenario_category": "synthetic",
                "review_status": "synthetic",
            }
        )
        sources.append(
            {
                "case_id": rows[-1]["case_id"],
                "expected_causal_lanes": rows[-1]["expected_lanes"],
                "proof_granularity": "do not infer an uninstrumented component or sub-step",
            }
        )
    with (public / "fab_data/process_history.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "lot_id",
                "operation_no",
                "equipment_id",
                "chamber_id",
                "recipe_id",
                "recipe_version",
                "started_at",
                "ended_at",
            ],
        )
        writer.writeheader()
        writer.writerow(
            dict(
                zip(
                    writer.fieldnames,
                    ["L1", "100", "E1", "C_UNPROVEN", "R1", "1", "2028-01-01", "2028-01-02"],
                    strict=True,
                )
            )
        )
    ref = build({"cases": rows}, public, {"cases": sources})
    assert len(ref["cases"]) == 24
    for c in ref["cases"]:
        assert c["proof_granularity"] == sources[0]["proof_granularity"]
        for r in c["roots"]:
            assert r["scope"]["kind"] == "equipment" and r["entity"]["chamber"] is None
            assert r["proof_granularity"] == c["proof_granularity"]


def test_recipe_root_identity_includes_version_but_mechanism_stays_separate():
    r = reference()
    r["recipe"]["requirement"] = "required_identity"
    c = proposal()
    c["recipe_version"] = "wrong"
    d = match_candidate(c, r)
    assert d["mechanism_match"] is True
    assert d["identity_hit"] is False and d["fully_grounded_hit"] is False
