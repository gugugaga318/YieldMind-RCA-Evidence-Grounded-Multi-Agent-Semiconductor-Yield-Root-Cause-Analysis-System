from __future__ import annotations

import copy
import json
import socket
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from challenge_diagnostics_support import before_observability
from challenge_projection_support import ResponseTape, frozen, inputs
from yield_rca_core import causal_adversarial as current
from yield_rca_core import challenge_diagnostics as diag
from yield_rca_core.challenge_projection import expand_challenge_payload, project_challenge_payload
from yield_rca_core.evidence_models import Evidence
from yield_rca_core.llm_gateway import LLMCallError, LLMOutputValidationError


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Diagnostic tests must never connect to a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def citation_fixture(kind):
    evidence = next(iter(inputs("qwen")["evidence_by_id"].values())).to_dict()
    evidence.update(
        evidence_id="EV_MATCH",
        timestamp="2026-01-02T00:00:00Z",
        metadata={"lane_id": "LANE_A"},
        entities=[{"entity_type": "chamber", "entity_id": "CH_A", "attributes": {}}],
    )
    lane = {
        "lane_id": "LANE_A",
        "chamber": "CH_A",
        "time_window": ["2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"],
    }
    payload = {
        "candidate_id": "CAND_A",
        "alternative_candidate_id": None,
        "evidence_probe_lane_id": "LANE_A",
        "challenge_kind": "mechanism",
        "mechanism_relation": "unknown",
        "supporting_evidence_ids": ["EV_MATCH"],
        "contradicting_evidence_ids": [],
        "unexplained_precursor_evidence_ids": [],
        "distinguishing_gap_ids": [],
        "distinguishing_questions": [],
        "challenge_explanation": "A grounded alternative probe.",
        "status": "resolved",
    }
    gaps = {}
    if kind == "entity_mismatch":
        evidence["entities"][0]["entity_id"] = "CH_OTHER"
    elif kind == "time_mismatch":
        evidence["timestamp"] = "2026-02-01T00:00:00Z"
    elif kind == "lane_mismatch":
        evidence["metadata"]["lane_id"] = "LANE_B"
    elif kind == "unknown_evidence_id":
        payload["supporting_evidence_ids"] = ["EV_UNKNOWN"]
    elif kind == "support_role_mismatch":
        payload["contradicting_evidence_ids"] = ["EV_MATCH"]
    elif kind == "missing_required_evidence":
        payload["supporting_evidence_ids"] = []
    elif kind == "malformed_challenge_relation":
        payload["mechanism_relation"] = "independent_alternative"
    elif kind == "scope_mismatch":
        payload["distinguishing_gap_ids"] = ["GAP_A"]
        gaps = {"GAP_A": {"candidate_id": "CAND_A", "target_scope": {"lane_id": "LANE_B"}}}
    elif kind == "other_validation_failure":
        payload["challenge_explanation"] = ""
    kwargs = dict(
        candidate_ids={"CAND_A"},
        lane_ids={"LANE_A", "LANE_B"},
        evidence_ids={"EV_MATCH"},
        gap_ids=set(gaps),
        gap_by_id=gaps,
        evidence_by_id={"EV_MATCH": Evidence.from_dict(evidence)},
        lane_contexts={"LANE_A": lane},
    )
    # Exercise explicit canonical input mapping. Correct/incorrect citations are
    # selected from its expanded facts, never transformed to force acceptance.
    semantic = {
        "candidates": [{"candidate_id": "CAND_A"}],
        "lanes": [lane],
        "reason": "shared canonical fact " * 30,
        "other": {"reason": "shared canonical fact " * 30},
        "evidence": [evidence],
    }
    projected = project_challenge_payload(semantic)
    assert projected["canonical_values"]
    assert expand_challenge_payload(projected) == semantic
    return payload, kwargs


def outcome(function, payload, kwargs):
    try:
        return ("accept", asdict(function(payload, **kwargs)))
    except LLMOutputValidationError as exc:
        return ("reject", str(exc))


