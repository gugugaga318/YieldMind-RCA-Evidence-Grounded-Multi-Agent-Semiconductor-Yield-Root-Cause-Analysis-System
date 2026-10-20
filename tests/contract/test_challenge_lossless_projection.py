from __future__ import annotations

import copy
import json
import socket
from dataclasses import asdict

import pytest
from challenge_projection_support import ResponseTape, frozen, inputs, legacy
from yield_rca_core import causal_adversarial as current
from yield_rca_core.challenge_projection import (
    PROJECTION_CONTRACT,
    expand_challenge_payload,
    project_challenge_payload,
)
from yield_rca_core.llm_gateway import LLMCallError, load_prompt


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Projection tests must never make a network call")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.mark.parametrize("name,old_chars", [("pro", 67998), ("qwen", 43550)])
def test_frozen_payload_roundtrip_preserves_every_field(name, old_chars):
    payload = frozen(name)["payload"]
    before = encoded(payload)
    projected = project_challenge_payload(payload)
    assert encoded(payload) == before
    assert len(before) == old_chars
    assert encoded(expand_challenge_payload(projected)) == before
    assert len(encoded(projected)) < 64000
    assert projected["challenge_output_contract"] == payload["challenge_output_contract"]
    assert projected["typed_evidence_register"] == payload["typed_evidence_register"]
    if name == "pro":
        # Regression evidence, not a new production margin policy.
        assert 53000 <= len(encoded(projected)) < 55000
        assert len(payload["candidates"]) == 2


def test_production_generate_crosses_old_size_barrier_without_dropping_inputs():
    kwargs = inputs("pro")
    before = frozen("pro")["payload"]
    old_client = ResponseTape([])
    old = legacy().QwenAdversarialChallenger(old_client).generate(**kwargs)
    assert not old_client.requests and old.output_invalid
    assert old.prompt_audit["prompt_payload_char_count"] == 67998
    # Deliberately invalid output: prove transport is reached without claiming RCA success.
    client = ResponseTape([{}, {}])
    new = current.QwenAdversarialChallenger(client).generate(**kwargs)
    assert len(client.requests) == 2
    assert expand_challenge_payload(client.requests[0].payload) == before
    assert new.output_invalid  # Existing validator still rejects an empty object.
    for request in client.requests:
        assert len(encoded(request.payload)) <= 64000
    audit = new.prompt_audit
    assert audit["payload_chars"] == audit["prompt_payload_char_count"]
    assert audit["remaining_chars"] == 64000 - audit["payload_chars"]
    assert audit["system_prompt_chars"] == len(load_prompt("causal_adversarial_challenge", "v1"))
    assert audit["combined_request_chars"] == audit["payload_chars"] + audit["system_prompt_chars"]


@pytest.mark.parametrize("mode", ["success", "repair", "invalid", "provider_failure"])
def test_same_response_tape_has_equal_semantics_and_validator_inputs(mode, monkeypatch):
    kwargs = inputs("qwen")
    valid = frozen("qwen")["validated_response"]
    responses = {
        "success": [valid],
        "repair": [{}, valid],
        "invalid": [{}, {}],
        "provider_failure": [LLMCallError("mock transport failure", failure_category="timeout")],
    }[mode]
    old_module = legacy()
    traces = [[], []]
    original_old = old_module._normalize_challenge_payload
    original_new = current._normalize_challenge_payload

    def observe(original, output):
        def wrapped(payload, **arguments):
            output.append((copy.deepcopy(payload), arguments))
            return original(payload, **arguments)

        return wrapped

    monkeypatch.setattr(
        old_module, "_normalize_challenge_payload", observe(original_old, traces[0])
    )
    monkeypatch.setattr(current, "_normalize_challenge_payload", observe(original_new, traces[1]))
    old_client, new_client = ResponseTape(responses), ResponseTape(responses)
    old = old_module.QwenAdversarialChallenger(old_client).generate(**kwargs)
    new = current.QwenAdversarialChallenger(new_client).generate(**kwargs)
    old_result, new_result = asdict(old), asdict(new)
    old_result.pop("prompt_audit")
    new_result.pop("prompt_audit")
    assert new_result == old_result
    assert len(new_client.requests) == len(old_client.requests)
    for a, b in zip(old_client.requests, new_client.requests, strict=True):
        assert expand_challenge_payload(b.payload) == a.payload
    assert traces[0] == traces[1]


