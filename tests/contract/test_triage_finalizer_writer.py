"""R2.2: independent authority, terminal safety and honest deterministic projections."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from yield_rca_api.app import _state_api_payload
from yield_rca_api.schemas import RCAJobStateResponse
from yield_rca_core.causal_investigation_models import ActionValueAssessment
from yield_rca_core.evidence_models import Evidence
from yield_rca_core.investigation_finalizer import (
    _attach_authority_results,
    finalize_non_competition_llm_react_terminal,
)
from yield_rca_core.models import AgentFinding, Hypothesis, RCAJob, RCAState


def fixture(n=2, *, status="inconclusive", job_status="completed"):
    rows, profiles = [], []
    for i in range(n):
        cid, eid = f"C{i}", f"E{i}"
        rows.append(
            dict(
                candidate_id=cid,
                root_cause=f" Original mechanism {i} ",
                causal_explanation=f"Existing explanation {i}",
                basis="llm_evidence_composition",
                status="candidate",
                supporting_evidence_ids=[eid],
                contradicting_evidence_ids=[],
                causal_evidence_matrix={
                    "claims": {
                        "parameter": dict(
                            status="supported", evidence_ids=[eid], reason="Measured"
                        ),
                        "mechanism": dict(
                            status="plausible", evidence_ids=[eid], reason="Bridge unproven"
                        ),
                    }
                },
            )
        )
        profiles.append(
            dict(
                candidate_id=cid,
                primary_mechanism=f"mechanism {i}",
                mechanism_relation="reference" if not i else "independent_alternative",
                depends_on_candidate_id=None,
                claimed_scope_kind="equipment",
                claimed_operation="OP",
                claimed_equipment="EQ",
                claimed_chamber=None,
                claimed_recipe=None,
                claimed_lane_ids=["L1"],
                comparison_lane_ids=["L2"],
                distinguishing_predictions=[
                    dict(
                        discriminator_kind="parameter_anomaly",
                        lane_ids=["L1"],
                        prediction=f"Existing model prediction {i}",
                    )
                ],
            )
        )
    details = dict(
        ranked_candidates=rows,
        candidate_semantic_profiles=profiles,
        hypothesis_candidate_generation={"candidate_output_invalid": False},
        conclusion_status=status,
        root_cause="OLD CONFIRMED" if status == "supported" else None,
        confirmation_gate={"status": status},
        alternative_search_status="unresolved",
        candidate_comparison={
            "final_ranks": [
                dict(root_cause=r["root_cause"], python_matrix_score=16, rank=i + 1)
                for i, r in enumerate(rows)
            ]
        },
    )
    return RCAState(
        job=RCAJob(job_id="OFFLINE_TRIAGE", user_query="Investigate", status=job_status),
        evidence=[
            Evidence(
                evidence_id=f"E{i}",
                source_type="system",
                source_id="fixture",
                summary="Observation",
            )
            for i in range(max(n, 1))
        ],
        findings=[
            AgentFinding(
                finding_id="F",
                agent="rca_reasoning",
                summary="Admitted",
                confidence=0.5,
                evidence_ids=[f"E{i}" for i in range(max(n, 1))],
                details=details,
            )
        ],
        authoritative_rca_finding_id="F",
        hypotheses=[
            Hypothesis(
                hypothesis_id="H",
                root_cause="OLD CONFIRMED",
                confidence=0.7,
                evidence_ids=["E0"],
                status=status,
            )
        ]
        if status == "supported"
        else [],
        authoritative_hypothesis_id="H" if status == "supported" else None,
    )


def details(state):
    return state.authoritative_rca_finding.details


def publish(state, competition="exhausted"):
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
        return _attach_authority_results(
            state,
            conclusion_status=details(state)["conclusion_status"],
            competition_status="complete_confirmed"
            if details(state)["conclusion_status"] == "supported"
            else competition,
            terminal_reason="Existing terminal reason",
        )


@pytest.mark.parametrize("n", [0, 1, 2, 4])
def test_candidate_cardinality_provenance_history_and_roundtrip(n):
    s = fixture(n)
    before = copy.deepcopy(s.to_dict())
    out = publish(s)
    t = out.triage_result
    assert t is not None and len(t.hypotheses) == min(n, 2)
    assert len(details(out)["ranked_candidates"]) == n
    for i, h in enumerate(t.hypotheses):
        assert h.candidate_id == f"C{i}" and h.engineering_statement == f" Original mechanism {i} "
        assert h.supporting_evidence_ids == (f"E{i}",)
    assert s.to_dict() == before
    assert RCAState.from_dict(json.loads(json.dumps(out.to_dict()))).to_dict() == out.to_dict()
    assert publish(s).triage_result == t
    api = RCAJobStateResponse.model_validate(_state_api_payload(out)).model_dump(mode="json")
    assert api["triage_result"] == t.to_dict()
    assert out.authoritative_rca_result.root_cause is None
    assert out.impact_publication_result.confirmed_impact_lots == ()


@pytest.mark.parametrize("status", ["inconclusive", "insufficient_evidence", "supported"])
def test_old_authority_exactly_preserved_and_not_human_signoff(status):
    s = fixture(status=status)
    with patch("yield_rca_core.investigation_finalizer.build_triage_projection", return_value=None):
        old = publish(s)
    out = publish(s)
    raw = out.to_dict()
    raw.pop("triage_result")
    assert raw == old.to_dict()
    assert out.triage_result.engineering_sign_off == "not_recorded"
    assert out.triage_result.primary.engineering_sign_off == "not_recorded"
    assert out.authoritative_rca_result.root_cause == (
        "OLD CONFIRMED" if status == "supported" else None
    )
    assert details(out)["confirmation_gate"]["status"] == status


def test_noncompetition_entry_writes_separate_result():
    out = finalize_non_competition_llm_react_terminal(fixture())
    assert out.triage_result and out.authoritative_rca_result.root_cause is None


def test_tie_and_ranking_do_not_establish_causal_winner():
    s = fixture()
    t = publish(s).triage_result
    assert t.comparison.preference_status == "tied"
    assert not t.comparison.causal_winner_established
    details(s)["candidate_comparison"]["final_ranks"][0]["python_matrix_score"] = 30
    t = publish(s).triage_result
    assert t.primary.candidate_id == "C0"
    assert t.comparison.preference_status == "not_distinguishable"
    assert t.comparison.preferred_candidate_id is None


@pytest.mark.parametrize(
    "bad", ["modifier", "dependent", "same_mechanism", "rejected", "contradicted", "unknown_id"]
)
def test_invalid_alternative_not_published(bad):
    s = fixture()
    p = details(s)["candidate_semantic_profiles"][1]
    r = details(s)["ranked_candidates"][1]
    if bad == "modifier":
        p["mechanism_relation"] = "modifier"
    if bad == "dependent":
        p["depends_on_candidate_id"] = "C0"
    if bad == "same_mechanism":
        p["primary_mechanism"] = "mechanism 0"
    if bad == "rejected":
        r["status"] = "rejected"
    if bad == "contradicted":
        details(s)["candidate_resolutions"] = [dict(candidate_id="C1", status="contradicted")]
    if bad == "unknown_id":
        r["supporting_evidence_ids"] = ["UNKNOWN"]
    t = publish(s).triage_result
    assert t.primary.candidate_id == "C0" and t.strongest_alternative is None
    assert t.comparison.alternative_disposition == "alternative_not_investigated"


def test_conflicted_candidate_keeps_conflict_not_confirmation():
    s = fixture()
    details(s)["ranked_candidates"][0]["causal_evidence_matrix"]["claims"]["mechanism"][
        "status"
    ] = "conflicted"
    t = publish(s).triage_result
    assert t.evidence_assessment[0].mechanism_support.status == "conflicted"
    assert "conflicting" in {g.kind for g in t.remaining_uncertainty}


@pytest.mark.parametrize(
    "search,expected",
    [
        ("not_searched", "alternative_not_investigated"),
        ("alternative_found", "no_valid_alternative_identified"),
        ("alternatives_eliminated", "no_valid_alternative_identified"),
    ],
)
def test_absent_alternative_never_invents_exclusion(search, expected):
    s = fixture(1)
    details(s)["alternative_search_status"] = search
    t = publish(s).triage_result
    assert t.comparison.alternative_disposition == expected
    assert t.comparison.preference_status == "preference_not_established"


@pytest.mark.parametrize(
    "competition,stop",
    [
        ("budget_exhausted", "budget_exhausted"),
        ("exhausted", "no_high_value_action"),
        ("blocked_by_missing_data", "data_unavailable"),
    ],
)
def test_science_unresolved_is_valid_terminal(competition, stop):
    s = replace(fixture(), stop_reason=stop)
    out = publish(s, competition)
    assert out.triage_result.terminal_reason == stop
    assert out.triage_result.scientific_question_resolved is False
    assert "unresolved_alternative" in {g.kind for g in out.triage_result.remaining_uncertainty}


@pytest.mark.parametrize("job_status", ["failed", "cancelled", "running", "pending", "retry_wait"])
def test_execution_failure_or_live_state_cannot_publish(job_status):
    assert publish(fixture(job_status=job_status)).triage_result is None


@pytest.mark.parametrize("competition", ["active", "pending", "failed", "not_evaluated"])
def test_nonterminal_or_failed_competition_cannot_publish(competition):
    assert publish(fixture(), competition).triage_result is None


def test_successfully_finalizing_budget_runtime_can_publish():
    s = replace(fixture(job_status="running"), stop_reason="budget_exhausted")
    assert publish(s, "budget_exhausted").triage_result is not None


@pytest.mark.parametrize(
    "status,kind",
    [
        ("not_acquired", "not_acquired"),
        ("unknown", "unavailable"),
        ("unavailable", "unavailable"),
        ("partial", "not_acquired"),
    ],
)
def test_coverage_on_comparison_lane_cannot_become_negative(status, kind):
    s = fixture()
    details(s)["evidence_synthesis"] = {
        "active_causal_lanes": [
            dict(
                lane_id="L2",
                outcome_observation_coverage=dict(
                    status=status, not_acquired_lot_ids=["LOT2"] if status == "partial" else []
                ),
            )
        ]
    }
    t = publish(s).triage_result
    gaps = [g for g in t.remaining_uncertainty if g.description.startswith("Outcome coverage")]
    assert gaps and all(g.kind == kind for g in gaps)
    assert all(g.scope.lane_ids == ("L2",) for g in gaps)
    assert all(h.scope.lane_ids == ("L1",) for h in t.hypotheses)


@pytest.mark.parametrize("observation", ["unknown", "not_acquired", "not_observed"])
def test_claim_observation_status_never_upgraded(observation):
    s = fixture(1)
    claim = details(s)["ranked_candidates"][0]["causal_evidence_matrix"]["claims"]["mechanism"]
    claim["observation_status"] = observation
    t = publish(s).triage_result
    assert t.evidence_assessment[0].mechanism_support.observation_status == observation
    assert t.evidence_assessment[0].mechanism_support.status == "plausible"


def with_action(*, executable=False):
    s = fixture()
    details(s)["causal_evidence_gaps"] = [
        dict(
            gap_id="G",
            candidate_id="C0",
            discriminator_kind="parameter_anomaly",
            reason=(
                "Acquire the observations needed to compare the persisted conditional predictions."
            ),
            allowed_actions=["inspect_fdc_spc"],
            target_scope=dict(operation="OP", equipment="EQ", lane_id="L1"),
        )
    ]
    option = ActionValueAssessment(
        option_id="A",
        action_kind="inspect_fdc_spc",
        scope_fingerprint="S",
        static_information_gain=0.8,
        source_availability="available" if executable else "unknown",
        decision_impact="ranking",
        estimated_tool_cost=1,
        estimated_llm_cost=0,
        remaining_tool_budget=3,
        high_value=executable,
        eligible=executable,
        value_tier="high" if executable else "low",
        candidate_id="C0",
        gap_id="G",
        discriminator_kind="parameter_anomaly",
        rejection_reason=None if executable else "discriminative_analysis_not_executable",
        discriminative_analysis_executability="not_assessed"
        if executable
        else "engineer_evidence_required",
    )
    return replace(s, latest_action_value_assessments=[option])


@pytest.mark.parametrize("executable", [True, False])
def test_action_handoff_no_execution_or_new_prediction(executable):
    s = with_action(executable=executable)
    before = s.to_dict()
    t = publish(s).triage_result
    a = t.next_investigation_action
    assert a.target_candidate_ids == ("C0", "C1")
    assert a.executor == ("system_tool" if executable else "human_engineer")
    assert a.executability == ("executable" if executable else "external_only")
    assert a.recommendation_only and s.to_dict() == before
    assert [x.expected_observation for x in a.expected_discriminating_outcomes] == [
        f"Existing model prediction {i}" for i in range(2)
    ]
    if not executable:
        assert t.terminal_reason == "engineer_evidence_required"


@pytest.mark.parametrize("missing", ["prediction", "same_prediction", "scope"])
def test_no_invented_action_without_compatible_prediction(missing):
    s = with_action()
    p = details(s)["candidate_semantic_profiles"][1]["distinguishing_predictions"][0]
    if missing == "prediction":
        p["prediction"] = ""
    if missing == "same_prediction":
        p["prediction"] = "Existing model prediction 0"
    if missing == "scope":
        p["lane_ids"] = ["OTHER"]
    t = publish(s).triage_result
    assert t.next_investigation_action is None and t.no_next_action_reason


def test_historical_state_api_never_backfill_from_rank():
    s = fixture()
    raw = s.to_dict()
    assert "triage_result" not in raw
    loaded = RCAState.from_dict(raw)
    assert loaded.triage_result is None
    assert _state_api_payload(loaded).get("triage_result") is None


def test_unsupported_uncited_negative_check_not_relabelled_observed():
    s = fixture(1)
    details(s)["ranked_candidates"][0]["causal_evidence_matrix"]["claims"]["contradiction"] = dict(
        status="supported", evidence_ids=[], reason="No cited contradiction"
    )
    t = publish(s).triage_result
    assert t.evidence_assessment[0].contradiction is None
    assert any("contradiction" in g.description for g in t.remaining_uncertainty)


@pytest.mark.parametrize("provider,count", [("deepseek", 2), ("dashscope", 1)])
def test_saved_formal005_projection_offline(provider, count):
    path = (
        Path(__file__).resolve().parents[2]
        / "outputs/benchmark_pilot_v1_1/runs"
        / f"pilot_v1_FORMAL_005_{provider}_r1/raw/states/FORMAL_005.json"
    )
    if not path.exists():
        pytest.skip("Local immutable Pilot artifact unavailable; portable fixture tests still run")
    raw = json.loads(path.read_text(encoding="utf-8"))
    s = RCAState.from_dict(raw)
    before = s.to_dict()
    out = publish(s)
    t = out.triage_result
    assert len(t.hypotheses) == count
    assert t.primary.candidate_id == details(s)["ranked_candidates"][0]["candidate_id"]
    assert t.evidence_assessment[0].mechanism_support.status == "plausible"
    assert {"unsupported_mechanism", "unresolved_alternative"} <= {
        g.kind for g in t.remaining_uncertainty
    }
    assert out.authoritative_rca_result.root_cause is None
    assert s.to_dict() == before
    if provider == "deepseek":
        assert t.comparison.preference_status == "tied"
        assert t.next_investigation_action.executor == "human_engineer"


def test_equipment_conflict_cannot_hide_behind_supported_chamber():
    s = fixture(1)
    details(s)["candidate_semantic_profiles"][0]["claimed_chamber"] = "CH"
    claims = details(s)["ranked_candidates"][0]["causal_evidence_matrix"]["claims"]
    claims["equipment"] = dict(status="conflicted", evidence_ids=["E0"], reason="Wrong equipment")
    claims["chamber"] = dict(status="supported", evidence_ids=["E0"], reason="Chamber check")
    t = publish(s).triage_result
    assert t.evidence_assessment[0].entity_consistency.status == "conflicted"


def test_action_wrong_lane_not_projected():
    s = with_action(executable=True)
    details(s)["causal_evidence_gaps"][0]["target_scope"]["lane_id"] = "OTHER"
    assert publish(s).triage_result.next_investigation_action is None


def test_unavailable_action_retains_source_status():
    s = with_action()
    s = replace(
        s,
        latest_action_value_assessments=[
            replace(s.latest_action_value_assessments[0], source_availability="unavailable")
        ],
    )
    a = publish(s).triage_result.next_investigation_action
    assert a.availability == "unavailable" and a.executor == "human_engineer"


@pytest.mark.parametrize(
    "bad", ["duplicate_ids", "bad_gap_shape", "invalid_gap_scope", "non_object_matrix"]
)
def test_incompatible_legacy_diagnostics_withhold_only_new_authority(bad):
    s = with_action()
    if bad == "duplicate_ids":
        details(s)["ranked_candidates"][0]["candidate_id"] = ["not-a-stable-id"]
    elif bad == "bad_gap_shape":
        details(s)["causal_evidence_gaps"][0]["expected_evidence_types"] = [42]
    elif bad == "non_object_matrix":
        details(s)["ranked_candidates"][0]["causal_evidence_matrix"]["claims"] = []
    else:
        details(s)["evidence_synthesis"] = {
            "active_causal_lanes": [
                dict(
                    lane_id="L2",
                    chamber="UNBOUND_CHAMBER",
                    outcome_observation_coverage=dict(status="not_acquired"),
                )
            ]
        }
    with patch("yield_rca_core.investigation_finalizer.build_triage_projection", return_value=None):
        old = publish(s)
    out = publish(s)
    assert out.triage_result is None
    assert out.authoritative_rca_result == old.authoritative_rca_result
    assert out.impact_publication_result == old.impact_publication_result
    assert out.findings == old.findings
    assert out.execution_metadata["triage_projection_status"] == "withheld_invalid_projection"


def test_no_triage_on_failed_trace_even_if_authority_metadata_claims_terminal():
    from yield_rca_core.causal_investigation_models import CompetitionTrace

    s = replace(
        fixture(),
        competition_trace=CompetitionTrace(
            competition_status="failed", competition_failure_reason="semantic_profile_invalid"
        ),
    )
    assert publish(s).triage_result is None