@pytest.mark.parametrize(
    "kind",
    [
        "accepted",
        "unknown_evidence_id",
        "entity_mismatch",
        "time_mismatch",
        "lane_mismatch",
        "scope_mismatch",
        "support_role_mismatch",
        "missing_required_evidence",
        "malformed_challenge_relation",
        "other_validation_failure",
    ],
)
def test_original_validator_result_and_structured_rejection(kind):
    payload, kwargs = citation_fixture(kind)
    expected = outcome(before_observability()._normalize_challenge_payload, payload, kwargs)

    @diag.observe_generation
    def inspect(self, *, request_id):
        diag.begin_attempt(1)
        actual = outcome(current._normalize_challenge_payload, payload, kwargs)
        assert actual == expected
        return SimpleNamespace(attempt_count=0, output_invalid=actual[0] == "reject")

    with diag.capture_challenge_diagnostics() as capture:
        inspect(SimpleNamespace(llm_client=None), request_id="offline-validator")
    record = capture.records[0]["attempts"][0]["validations"][0]
    assert record["accepted"] == (kind == "accepted")
    if kind != "accepted":
        assert kind in record["rejection_codes"]
        assert record["rejection_detail"] == expected[1]
    if kind in ("accepted", "entity_mismatch", "time_mismatch", "lane_mismatch"):
        check = record["evidence_checks"][0]
        assert check["evidence_id"] == "EV_MATCH"
        assert check["expected_entity"]["chamber"] == "CH_A"
        assert check["actual_evidence_time_scope"]
        if kind != "accepted":
            assert check[kind.replace("_mismatch", "_match")] is False


@pytest.mark.parametrize(
    "mode", ["accept", "repair_accept", "repair_reject", "provider", "preflight"]
)
def test_attempt_outputs_are_separate_and_do_not_change_generation(mode):
    valid = frozen("qwen")["validated_response"]
    bad = copy.deepcopy(valid)
    bad["challenges"][0]["supporting_evidence_ids"] = ["EV_UNKNOWN"]
    responses = {
        "accept": [valid],
        "repair_accept": [bad, valid],
        "repair_reject": [bad, bad],
        "provider": [LLMCallError("offline timeout")],
        "preflight": [],
    }[mode]
    kwargs = inputs("qwen")
    if mode == "preflight":
        kwargs["candidates"][0]["causal_explanation"] = "x" * 80000
    old_client, new_client = ResponseTape(responses), ResponseTape(responses)
    old = before_observability().QwenAdversarialChallenger(old_client).generate(**kwargs)
    with diag.capture_challenge_diagnostics(include_response_json=True) as capture:
        new = current.QwenAdversarialChallenger(new_client).generate(**kwargs)
    assert asdict(old) == asdict(new)
    assert [asdict(r) for r in old_client.requests] == [asdict(r) for r in new_client.requests]
    record = capture.records[0]
    assert record["attempt_count"] == len(new_client.requests)
    attempts = record["attempts"]
    assert len(attempts) == (2 if mode.startswith("repair") else 1)
    if mode == "preflight":
        assert attempts[0]["outcome"] == "preflight_failure"
        assert not attempts[0]["logical_call_started"]
        assert "parsed_response_json" not in attempts[0]
    elif mode == "provider":
        assert attempts[0]["outcome"] == "provider_failure"
        assert "parsed_response_json" not in attempts[0]
    else:
        assert attempts[0]["parsed_response_json"]["challenges"]
        if mode.startswith("repair"):
            assert attempts[0]["validations"][0]["rejection_codes"] == ["unknown_evidence_id"]
            assert attempts[1]["attempt"] == 2 and attempts[1]["is_repair"]
            assert attempts[1]["validations"][0]["accepted"] == (mode == "repair_accept")
        assert attempts[-1]["logical_call_id_available"]
    assert capture.dropped_count == 0


def test_capture_default_off_and_sidecar_default_excludes_raw_json(tmp_path):
    payload, kwargs = citation_fixture("accepted")
    assert outcome(current._normalize_challenge_payload, payload, kwargs)[0] == "accept"
    with diag.challenge_diagnostic_artifact(tmp_path / "disabled.json"):
        pass
    assert not (tmp_path / "disabled.json").exists()
    with diag.capture_challenge_diagnostics() as capture:
        current.QwenAdversarialChallenger(
            ResponseTape([frozen("qwen")["validated_response"]])
        ).generate(**inputs("qwen"))
    assert "parsed_response_json" not in capture.records[0]["attempts"][0]
    assert capture.write(tmp_path / "sidecar.json")
    document = json.loads((tmp_path / "sidecar.json").read_text(encoding="utf-8"))
    assert document["kind"] == "challenge_diagnostics"
    assert "prompt" not in document and "state" not in document