def test_multiple_lanes_gaps_profiles_reasons_and_evidence_sets_are_lossless():
    payload = frozen("pro")["payload"]
    # Genuine repeated domain objects, including long Unicode values/statistics/units.
    payload["candidate_competition"]["reason"] = "共享物理证据，不是模型断言。" * 1000
    payload["evidence_gaps"][0]["reason"] = payload["candidate_competition"]["reason"]
    for candidate in payload["candidates"]:
        candidate["semantic_profile"]["extra_predictions"] = ["Long prediction " * 500]
    projected = project_challenge_payload(payload)
    assert expand_challenge_payload(projected) == payload
    assert len(projected["canonical_values"]) > 1
    assert len(payload["causal_lanes"]) > 1
    assert len(payload["evidence_gaps"]) > 1
    assert len(encoded(projected)) < len(encoded(payload))


@pytest.mark.parametrize("target", [63999, 64000, 64001, 80000])
def test_fixed_budget_uses_projected_payload_and_never_truncates(target):
    kwargs = inputs("qwen")
    kwargs["candidates"][0]["causal_explanation"] = ""
    capture = ResponseTape([{}, {}])
    current.QwenAdversarialChallenger(capture).generate(**kwargs)
    initial = capture.requests[0].payload
    padding = target - len(encoded(initial))
    kwargs["candidates"][0]["causal_explanation"] = "x" * padding
    client = ResponseTape([frozen("qwen")["validated_response"]])
    result = current.QwenAdversarialChallenger(client).generate(**kwargs)
    assert result.prompt_audit["payload_chars"] == target
    if target <= 64000:
        assert len(client.requests) == 1
        restored = expand_challenge_payload(client.requests[0].payload)
        assert restored["candidates"][0]["causal_explanation"] == "x" * padding
    else:
        assert not client.requests
        assert result.output_invalid
        assert result.attempt_count == 0  # No logical call passes the size gate.
        assert result.validation_errors == (
            "adversarial challenge prompt exceeds the governed payload limit",
        )


def test_repeatable_independent_of_mapping_insertion_order():
    payload = frozen("pro")["payload"]

    def reverse(value):
        if isinstance(value, dict):
            return {k: reverse(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse(v) for v in value]
        return value

    assert encoded(project_challenge_payload(payload)) == encoded(
        project_challenge_payload(reverse(payload))
    )


def test_literal_reference_like_data_and_typed_values_roundtrip():
    payload = {
        "candidates": [],
        "candidate_competition": None,
        "facts": [
            {"$ref": "/not/a/reference"},
            {"$literal": {"$ref": "literal"}},
            {"a/b~c": [1, 1.0, True, None, "μm", -0.0]},
        ],
        "reason": "重复值" * 100,
        "other": {"reason": "重复值" * 100},
    }
    assert encoded(expand_challenge_payload(project_challenge_payload(payload))) == encoded(payload)


@pytest.mark.parametrize(
    "ref",
    ["/missing", "/canonical_values/v0", "https://example.test", "/candidates/00", "/bad~2key"],
)
def test_invalid_or_cyclic_references_fail_locally(ref):
    payload = {
        "projection_contract": PROJECTION_CONTRACT,
        "canonical_values": {"v0": {"$ref": ref}},
        "candidates": [],
    }
    with pytest.raises(ValueError):
        expand_challenge_payload(payload)


@pytest.mark.parametrize("reserved", ["canonical_values", "projection_contract"])
def test_original_metadata_cannot_be_silently_overwritten(reserved):
    with pytest.raises(ValueError):
        project_challenge_payload({reserved: {"fact": 3.14}})


def test_plain_legacy_payload_remains_readable():
    payload = frozen("qwen")["payload"]
    assert expand_challenge_payload(payload) == payload


def test_semantic_profile_equality_does_not_conflate_boolean_and_number():
    candidate_profile = {"value": True, "mechanism": "plausible physical bridge " * 10}
    competition_profile = {**candidate_profile, "value": 1}
    payload = {
        "candidates": [{"semantic_profile": candidate_profile}],
        "candidate_competition": {
            "candidate_profiles": [{"semantic_profile": competition_profile}]
        },
    }
    assert encoded(expand_challenge_payload(project_challenge_payload(payload))) == encoded(payload)


def test_pro_repair_retains_the_original_contract_without_copying_it():
    client = ResponseTape([{}, {}])
    current.QwenAdversarialChallenger(client).generate(**inputs("pro"))
    assert len(client.requests) == 2
    first, second = (expand_challenge_payload(r.payload) for r in client.requests)
    assert second["output_attempt"] == 2
    feedback = second["previous_validation_feedback"]
    assert feedback["challenge_output_contract"] == first["challenge_output_contract"]
    for key in first:
        if key not in {"output_attempt", "previous_validation_feedback"}:
            assert second[key] == first[key]
    assert len(encoded(client.requests[1].payload)) < 64000
