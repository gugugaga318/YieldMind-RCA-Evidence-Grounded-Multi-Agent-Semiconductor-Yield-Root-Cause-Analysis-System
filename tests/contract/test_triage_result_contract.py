"""R1 isolation, provenance, honest uncertainty and serialization contracts."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "backend")]

from yield_rca_api.app import _state_api_payload  # noqa: E402
from yield_rca_api.schemas import (  # noqa: E402
    CreateRCAJobRequest,
    RCAJobResponse,
    RCAJobStateResponse,
    TriageResultResponse,
)
from yield_rca_core.causal_hypothesis import CausalClaimStatus  # noqa: E402
from yield_rca_core.evidence_models import Evidence, ModelValidationError  # noqa: E402
from yield_rca_core.models import AgentFinding, RCAJob, RCAState  # noqa: E402
from yield_rca_core.triage_result import TriageResult  # noqa: E402


def scope():
    return dict(
        entity=None,
        operation="OP_X",
        equipment="EQ_X",
        chamber=None,
        recipe="RECIPE_X",
        recipe_version=None,
        scope_kind="equipment",
        lane_ids=["LANE_X"],
        time_window=[],
    )


def hypothesis(n):
    return dict(
        candidate_id=f"C{n}",
        engineering_statement=f"  Model mechanism {n}: 工程表述  ",
        causal_explanation=f"Model-authored explanation {n}",
        primary_mechanism=f"mechanism {n}",
        supporting_evidence_ids=[f"E{n}"],
        contradicting_evidence_ids=["EC"],
        scope=scope(),
    )


def assessment(n):
    return dict(
        candidate_id=f"C{n}",
        parameter_support=None,
        mechanism_support=None,
        entity_consistency=None,
        operation_consistency=None,
        recipe_version_consistency=None,
        scope_consistency=None,
        temporal_consistency=None,
        contradiction=None,
        causal_chain=None,
        alternative_status=None,
    )


def payload(n=2):
    ids = [f"C{i}" for i in range(1, n + 1)]
    return dict(
        result_id="TRIAGE_TEST",
        source_finding_id="F_TRIAGE",
        primary=hypothesis(1) if n else None,
        strongest_alternative=hypothesis(2) if n == 2 else None,
        comparison=dict(
            preference_status="tied" if n == 2 else "preference_not_established",
            ordering_basis="stable_presentation_order" if n == 2 else "single" if n else "none",
            primary_ordering_reason="Stable candidate ID ordering, not a causal winner.",
            alternative_retention_reason="Independent Evidence remains." if n == 2 else None,
            alternative_disposition="retained" if n == 2 else "alternative_not_investigated",
            discriminating_evidence_ids=[],
            preferred_candidate_id=None,
        ),
        evidence_assessment=[assessment(i) for i in range(1, n + 1)],
        remaining_uncertainty=[
            dict(
                kind="not_acquired",
                description="Dose data not acquired.",
                candidate_ids=ids,
                evidence_ids=[],
                scope=scope(),
            )
        ],
        next_investigation_action=dict(
            action_id="NEXT_1",
            purpose="discriminate_hypotheses" if n == 2 else "acquire_missing_evidence",
            target_candidate_ids=ids,
            requested_observation="Acquire independent dose metrology.",
            scope=scope(),
            expected_discriminating_outcomes=[
                dict(
                    candidate_id=i,
                    expected_observation=f"Observation favoring {i}",
                    interpretation=f"Would support {i}; does not confirm it.",
                )
                for i in ids
            ],
            inconclusive_outcome="Unchanged measurements do not distinguish hypotheses.",
            executor="external_process",
            approval_required=True,
            executability="external_only",
            availability="unknown",
            availability_reason="Engineer must arrange metrology.",
        ),
        no_next_action_reason=None,
        terminal_reason="hypotheses_tied"
        if n == 2
        else "single_candidate_unconfirmed"
        if n
        else "insufficient_candidate_evidence",
    )


def source_finding(data):
    candidates, profiles = [], []
    for h in (data["primary"], data["strongest_alternative"]):
        if h is None:
            continue
        s = h["scope"]
        candidates.append(
            dict(
                candidate_id=h["candidate_id"],
                root_cause=h["engineering_statement"],
                causal_explanation=h["causal_explanation"],
                basis="llm_evidence_composition",
                supporting_evidence_ids=h["supporting_evidence_ids"],
                contradicting_evidence_ids=h["contradicting_evidence_ids"],
            )
        )
        profiles.append(
            dict(
                candidate_id=h["candidate_id"],
                primary_mechanism=h["primary_mechanism"],
                mechanism_relation="reference"
                if h == data["primary"]
                else "independent_alternative",
                depends_on_candidate_id=None,
                claimed_operation=s["operation"],
                claimed_equipment=s["equipment"],
                claimed_chamber=s["chamber"],
                claimed_recipe=s["recipe"],
                claimed_scope_kind=s["scope_kind"],
                claimed_lane_ids=s["lane_ids"],
                entity=s["entity"],
                recipe_version=s["recipe_version"],
                time_window=s["time_window"],
            )
        )
    return AgentFinding(
        finding_id="F_TRIAGE",
        agent="rca_reasoning",
        summary="Synthetic admission.",
        confidence=0.5,
        evidence_ids=["E1", "E2", "EC"],
        details=dict(
            ranked_candidates=candidates,
            candidate_semantic_profiles=profiles,
            hypothesis_candidate_generation={"candidate_output_invalid": False},
        ),
    )


def state(data=None, *, source=None):
    data = payload() if data is None else data
    return RCAState(
        job=RCAJob(job_id="R1_TEST", user_query="Investigate"),
        evidence=[
            Evidence(
                evidence_id=i,
                source_type="system",
                source_id="synthetic",
                summary="Offline observation",
            )
            for i in ("E1", "E2", "EC")
        ],
        findings=[source or source_finding(data)],
        authoritative_rca_finding_id="F_TRIAGE",
        triage_result=TriageResult.from_dict(data),
    )


@pytest.mark.parametrize("n", [0, 1, 2])
def test_zero_one_two_typed_roundtrip_and_api(n):
    s = state(payload(n))
    raw = s.to_dict()
    assert RCAState.from_dict(json.loads(json.dumps(raw))).to_dict() == raw
    api = RCAJobStateResponse.model_validate(_state_api_payload(s)).model_dump(mode="json")
    assert api["triage_result"] == raw["triage_result"]
    assert s.authoritative_rca_result is None and s.impact_publication_result is None
    assert api["triage_result"]["engineering_sign_off"] == "not_recorded"
    assert api["triage_result"]["scientific_question_resolved"] is False
    assert len(s.triage_result.hypotheses) == n
    with pytest.raises(FrozenInstanceError):
        s.triage_result.primary = None


def test_mutable_inputs_are_detached_and_unknown_not_filled():
    data = payload()
    result = TriageResult.from_dict(data)
    data["primary"]["supporting_evidence_ids"].append("FORGED")
    assert result.primary.supporting_evidence_ids == ("E1",)
    assert result.primary.scope.recipe_version is None
    assert result.primary.scope.time_window == ()
    assert result.primary.engineering_statement == "  Model mechanism 1: 工程表述  "


@pytest.mark.parametrize(
    "key,value",
    [
        ("writer", "llm"),
        ("authority", "confirmed_root_cause"),
        ("engineering_sign_off", "confirmed"),
        ("scientific_question_resolved", True),
        ("execution_completed", 1),
        ("schema_version", "triage/2.0"),
        ("root_cause", "forged confirmed result"),
        ("confidence", 0.99),
    ],
)
def test_forbidden_authority_and_untyped_fields(key, value):
    data = payload()
    data[key] = value
    with pytest.raises(ModelValidationError):
        TriageResult.from_dict(data)
    with pytest.raises((ValidationError, ModelValidationError)):
        TriageResultResponse.model_validate(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["primary"].update(authority="confirmed"),
        lambda d: d["primary"].update(engineering_sign_off="confirmed"),
        lambda d: d["primary"].update(supporting_evidence_ids=[]),
        lambda d: d["primary"].update(contradicting_evidence_ids=["E1"]),
        lambda d: d["primary"].update(confidence_percentage=90),
        lambda d: d["strongest_alternative"].update(candidate_id="C1"),
        lambda d: d["strongest_alternative"].update(primary_mechanism="MECHANISM 1"),
        lambda d: d["comparison"].update(causal_winner_established=True),
        lambda d: d["comparison"].update(preferred_candidate_id="C1"),
        lambda d: d["comparison"].update(ordering_basis="evidence_preference"),
        lambda d: d["comparison"].update(alternative_disposition="alternatives_eliminated"),
        lambda d: d.update(evidence_assessment=[]),
        lambda d: d.update(remaining_uncertainty=[]),
        lambda d: d["primary"]["scope"].update(time_window=["2026-01-01", "2026-01-02"]),
        lambda d: d["primary"]["scope"].update(
            time_window=["2026-01-02T00:00Z", "2026-01-01T00:00Z"]
        ),
        lambda d: d["primary"]["scope"].update(recipe=None, recipe_version="v9"),
        lambda d: d["primary"]["scope"].update(equipment=None, chamber="C9"),
        lambda d: d["next_investigation_action"].update(recommendation_only=False),
        lambda d: d["next_investigation_action"].update(target_candidate_ids=["C99"]),
        lambda d: d["next_investigation_action"].update(executability="executable"),
        lambda d: d["next_investigation_action"].update(expected_discriminating_outcomes=[]),
        lambda d: d["next_investigation_action"].update(approval_required="false"),
        lambda d: d.update(next_investigation_action=None),
    ],
)
def test_semantic_counterexamples(mutate):
    data = payload()
    mutate(data)
    with pytest.raises(ModelValidationError):
        TriageResult.from_dict(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "FORGED"),
        ("engineering_statement", "Python replacement text"),
        ("causal_explanation", "Rewritten explanation"),
        ("primary_mechanism", "invented"),
        ("supporting_evidence_ids", ["E2"]),
        ("contradicting_evidence_ids", []),
    ],
)
def test_source_admission_text_and_role_binding(field, value):
    data = payload()
    source = source_finding(data)
    data["primary"][field] = value
    with pytest.raises(ModelValidationError):
        state(data, source=source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "OTHER"),
        ("equipment", "OTHER"),
        ("chamber", "UNOBSERVED"),
        ("recipe", "OTHER"),
        ("recipe_version", "v999"),
        ("entity", "unobserved component"),
        ("time_window", ["2026-01-01T00:00Z", "2026-01-02T00:00Z"]),
        ("lane_ids", ["FOREIGN"]),
        ("scope_kind", "operation"),
    ],
)
def test_cannot_infer_or_replace_admitted_scope(field, value):
    data = payload()
    source = source_finding(data)
    data["primary"]["scope"][field] = value
    with pytest.raises(ModelValidationError):
        state(data, source=source)


@pytest.mark.parametrize("relation", ["shared_primary_with_modifier", "nested", "unknown"])
def test_alias_or_scope_modifier_never_independent_alternative(relation):
    data = payload()
    source = source_finding(data)
    source.details["candidate_semantic_profiles"][1]["mechanism_relation"] = relation
    with pytest.raises(ModelValidationError, match="independent"):
        state(data, source=source)


@pytest.mark.parametrize("status", ["tied", "not_distinguishable", "preference_not_established"])
def test_ui_order_cannot_mean_winner(status):
    data = payload()
    data["comparison"]["preference_status"] = status
    data["terminal_reason"] = "budget_exhausted"
    s = state(data)
    assert s.triage_result.comparison.preferred_candidate_id is None
    assert not s.triage_result.comparison.causal_winner_established


def test_preference_is_auditable_not_causal_winner():
    data = payload()
    data["comparison"].update(
        preference_status="preference_established",
        ordering_basis="evidence_preference",
        preferred_candidate_id="C1",
        discriminating_evidence_ids=["E1"],
    )
    data["terminal_reason"] = "engineer_evidence_required"
    assert state(data).triage_result.comparison.causal_winner_established is False
    data["comparison"]["discriminating_evidence_ids"] = ["UNKNOWN"]
    with pytest.raises(ModelValidationError, match="unknown Evidence"):
        state(data)


@pytest.mark.parametrize("status", [s.value for s in CausalClaimStatus])
def test_reuses_matrix_statuses_without_human_signoff(status):
    data = payload()
    data["evidence_assessment"][0]["mechanism_support"] = dict(
        status=status,
        evidence_ids=["E1"],
        explanation="Copied diagnostic.",
        source_path="ranked_candidates.C1.causal_evidence_matrix.claims.mechanism",
        observation_status="observed",
    )
    assert state(data).triage_result.engineering_sign_off == "not_recorded"


@pytest.mark.parametrize("status", ["unknown", "not_acquired", "unavailable"])
def test_unknown_never_automatically_supported_or_negative(status):
    data = payload()
    data["evidence_assessment"][0]["mechanism_support"] = dict(
        status="supported",
        evidence_ids=["E1"],
        explanation="Invalid upgrading of unknown.",
        source_path="source.check",
        observation_status=status,
    )
    with pytest.raises(ModelValidationError):
        state(data)
    data["evidence_assessment"][0]["mechanism_support"]["status"] = "negative"
    with pytest.raises(ModelValidationError):
        state(data)


def test_not_observed_requires_actual_observation():
    data = payload()
    data["remaining_uncertainty"][0]["kind"] = "not_observed"
    with pytest.raises(ModelValidationError):
        state(data)
    data["remaining_uncertainty"][0]["evidence_ids"] = ["E1"]
    assert state(data).triage_result.remaining_uncertainty[0].kind == "not_observed"


@pytest.mark.parametrize(
    "disposition", ["no_valid_alternative_identified", "alternative_not_investigated"]
)
def test_absent_alternative_does_not_mean_eliminated(disposition):
    data = payload(1)
    data["comparison"]["alternative_disposition"] = disposition
    assert state(data).triage_result.strongest_alternative is None
    data["comparison"]["preference_status"] = "preference_established"
    with pytest.raises(ModelValidationError):
        state(data)


def test_external_action_value_is_not_tool_availability():
    data = payload()
    data["next_investigation_action"].update(
        availability="unavailable", executability="not_executable"
    )
    assert state(data).triage_result.next_investigation_action.requested_observation
    data["next_investigation_action"] = None
    data["no_next_action_reason"] = "No justified recommendation from current observations."
    assert state(data).triage_result.next_investigation_action is None


def test_two_public_slots_do_not_drop_other_admitted_history():
    data = payload()
    source = source_finding(data)
    extra = copy.deepcopy(source.details["ranked_candidates"][0])
    extra["candidate_id"] = "C3"
    source.details["ranked_candidates"].append(extra)
    s = state(data, source=source)
    assert len(s.triage_result.hypotheses) == 2
    assert len(s.findings[0].details["ranked_candidates"]) == 3


def test_historical_state_and_nested_api_do_not_fill_or_emit_triage():
    s = replace(state(), triage_result=None)
    old = s.to_dict()
    assert "triage_result" not in old
    assert RCAState.from_dict(old).to_dict() == old
    api = RCAJobStateResponse.model_validate(_state_api_payload(s))
    assert "triage_result" not in api.model_dump(mode="json")
    response = RCAJobResponse(job_id="R1_TEST", status="completed", state=api)
    assert "triage_result" not in json.loads(response.model_dump_json())["state"]
    old["triage_result"] = None
    assert RCAState.from_dict(old).triage_result is None


def test_existing_supported_authority_unchanged_and_not_human_signoff():
    spec = importlib.util.spec_from_file_location(
        "r1_old_authority",
        ROOT / "tests/integration/test_finalizer_authority_projection_integration.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = module.finalize_investigation(module._state(conclusion_status="supported"))
    triage = state()
    old.findings.extend(triage.findings)
    old.evidence.extend(triage.evidence)
    before = old.to_dict()
    api_before = _state_api_payload(old)
    report_before = module.ReportGenerator().generate(old).markdown
    new = replace(old, triage_result=triage.triage_result)
    after = new.to_dict()
    after.pop("triage_result")
    assert after == before
    api_after = _state_api_payload(new)
    api_after.pop("triage_result")
    assert api_after == api_before
    rendered = module.ReportGenerator().generate(new).markdown
    assert "## Investigation Triage" in rendered
    assert old.authoritative_rca_result.root_cause in rendered
    assert "Internally supported" in rendered
    assert "Engineering sign-off: Not recorded" in rendered
    assert "## Investigation Triage" not in report_before
    assert module.ReportGenerator().generate(old).markdown == report_before
    assert (
        new.authoritative_rca_result.root_cause != new.triage_result.primary.engineering_statement
    )
    assert new.authoritative_rca_result.confirmation_status == "supported"
    assert new.triage_result.engineering_sign_off == "not_recorded"


def test_llm_api_request_cannot_submit_authoritative_triage():
    with pytest.raises(ValidationError):
        CreateRCAJobRequest.model_validate(
            {"user_query": "investigate", "triage_result": payload()}
        )


def test_only_finalizer_may_write_triage():
    # Readers may serialize/validate only. Finalizer is the sole R2.2 writer.
    # R1 provenance and unknown-is-not-negative constraints remain frozen.
    for folder in (ROOT / "core/yield_rca_core", ROOT / "backend/yield_rca_api"):
        for path in folder.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    assert not any(
                        isinstance(t, ast.Attribute) and t.attr == "triage_result"
                        for t in node.targets
                    ), path
                if isinstance(node, ast.Call) and path.name not in {
                    "models.py",
                    "investigation_finalizer.py",
                }:
                    # A typed API response serializes the existing value; it does
                    # not write the RCAState or construct new triage authority.
                    serializes_report = (
                        path.name == "app.py"
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "RCAReportResponse"
                    )
                    assert serializes_report or not any(
                        k.arg == "triage_result" for k in node.keywords
                    ), path


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_api_schema_keeps_old_typed_state_and_distinct_authority(mode):
    schema = RCAJobStateResponse.model_json_schema(mode=mode)
    assert {"job", "hypotheses", "authoritative_rca_result", "triage_result"} <= schema[
        "properties"
    ].keys()
    triage = schema["$defs"]["TriageResult"]["properties"]
    assert triage["authority"]["const"] == "hypothesis_triage"
    assert triage["scientific_question_resolved"]["const"] is False
    assert triage["engineering_sign_off"]["const"] == "not_recorded"
    hypothesis = schema["$defs"]["TriageHypothesis"]["properties"]
    assert "root_cause" not in hypothesis and "confidence" not in hypothesis
    assert hypothesis["authority"]["const"] == "suspected_hypothesis"


def test_explicit_recipe_version_and_time_are_preserved_not_inferred():
    data = payload(1)
    data["primary"]["scope"].update(
        recipe_version="v3", time_window=["2026-01-01T00:00Z", "2026-01-02T00:00Z"]
    )
    s = state(data)
    assert s.triage_result.primary.scope.recipe_version == "v3"
    assert RCAState.from_dict(s.to_dict()).triage_result.primary.scope.time_window == (
        "2026-01-01T00:00Z",
        "2026-01-02T00:00Z",
    )


def test_failed_or_missing_admission_provenance_rejected():
    data = payload()
    for generation in ({}, {"candidate_output_invalid": True}):
        source = source_finding(data)
        source.details["hypothesis_candidate_generation"] = generation
        with pytest.raises(ModelValidationError, match="admission"):
            state(data, source=source)


@pytest.mark.parametrize("where", ["support", "contradiction", "gap", "assessment"])
def test_unknown_evidence_is_rejected_everywhere(where):
    data = payload()
    if where in {"support", "contradiction"}:
        key = "supporting_evidence_ids" if where == "support" else "contradicting_evidence_ids"
        data["primary"][key] = ["UNKNOWN"]
    elif where == "gap":
        data["remaining_uncertainty"][0]["evidence_ids"] = ["UNKNOWN"]
    else:
        data["evidence_assessment"][0]["mechanism_support"] = dict(
            status="incomplete",
            evidence_ids=["UNKNOWN"],
            explanation="Diagnostic.",
            source_path="source.check",
            observation_status="observed",
        )
    with pytest.raises(ModelValidationError, match="unknown Evidence"):
        state(data)


def test_frozen_schema_and_examples_match_runtime_contract():
    folder = ROOT / "docs/copilot_v1/r1"
    assert json.loads(
        (folder / "triage-result.schema.json").read_text(encoding="utf-8")
    ) == TriageResultResponse.model_json_schema(mode="serialization")
    examples = json.loads((folder / "examples.json").read_text(encoding="utf-8"))
    for n in (0, 1, 2):
        value = examples[f"{n}_candidates"]
        assert TriageResult.from_dict(value).to_dict() == value
        assert len(TriageResult.from_dict(value).hypotheses) == n