def test_redaction_bounds_and_non_interference(tmp_path, monkeypatch):
    secret = "credential_without_standard_prefix"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    value = {
        "Authorization": "Bearer auth-secret-123456789",
        "nested": {"api_key": secret, "PASSWORD": "hidden"},
        "reason": secret + " Bearer another-secret-12345",
        "oversized": "z" * 9000,
        "many": list(range(100)),
    }
    with diag.capture_challenge_diagnostics(include_response_json=True) as capture:
        cleaned = capture.clean(value)
        capture.retain({"generation": 1, "value": cleaned})
    assert capture.write(tmp_path / "redacted.json")
    text = (tmp_path / "redacted.json").read_text(encoding="utf-8")
    assert secret not in text and "auth-secret" not in text and "another-secret" not in text
    assert "hidden" not in text
    assert "[REDACTED]" in text and capture.truncation_count > 0
    assert len(text.encode()) < diag.MAX_TOTAL_BYTES

    valid = frozen("qwen")["validated_response"]
    with diag.capture_challenge_diagnostics(max_generations=0) as dropped:
        result = current.QwenAdversarialChallenger(ResponseTape([valid])).generate(**inputs("qwen"))
    assert result.challenges and dropped.dropped_count == 1 and dropped.records == []
    # Observation failure cannot alter the result, including broken sanitizer.
    with diag.capture_challenge_diagnostics() as broken:
        monkeypatch.setattr(
            broken, "clean", lambda *a, **k: (_ for _ in ()).throw(ValueError("capture"))
        )
        result = current.QwenAdversarialChallenger(ResponseTape([valid])).generate(**inputs("qwen"))
    assert result.challenges and broken.dropped_count > 0


def test_artifact_write_failure_cannot_replace_workflow_result(tmp_path):
    path = tmp_path / "file"
    path.write_text("existing", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="could not be saved"):
        with diag.challenge_diagnostic_artifact(path / "bad.json", enabled=True):
            observed = 42
    assert observed == 42


