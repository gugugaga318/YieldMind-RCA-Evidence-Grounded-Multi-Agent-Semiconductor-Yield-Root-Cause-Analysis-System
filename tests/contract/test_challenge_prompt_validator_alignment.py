from __future__ import annotations

import copy
import json
import socket
from dataclasses import asdict

import pytest
from challenge_diagnostics_support import before_observability
from challenge_projection_support import ResponseTape, inputs
from test_challenge_rejected_output_diagnostics import citation_fixture, outcome
from yield_rca_core import causal_adversarial as current
from yield_rca_core.challenge_projection import expand_challenge_payload
from yield_rca_core.evidence_models import Evidence
from yield_rca_core.llm_gateway import LLMOutputValidationError, load_prompt

STATES = [
    "open", "unresolved", "alternative_identified", "resolved", "blocked", "non_discriminative",
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Alignment tests cannot connect to a network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def scenario(status="unresolved", matching=True):
    raw, kw = citation_fixture("accepted")
    raw["status"] = status
    gap = {
        "gap_id": "GAP_A", "candidate_id": "CAND_A",
        "gap_type": "hypothesis_discrimination", "discriminator_kind": "parameter_anomaly",
        "lane_binding": "challenge_selected", "applicable_lane_ids": ["LANE_A"],
        "information_gain": 1, "information_gain_by_lane": {"LANE_A": 1},
    }
    kw["lane_contexts"]["LANE_A"]["parameter_scope"] = ["pressure"]
    kw["gap_by_id"] = {"GAP_A": gap}
    kw["gap_ids"] = {"GAP_A"}
    raw["distinguishing_gap_ids"] = ["GAP_A"]
    target = kw["evidence_by_id"]["EV_MATCH"].to_dict()
    target["evidence_type"] = "equipment_exposure"
    target["metadata"] = {"lane_id": "LANE_A"}
    target["entities"].append({"entity_type": "recipe", "entity_id": "RCP_A", "attributes": {}})
    target["summary"] = "Exposure only; no parameter anomaly is established."
    kw["lane_contexts"]["LANE_A"]["recipe"] = "RCP_A"
    kw["evidence_by_id"]["EV_MATCH"] = Evidence.from_dict(target)
    for i in range(3):
        other = copy.deepcopy(target)
        other["evidence_id"] = f"EV_OTHER_{i}"
        other["metadata"]["lane_id"] = "LANE_B"
        other["entities"][-1]["entity_id"] = "RCP_B"
        kw["evidence_by_id"][other["evidence_id"]] = Evidence.from_dict(other)
    kw["evidence_ids"] = set(kw["evidence_by_id"])
    raw["supporting_evidence_ids"] = ["EV_OTHER_0", "EV_OTHER_1", "EV_OTHER_2"]
    if matching:
        raw["supporting_evidence_ids"].append("EV_MATCH")
    return raw, kw


def generation_inputs(kw):
    matrix = inputs("qwen")["matrices"][0]
    return dict(
        request_id="alignment", candidates=[{"candidate_id": "CAND_A"}], matrices=[matrix],
        evidence_gaps=list(kw["gap_by_id"].values()),
        evidence_ids=sorted(kw["evidence_ids"]), evidence_by_id=kw["evidence_by_id"],
        lane_ids=["LANE_A", "LANE_B"], active_lane_ids=["LANE_A"],
        lane_contexts=list(kw["lane_contexts"].values()),
    )


def response(raw):
    return {"challenges": [raw], "analysis_summary": "A bounded alternative probe."}


@pytest.mark.parametrize("status", STATES)
@pytest.mark.parametrize("matching", [False, True])
def test_all_statuses_keep_original_citation_verdict(status, matching):
    raw, kw = scenario(status, matching)
    expected = outcome(before_observability()._normalize_challenge_payload, raw, kw)
    actual = outcome(current._normalize_challenge_payload, raw, kw)
    assert actual == expected
    assert actual[0] == ("accept" if matching else "reject")
    # Same public prompt for every status; no state-specific or provider-specific bypass.
    prompt = load_prompt("causal_adversarial_challenge", "v1")
    assert "regardless of status" in prompt
    assert "at least one" in prompt
    assert "comparison context" in prompt
    for name in STATES:
        assert name in prompt


def test_exposure_anchor_does_not_promote_mechanism_or_confirmation():
    raw, kw = scenario()
    before = generation_inputs(kw)
    tape = ResponseTape([response(raw)])
    result = current.QwenAdversarialChallenger(tape).generate(**before)
    assert result.challenges and result.attempt_count == 1
    assert result.challenges[0].status == "unresolved"
    assert result.alternative_search_status != "alternatives_eliminated"
    assert before == generation_inputs(kw)
    assert all(e.evidence_type == "equipment_exposure" for e in kw["evidence_by_id"].values())
    assert not current._is_typed_precursor(kw["evidence_by_id"]["EV_MATCH"])
    prompt = load_prompt("causal_adversarial_challenge", "v1")
    assert "does not establish parameter anomaly" in prompt
    assert "physical mechanism" in prompt and "root cause" in prompt


@pytest.mark.parametrize("repair_matches", [True, False])
def test_repair_receives_specific_failure_and_keeps_validator_authority(repair_matches):
    bad, kw = scenario(matching=False)
    repaired, _ = scenario(matching=repair_matches)
    kwargs = generation_inputs(kw)
    tape = ResponseTape([response(bad), response(repaired)])
    result = current.QwenAdversarialChallenger(tape).generate(**kwargs)
    assert len(tape.requests) == result.attempt_count == 2
    initial, repair = [expand_challenge_payload(r.payload) for r in tape.requests]
    assert initial["previous_validation_feedback"] is None
    feedback = repair["previous_validation_feedback"]["citation_failures"]
    assert len(feedback) == 1
    item = feedback[0]
    assert item["target_lane"]["lane_id"] == "LANE_A"
    assert item["candidate_id"] == "CAND_A"
    assert item["rejection_codes"] == ["lane_mismatch"]
    assert {e["evidence_id"] for e in item["offending_evidence"]} == set(
        bad["supporting_evidence_ids"]
    )
    assert all(e["actual_lane_id"] == "LANE_B" for e in item["offending_evidence"])
    assert all(e["roles"] == ["supporting_evidence_ids"] for e in item["offending_evidence"])
    assert "at least one" in item["requirement"]
    assert "EV_MATCH" not in json.dumps(feedback)  # No Python-selected replacement answer.
    assert "challenge_explanation" not in json.dumps(feedback)  # No raw response dump.
    old = before_observability().QwenAdversarialChallenger(
        ResponseTape([response(bad), response(repaired)])
    ).generate(**kwargs)
    a, b = asdict(result), asdict(old)
    a.pop("prompt_audit")
    b.pop("prompt_audit")
    assert a == b
    assert bool(result.challenges) == repair_matches
    assert result.output_invalid == (not repair_matches)
    for key in initial:
        if key not in {"output_attempt", "previous_validation_feedback"}:
            assert initial[key] == repair[key]


@pytest.mark.parametrize("kind", ["entity_mismatch", "time_mismatch", "lane_mismatch"])
def test_feedback_reuses_actual_matcher_short_circuit(kind):
    from yield_rca_core.challenge_citation_feedback import capture_citation_failure
    raw, kw = citation_fixture(kind)
    failures = []
    with pytest.raises(LLMOutputValidationError), capture_citation_failure(raw, failures):
        current._normalize_challenge_payload(raw, **kw)
    check = failures[0]["offending_evidence"][0]
    assert check["rejection_code"] == kind
    if kind == "lane_mismatch":
        assert check["entity_match"] is None and check["time_match"] is None


def test_feedback_bounded_redacted_and_not_persisted(monkeypatch):
    from yield_rca_core.challenge_citation_feedback import (
        MAX_FEEDBACK_CHARS,
        capture_citation_failure,
    )
    raw, kw = scenario(matching=False)
    monkeypatch.setenv("TEST_API_KEY", "sensitive-value-example")
    raw["candidate_id"] = "sensitive-value-example"
    kw["candidate_ids"] = {raw["candidate_id"]}
    raw["distinguishing_gap_ids"] = []
    raw["status"] = "blocked"
    raw["challenge_explanation"] = "Bearer should-not-be-copied " * 50000
    failures = []
    for _ in range(5):
        with pytest.raises(LLMOutputValidationError), capture_citation_failure(raw, failures):
            current._normalize_challenge_payload(raw, **kw)
    encoded = json.dumps(failures, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) <= MAX_FEEDBACK_CHARS
    assert len(failures) <= 2
    assert "sensitive-value-example" not in encoded
    assert "should-not-be-copied" not in encoded

@pytest.mark.parametrize("capture_enabled", [False, True])
def test_feedback_independent_of_optional_artifact_capture(capture_enabled):
    from contextlib import nullcontext

    from yield_rca_core.challenge_diagnostics import capture_challenge_diagnostics
    bad, kw = scenario(matching=False)
    good, _ = scenario()
    tape = ResponseTape([response(bad), response(good)])
    context = capture_challenge_diagnostics() if capture_enabled else nullcontext()
    with context:
        result = current.QwenAdversarialChallenger(tape).generate(**generation_inputs(kw))
    feedback = expand_challenge_payload(tape.requests[1].payload)["previous_validation_feedback"]
    assert feedback["citation_failures"][0]["rejection_codes"] == ["lane_mismatch"]
    assert result.attempt_count == 2 and result.challenges


def test_feedback_large_checks_are_bounded_and_explicitly_omitted():
    from yield_rca_core.challenge_citation_feedback import (
        MAX_FEEDBACK_CHARS,
        capture_citation_failure,
    )
    raw, kw = scenario(matching=False)
    original = kw["evidence_by_id"]["EV_OTHER_0"].to_dict()
    for i in range(100):
        value = copy.deepcopy(original)
        value["evidence_id"] = f"EV_LARGE_{i:03d}"
        value["entities"] *= 20
        kw["evidence_by_id"][value["evidence_id"]] = Evidence.from_dict(value)
    kw["evidence_ids"] = set(kw["evidence_by_id"])
    raw["supporting_evidence_ids"] = sorted(e for e in kw["evidence_ids"] if e != "EV_MATCH")
    failures = []
    with pytest.raises(LLMOutputValidationError), capture_citation_failure(raw, failures):
        current._normalize_challenge_payload(raw, **kw)
    assert failures[0]["omitted_evidence_checks"] > 0
    size = len(json.dumps(failures, ensure_ascii=False, separators=(",", ":")))
    assert size <= MAX_FEEDBACK_CHARS


def test_no_checks_leak_into_later_accepted_validation():
    from yield_rca_core.challenge_citation_feedback import capture_citation_failure
    bad, kw = scenario(matching=False)
    good, _ = scenario()
    failures = []
    with pytest.raises(LLMOutputValidationError), capture_citation_failure(bad, failures):
        current._normalize_challenge_payload(bad, **kw)
    first = copy.deepcopy(failures)
    with capture_citation_failure(good, failures):
        current._normalize_challenge_payload(good, **kw)
    assert failures == first

def test_exposure_only_matrix_and_gate_remain_conservative():
    from yield_rca_core.causal_confirmation import confirm_candidate
    from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix
    raw, kw = scenario()
    hypothesis = {
        "root_cause": "RCP_A on CH_A may have a pressure-related mechanism.",
        "causal_explanation": "A proposed physical mechanism requires further evidence.",
        "supporting_evidence_ids": ["EV_MATCH"], "contradicting_evidence_ids": [],
    }
    matrix = build_causal_evidence_matrix(hypothesis, kw["evidence_by_id"].values())
    assert matrix.mechanism_status != "supported"
    before = matrix.to_dict()
    tape = ResponseTape([response(raw)])
    args = generation_inputs(kw)
    args["matrices"] = [matrix]
    result = current.QwenAdversarialChallenger(tape).generate(**args)
    assert result.challenges  # Lane anchor accepted without inventing parameter facts.
    gate = confirm_candidate(matrix, alternative_search_status=result.alternative_search_status)
    assert gate.status != "supported"
    assert matrix.to_dict() == before


def test_formal012_initial_and_citation_repair_payload_budget():
    kwargs = inputs("pro")
    target = next(lane for lane in kwargs["lane_contexts"] if lane["recipe"] == "RCP_184CE7C6")
    wrong = sorted(
        eid for eid, e in kwargs["evidence_by_id"].items()
        if e.evidence_type == "parameter_deviation"
        and not current._evidence_matches_lane(e, target)
    )
    assert len(wrong) >= 3
    raw = {
        "candidate_id": kwargs["candidates"][0]["candidate_id"],
        "alternative_candidate_id": kwargs["candidates"][1]["candidate_id"],
        "evidence_probe_lane_id": target["lane_id"],
        "challenge_kind": "mechanism", "mechanism_relation": "independent_alternative",
        "supporting_evidence_ids": wrong[:3], "contradicting_evidence_ids": [],
        "unexplained_precursor_evidence_ids": [],
        "distinguishing_gap_ids": ["candidate_0.hypothesis_discrimination.parameter_anomaly"],
        "distinguishing_questions": [], "challenge_explanation": "Investigate this alternative.",
        "status": "unresolved",
    }
    client = ResponseTape([response(raw), response(raw)])
    result = current.QwenAdversarialChallenger(client).generate(**kwargs)
    assert len(client.requests) == result.attempt_count == 2
    sizes = []
    for req in client.requests:
        size = len(json.dumps(req.payload, ensure_ascii=False, separators=(",", ":")))
        assert size < 64000
        sizes.append(size)
    assert sizes[0] == 53816  # Existing projection replay is unchanged.
    assert sizes[1] < 59000  # Fixture regression assertion, not a new business gate.
    feedback = expand_challenge_payload(client.requests[1].payload)["previous_validation_feedback"]
    assert feedback["citation_failures"][0]["rejection_codes"] == ["lane_mismatch"]
    assert len(feedback["citation_failures"][0]["offending_evidence"]) == 3
    assert result.prompt_audit["remaining_chars"] == 64000 - sizes[1]
    assert result.prompt_audit["combined_request_chars"] == (
        sizes[1] + len(load_prompt("causal_adversarial_challenge", "v1"))
    )


def test_empty_citation_feedback_keeps_target_without_selecting_evidence():
    raw, kw = scenario(matching=False)
    raw["supporting_evidence_ids"] = []
    client = ResponseTape([response(raw), response(raw)])
    current.QwenAdversarialChallenger(client).generate(**generation_inputs(kw))
    feedback = expand_challenge_payload(client.requests[1].payload)["previous_validation_feedback"]
    item = feedback["citation_failures"][0]
    assert item["target_lane"]["lane_id"] == "LANE_A"
    assert item["rejection_codes"] == ["missing_required_evidence"]
    assert item["offending_evidence"] == []