@pytest.mark.parametrize("status", [200, 503])
def test_runtime_call_correlation_without_duplicate_events(status, monkeypatch, tmp_path):
    import httpx
    from yield_rca_core.llm_gateway import LLMSettings, build_llm_client
    from yield_rca_core.model_telemetry import capture_model_telemetry

    secret = "configured-key-without-prefix"
    client = build_llm_client(
        LLMSettings(
            agent_mode="llm",
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key=secret,
            max_retries=0,
        )
    )
    bad = copy.deepcopy(frozen("qwen")["validated_response"])
    bad["challenges"][0]["supporting_evidence_ids"] = ["EV_UNKNOWN"]
    bad["challenges"][0]["challenge_explanation"] = secret
    bad["analysis_summary"] = "Bearer leaked-test-token"
    wire_calls = []

    def post(self, url, **kwargs):
        wire_calls.append(1)
        return httpx.Response(
            status,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": json.dumps(bad)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    with capture_model_telemetry() as facts:
        with diag.capture_challenge_diagnostics(include_response_json=True) as capture:
            result = current.QwenAdversarialChallenger(client).generate(**inputs("qwen"))
    expected_calls = 2 if status == 200 else 1
    assert result.attempt_count == len(wire_calls) == expected_calls
    logical = [e for e in facts.events if e.kind == "logical_call"]
    assert len(logical) == expected_calls
    assert len([e for e in facts.events if e.kind == "http_attempt"]) == expected_calls
    attempts = capture.records[0]["attempts"]
    assert [a["logical_call_id"] for a in attempts] == [e.logical_call_id for e in logical]
    assert all(a["logical_call_id_available"] for a in attempts)
    assert capture.records[0]["provider"] == "deepseek"
    assert capture.write(tmp_path / "runtime.json")
    saved = (tmp_path / "runtime.json").read_text(encoding="utf-8")
    assert secret not in saved and "leaked-test-token" not in saved
    if status == 200:
        assert all(a["rejection_codes"] == ["unknown_evidence_id"] for a in attempts)
        assert all(a["parsed_response_json"]["challenges"] for a in attempts)
        assert "[REDACTED]" in saved


def test_capture_limits_are_explicit_and_nested_scopes_are_isolated(tmp_path):
    @diag.observe_generation
    def oversized(self, *, request_id):
        diag.begin_attempt(1)
        diag.response_received(
            SimpleNamespace(
                usage=SimpleNamespace(call_id="bounded"),
                data={f"field-{i}": "x" * 1000 for i in range(40)},
            )
        )
        return SimpleNamespace(attempt_count=1, output_invalid=True)

    with diag.capture_challenge_diagnostics(include_response_json=True) as outer:
        with diag.capture_challenge_diagnostics(include_response_json=True) as inner:
            oversized(SimpleNamespace(llm_client=None), request_id="large")
        assert not outer.records
        assert inner.records[0]["attempts"][0]["parsed_response_json"]["omitted"] == (
            "response_size_limit"
        )
        assert inner.truncation_count > 0
        outer.retain({"generation": 1, "rows": ["x" * 1000] * 60})
    assert inner.write(tmp_path / "limited.json")
    assert not json.loads((tmp_path / "limited.json").read_text())["complete"]
    # A large generation is replaced explicitly, never silently persisted unbounded.
    single = diag.ChallengeCapture()
    single.retain({"rows": [["x" * 1000] * 40] * 3})
    assert single.records[0]["omitted"] == "generation_size_limit"
    assert single.dropped_count == 1
    for _ in range(20):
        outer.retain({"rows": ["x" * 1000] * 60})
    assert outer.bytes_retained <= diag.MAX_TOTAL_BYTES
    assert outer.dropped_count > 0
    assert all(len(diag._encoded(r)) <= diag.MAX_GENERATION_BYTES for r in outer.records)
    # Context cleanup: no active capture after exit, no further record created.
    before = len(inner.records)
    oversized(SimpleNamespace(llm_client=None), request_id="disabled")
    assert len(inner.records) == before


def test_evidence_check_overflow_is_marked_and_alias_precedence_is_exact():
    payload, kwargs = citation_fixture("entity_mismatch")
    payload["strongest_alternative_lane_id"] = "LANE_B"

    @diag.observe_generation
    def inspect(self, *, request_id):
        diag.begin_attempt(1)
        outcome(current._normalize_challenge_payload, payload, kwargs)
        return SimpleNamespace(attempt_count=0)

    with diag.capture_challenge_diagnostics() as capture:
        inspect(SimpleNamespace(llm_client=None), request_id="alias")
    record = capture.records[0]["attempts"][0]["validations"][0]
    assert record["alternative_lane_id"] == "LANE_A"
    assert record["expected_lane_context"]["chamber"] == "CH_A"
    payload["evidence_probe_lane_id"] = ""
    with diag.capture_challenge_diagnostics() as capture:
        inspect(SimpleNamespace(llm_client=None), request_id="empty-alias")
    assert capture.records[0]["attempts"][0]["validations"][0]["alternative_lane_id"] is None
    with diag.capture_challenge_diagnostics() as capture:
        token = diag._VALIDATION.set({"evidence_checks": [{}] * 64})
        try:
            diag.lane_check(
                None,
                {},
                matched=False,
                code="lane_mismatch",
                lane_match=False,
                entity_match=None,
                time_match=None,
            )
        finally:
            diag._VALIDATION.reset(token)
    assert capture.truncation_count == 1


def test_runner_flags_persist_sidecar_on_workflow_exception_without_changing_report(
    tmp_path, monkeypatch
):
    import run_formal_blind_rca as runner
    from yield_rca_core.llm_gateway import LLMSettings

    monkeypatch.setattr(runner.LLMSettings, "from_env", lambda: LLMSettings())
    monkeypatch.setattr(
        runner,
        "load_public_cases",
        lambda path: ("offline-diagnostic", [runner.PublicCase("OFFLINE", "LOT", "test")]),
    )
    monkeypatch.setattr(runner, "_code_snapshot", lambda: ("frozen", True))

    class Workflow:
        def run(self, *args, **kwargs):
            bad = copy.deepcopy(frozen("qwen")["validated_response"])
            bad["challenges"][0]["supporting_evidence_ids"] = ["EV_UNKNOWN"]
            current.QwenAdversarialChallenger(ResponseTape([bad, bad])).generate(**inputs("qwen"))
            raise RuntimeError("offline workflow failure after Challenge")

    monkeypatch.setattr(runner, "build_csv_workflow", lambda *a, **k: Workflow())
    results = []
    for enabled in (False, True):
        directory = tmp_path / str(enabled)
        argv = ["--public-dir", str(tmp_path / "public"), "--output-dir", str(directory)]
        if enabled:
            argv += ["--challenge-diagnostics", "--challenge-response-json"]
        result = runner.run_formal_blind(runner.build_parser().parse_args(argv))
        results.append(result)
        path = directory / "diagnostics" / "OFFLINE.challenge.json"
        assert path.exists() == enabled
        if enabled:
            attempts = json.loads(path.read_text(encoding="utf-8"))["records"][0]["attempts"]
            assert len(attempts) == 2
            assert all(a["parsed_response_json"] for a in attempts)
            assert all(a["rejection_codes"] == ["unknown_evidence_id"] for a in attempts)
    assert results[0] == results[1]
