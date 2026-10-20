"""Batch 4 real adapter wire tests; all transport is mocked, never paid."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.model_contracts import (
    LLMCallError,
    LLMConfigurationError,
    LLMOutputValidationError,
    ModelMessage,
    ModelRequest,
)
from yield_rca_core.model_runtime import ModelRuntime
from yield_rca_core.model_telemetry import capture_model_telemetry
from yield_rca_core.providers.deepseek import DeepSeekAdapter
from yield_rca_core.result_identity import configured_identity

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_formal_blind_rca as runner  # noqa: E402
from model_contract_acceptance import model_contract_acceptance_reasons  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "deepseek_identity_fixtures", ROOT / "tests/contract/test_result_identity_contracts.py"
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
REQUEST = ModelRequest(
    "deepseek",
    "deepseek-flash",
    (ModelMessage("system", "Return JSON."), ModelMessage("user", "test")),
    temperature=0.25,
    logical_call_id="one",
    stage="planner",
    agent="planner",
    prompt_version="v1",
)
KEY = "offline-secret-value-only"


@pytest.fixture(params=["deepseek-flash", "deepseek-v4-pro"])
def model_request(request):
    return replace(REQUEST, model=request.param)


def envelope(content='{"ok":true}', **changes):
    return {
        "model": "reported-alias",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "prompt_cache_hit_tokens": 3,
        },
        **changes,
    }


def response(status=200, payload=None):
    return httpx.Response(
        status,
        json=envelope() if payload is None else payload,
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
    )


def settings(*, model="deepseek-flash", **kwargs):
    return gateway.LLMSettings(
        agent_mode="llm",
        provider="deepseek",
        model=model,
        base_url="https://api.deepseek.com",
        api_key=KEY,
        **kwargs,
    )


def test_wire_identity_usage_and_no_sdk_retry(model_request):
    client = gateway.build_llm_client(
        settings(model=model_request.model, timeout_seconds=120, max_retries=1)
    )
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.return_value = response()
        result = client.runtime.complete_json(model_request)
    factory.assert_called_once_with(timeout=120)
    post.assert_called_once()
    assert post.call_args.args == ("https://api.deepseek.com/chat/completions",)
    body = post.call_args.kwargs["json"]
    assert body == {
        "model": model_request.model,
        "messages": [asdict(m) for m in model_request.messages],
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": 8192,
    }
    assert post.call_args.kwargs["headers"] == {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
    }
    assert result.data == {"ok": True}
    assert result.usage.cached_tokens == 3 and result.usage.total_tokens == 12
    assert result.identity == configured_identity(client)
    assert result.reported_model == "reported-alias" and result.stop_reason == "stop"
    assert [e.kind for e in facts.events] == ["logical_call", "http_attempt", "model_result"]
    assert all((e.provider, e.model) == ("deepseek", model_request.model) for e in facts.events)
    assert KEY not in repr(facts.events) and KEY not in repr(settings())


@pytest.mark.parametrize(
    "status,category,attempts",
    [
        (401, "authentication", 1),
        (403, "authentication", 1),
        (402, "billing", 1),
        (429, "rate_limit", 2),
        (500, "unavailable", 2),
        (503, "unavailable", 2),
        (408, "provider_http_error", 2),
        (422, "provider_http_error", 1),
        (400, "context_overflow", 1),
        (413, "context_overflow", 1),
        (302, "provider_http_error", 1),
    ],
)
def test_http_errors_retry_and_redaction(status, category, attempts, model_request):
    payload = {
        "error": {
            "code": "context_length_exceeded" if status == 400 else "error",
            "message": f"Authorization: Bearer {KEY}; api_key={KEY}",
        }
    }
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.return_value = response(status, payload)
        with pytest.raises(LLMCallError) as caught:
            ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
    exc = caught.value
    assert (exc.status_code, exc.failure_category, exc.call_attempt_count) == (
        status,
        category,
        attempts,
    )
    assert post.call_count == attempts
    assert KEY not in repr(vars(exc)) + repr(facts.events)
    assert sum(e.kind == "logical_call" for e in facts.events) == 1
    assert sum(e.kind == "http_attempt" for e in facts.events) == attempts


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
@pytest.mark.parametrize("retries", [0, 1, 2])
def test_transport_retry_preserves_caller_transient_semantics(error, retries, model_request):
    from yield_rca_core.next_action_planner import _is_retryable_call_error

    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.side_effect = error(KEY)
        with pytest.raises(LLMCallError) as caught:
            ModelRuntime(DeepSeekAdapter(api_key=KEY, max_retries=retries)).complete_json(
                model_request
            )
    assert post.call_count == retries + 1
    assert caught.value.failure_category == "transport_error"
    assert _is_retryable_call_error(caught.value)
    assert caught.value.__context__ is None
    assert KEY not in repr(vars(caught.value)) + repr(facts.events)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"choices": []},
        envelope(usage=None),
        envelope(usage={"prompt_tokens": -1}),
        envelope(choices=[{"message": {"content": None}}]),
        envelope(usage={"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}),
    ],
)
def test_invalid_envelope_never_http_retried(payload, model_request):
    with patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.return_value = response(payload=payload)
        with pytest.raises(LLMOutputValidationError, match="envelope"):
            ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
        assert post.call_count == 1


@pytest.mark.parametrize("content", ["", "not-json", "[]", '{"truncated":'])
def test_invalid_json_owned_by_runtime_no_transport_retry(content, model_request):
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.return_value = response(payload=envelope(content))
        with pytest.raises(LLMOutputValidationError):
            ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
        assert post.call_count == 1
    assert facts.events[-1].error_category == "invalid_json"


@pytest.mark.parametrize(
    "changes",
    [
        {"output_modes": frozenset()},
        {"message_roles": frozenset({"user"})},
        {"temperature_range": (0.1, 1)},
        {"usage_available": False},
        {"context_window_tokens": 1000},
    ],
)
def test_startup_capability_gate_before_network(changes):
    with patch.object(
        DeepSeekAdapter, "capabilities", replace(DeepSeekAdapter.capabilities, **changes)
    ):
        with patch.object(httpx, "Client") as factory, pytest.raises(LLMConfigurationError):
            gateway.build_llm_client(settings())
        factory.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "deepseek-chat"},
        {"provider": "qwen"},
        {"temperature": float("nan")},
        {"temperature": 3},
        {"messages": (ModelMessage("system", "no instruction"),)},
    ],
)
def test_request_preflight(changes, model_request):
    with patch.object(httpx, "Client") as factory, pytest.raises(LLMConfigurationError):
        ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(replace(model_request, **changes))
    factory.assert_not_called()


def test_context_overflow_local_and_http_attempts_are_separate(model_request):
    adapter = DeepSeekAdapter(api_key=KEY)
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        with pytest.raises(LLMCallError) as caught:
            ModelRuntime(adapter).complete_json(
                replace(model_request, messages=(ModelMessage("system", "JSON" + "x" * 1_000_000),))
            )
    factory.assert_not_called()
    assert caught.value.failure_category == "context_overflow"
    assert caught.value.call_attempt_count == 0
    assert not any(e.kind == "http_attempt" for e in facts.events)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.com",
        "https://example.test",
        "https://api.deepseek.com.evil.test",
        "https://api.deepseek.com?key=secret",
        "https://user:secret@api.deepseek.com",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_endpoint_cannot_silently_send_wrong_provider_key(url):
    with pytest.raises(LLMConfigurationError):
        DeepSeekAdapter(api_key=KEY, base_url=url)


def test_config_separate_keys_and_qwen_alias():
    with patch.dict(
        os.environ,
        {
            "YIELD_RCA_AGENT_MODE": "llm",
            "YIELD_RCA_LLM_PROVIDER": "deepseek",
            "DASHSCOPE_API_KEY": "wrong-provider",
        },
        clear=True,
    ):
        with pytest.raises(LLMConfigurationError, match="DEEPSEEK_API_KEY"):
            gateway.LLMSettings.from_env()
        os.environ["DEEPSEEK_API_KEY"] = KEY
        cfg = gateway.LLMSettings.from_env()
        assert cfg.api_key == KEY and cfg.model == "deepseek-flash"
        assert cfg.base_url == "https://api.deepseek.com"
    for provider in ("qwen", "dashscope"):
        cfg = gateway.LLMSettings(agent_mode="llm", provider=provider, api_key="offline")
        assert gateway.build_llm_client(cfg).provider == "dashscope"
    with pytest.raises(LLMConfigurationError, match="unsupported"):
        gateway.build_llm_client(replace(cfg, provider="other"))


@pytest.fixture(scope="module", params=["deepseek-flash", "deepseek-v4-pro"])
def configured_workflow(request):
    client = gateway.build_llm_client(settings(model=request.param))
    fake = gateway.FakeLLMClient()
    original = client.complete_json
    calls = []

    def complete(request):
        with patch.object(gateway, "_record_usage", lambda event: None):
            generated = fake.complete_json(request)
        with patch.object(httpx, "Client") as factory:
            post = factory.return_value.__enter__.return_value.post
            post.return_value = response(payload=envelope(json.dumps(generated.data)))
            result = original(request)
            calls.append(post.call_args.kwargs["json"])
            return result

    with patch.object(client, "complete_json", complete):
        workflow = fixtures.workflow.build_csv_workflow(
            fixtures.workflow.SEED_DIR,
            llm_settings=settings(model=request.param),
            llm_client=client,
            orchestration_mode="llm_react",
        )
        state = workflow.run(
            fixtures.workflow.ROOT_CAUSE_QUERY, job_id="DEEPSEEK_OFFLINE", lot_id="LOT_A_001"
        )
    metadata = state.execution_metadata
    result = {
        "job_status": state.job.status,
        "actual_orchestration_mode": "llm_react",
        "hypothesis_candidate_source": "model",
        "competition_lifecycle_valid": True,
        "investigation_decision_accepted": True,
        "terminal_stop_reason": state.stop_reason,
        **{
            k: metadata.get(k)
            for k in (
                "planner_stop_proposed_by",
                "terminal_stop_projected_by",
                "terminal_question_updates_source",
            )
        },
    }
    return client, state, result, calls


def audit(state, result, client):
    return model_contract_acceptance_reasons(
        result,
        state=state,
        identity=configured_identity(client),
        requested_mode="llm_react",
        agent_mode="llm",
        python_stop_is_governed=runner._python_terminal_stop_is_governed,
    )


def test_full_configured_adapter_workflow_and_neutral_acceptance(configured_workflow):
    client, state, result, calls = configured_workflow
    assert state.job.status == "completed" and calls
    assert audit(state, result, client) == []
    assert "hypothesis_candidate_not_qwen" in runner._strict_qwen_acceptance_reasons(
        result, requested_mode="llm_react", agent_mode="llm"
    )
    assert state.execution_metadata["model_identity"]["provider"] == "deepseek"
    assert state.execution_metadata["model_identity"]["model"] == client.model
    assert all(c["model"] == client.model for c in calls)
    assert all(c["thinking"] == {"type": "disabled"} for c in calls)
    assert state.authoritative_rca_result.conclusion_status == "inconclusive"
    assert all("json" in c["messages"][0]["content"].lower() for c in calls)
    assert KEY not in json.dumps(state.to_dict())


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("hypothesis_candidate_source", "qwen", "hypothesis_candidate_not_model_authored"),
        ("planner_stop_proposed_by", "model_fake", "planner_stop_source_invalid"),
        ("terminal_question_updates_source", "model", "terminal_updates_not_python_evidence_gate"),
        ("terminal_stop_projected_by", "model", "terminal_stop_projection_source_invalid"),
        ("competition_lifecycle_valid", False, "competition_lifecycle_invalid"),
        ("provider_failure", True, "provider_failure"),
        ("fallback_reason", "fallback", "orchestration_fallback_reason_present"),
    ],
)
def test_neutral_governance_rejects_bad_reports(configured_workflow, field, value, reason):
    client, state, result, _ = configured_workflow
    assert reason in audit(state, {**result, field: value}, client)


def test_neutral_identity_and_telemetry_are_required(configured_workflow):
    client, state, result, _ = configured_workflow
    metadata = {
        **state.execution_metadata,
        "model_identity": {
            "author": "model",
            "provider": "qwen",
            "model": "forged",
            "basis": "runtime_config",
        },
    }
    assert "state_identity_mismatch" in audit(
        replace(state, execution_metadata=metadata), result, client
    )
    metadata = {**state.execution_metadata, "model_telemetry": {}}
    assert "model_telemetry_incomplete" in audit(
        replace(state, execution_metadata=metadata), result, client
    )


def test_runner_paid_and_contract_flags_are_separate(tmp_path):
    parser = runner.build_parser()
    args = parser.parse_args(["--public-dir", str(tmp_path), "--orchestration-mode", "llm_react"])
    case = runner.PublicCase("OFFLINE", "LOT_A_001", "query")
    with (
        patch.object(runner, "load_public_cases", return_value=("fixture", [case])),
        patch.object(runner.LLMSettings, "from_env", return_value=settings()),
    ):
        with pytest.raises(ValueError, match="non-Qwen"):
            runner.run_formal_blind(args)
        args.acceptance_contract = "model_contract"
        args.confirm_paid_qwen = True
        with pytest.raises(ValueError, match="confirm-paid-model"):
            runner.run_formal_blind(args)


@pytest.mark.parametrize(
    "stop", ["stop", "length", "content_filter", "insufficient_system_resource", "aborted"]
)
def test_stop_reason_preserved_without_hidden_completion_retry(stop, model_request):
    payload = envelope()
    payload["choices"][0]["finish_reason"] = stop
    with patch.object(httpx, "Client") as factory:
        post = factory.return_value.__enter__.return_value.post
        post.return_value = response(payload=payload)
        result = ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
    assert result.stop_reason == stop and post.call_count == 1


def test_cache_usage_precedence_and_forged_report_identity(model_request):
    payload = envelope('{"provider":"qwen","model":"forged"}')
    payload["usage"]["prompt_tokens_details"] = {"cached_tokens": 4}
    payload["usage"]["completion_tokens_details"] = {"reasoning_tokens": 1}
    payload["model"] = KEY
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        factory.return_value.__enter__.return_value.post.return_value = response(payload=payload)
        result = ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
    assert result.usage.cached_tokens == 4 and result.usage.reasoning_tokens == 1
    assert result.identity.provider == "deepseek" and result.identity.model == model_request.model
    assert KEY not in repr(facts.events)


def test_429_then_success_has_one_logical_call_two_http_attempts(model_request):
    with capture_model_telemetry() as facts, patch.object(httpx, "Client") as factory:
        factory.return_value.__enter__.return_value.post.side_effect = [response(429), response()]
        ModelRuntime(DeepSeekAdapter(api_key=KEY)).complete_json(model_request)
    assert [e.http_attempt for e in facts.events if e.kind == "http_attempt"] == [1, 2]
    assert sum(e.kind == "logical_call" for e in facts.events) == 1
    assert facts.events[-1].outcome == "success"


@pytest.mark.parametrize("telemetry", [None, {"events": None}, {"events": [None]}])
def test_neutral_rejects_malformed_fact_stream(configured_workflow, telemetry):
    client, state, result, _ = configured_workflow
    changed = replace(
        state, execution_metadata={**state.execution_metadata, "model_telemetry": telemetry}
    )
    assert "model_telemetry_incomplete" in audit(changed, result, client)


def test_neutral_rejects_missing_or_forged_response_identity(configured_workflow):
    client, state, result, _ = configured_workflow
    import copy

    for mutation, reason in (
        ("identity", "telemetry_identity_mismatch"),
        ("result", "model_response_ledger_invalid"),
        ("attempt", "http_attempt_ledger_invalid"),
    ):
        metadata = copy.deepcopy(state.execution_metadata)
        events = metadata["model_telemetry"]["events"]
        if mutation == "identity":
            events[0]["provider"] = "qwen"
        elif mutation == "result":
            events[:] = [e for e in events if e["kind"] != "model_result"]
        else:
            events[:] = [e for e in events if e["kind"] != "http_attempt"]
        assert reason in audit(replace(state, execution_metadata=metadata), result, client)


def test_formal_neutral_runner_output_never_claims_strict_qwen(tmp_path, configured_workflow):
    from types import SimpleNamespace

    client, state, _, _ = configured_workflow
    args = runner.build_parser().parse_args(
        [
            "--public-dir",
            str(tmp_path / "public"),
            "--output-dir",
            str(tmp_path / "run"),
            "--orchestration-mode",
            "llm_react",
            "--acceptance-contract",
            "model_contract",
            "--confirm-paid-model",
        ]
    )
    case = runner.PublicCase("OFFLINE", "LOT_A_001", "query")
    with (
        patch.object(runner, "load_public_cases", return_value=("fixture", [case])),
        patch.object(runner.LLMSettings, "from_env", return_value=settings(model=client.model)),
        patch.object(runner, "build_llm_client", return_value=client),
        patch.object(
            runner, "build_csv_workflow", return_value=SimpleNamespace(run=lambda *a, **k: state)
        ),
        patch.object(runner, "_strict_qwen_acceptance_reasons") as strict,
    ):
        report = runner.run_formal_blind(args)
    strict.assert_not_called()
    assert report["model_contract_accepted_case_count"] == 1, report["results"]
    assert report["strict_qwen_acceptance_evaluated"] is False
    assert report["strict_qwen_rejected_case_count"] == 0
    assert report["results"][0]["strict_qwen_accepted"] is None
    assert report["results"][0]["model_contract_rejection_reasons"] == []
    assert KEY not in (tmp_path / "run" / "run_manifest.json").read_text()


def test_persistent_deepseek_429_preserves_planner_two_logical_four_http(model_request):
    client = gateway.build_llm_client(settings(model=model_request.model))
    fake = gateway.FakeLLMClient()
    original = client.complete_json

    def complete(request):
        with patch.object(gateway, "_record_usage", lambda event: None):
            generated = fake.complete_json(request)
        with patch.object(httpx, "Client") as factory:
            factory.return_value.__enter__.return_value.post.return_value = (
                response(429)
                if request.prompt_name == "next_action_planner"
                else response(payload=envelope(json.dumps(generated.data)))
            )
            return original(request)

    with capture_model_telemetry() as facts, patch.object(client, "complete_json", complete):
        workflow = fixtures.workflow.build_csv_workflow(
            fixtures.workflow.SEED_DIR,
            llm_settings=settings(model=model_request.model),
            llm_client=client,
            orchestration_mode="llm_react",
        )
        state = workflow.run(
            fixtures.workflow.ROOT_CAUSE_QUERY, job_id="DEEPSEEK_429", lot_id="LOT_A_001"
        )
    planner_events = [e for e in facts.events if e.stage == "next_action_planner"]
    assert sum(e.kind == "logical_call" for e in planner_events) == 2
    assert sum(e.kind == "http_attempt" for e in planner_events) == 4
    assert state.execution_metadata.get("orchestration_fallback_reason")


def test_neutral_rejects_ungoverned_python_stop(configured_workflow):
    client, state, result, _ = configured_workflow
    bad = {**result, "planner_stop_proposed_by": "python_runtime", "terminal_stop_reason": "forged"}
    assert "python_runtime_stop_not_governed" in audit(state, bad, client)


def test_no_business_prompt_needs_a_deepseek_hint():
    paths = list((ROOT / "core/yield_rca_core/prompts").glob("*.md"))
    assert paths
    assert all("json" in p.read_text(encoding="utf-8").lower() for p in paths)


@pytest.mark.parametrize("mutation", ["unhashable", "orphan", "missing_outcome"])
def test_neutral_rejects_inconsistent_call_ledger(configured_workflow, mutation):
    import copy

    client, state, result, _ = configured_workflow
    metadata = copy.deepcopy(state.execution_metadata)
    events = metadata["model_telemetry"]["events"]
    if mutation == "unhashable":
        events[0]["logical_call_id"] = []
    elif mutation == "orphan":
        events.append(
            {**next(e for e in events if e["kind"] == "http_attempt"), "logical_call_id": "orphan"}
        )
    else:
        next(e for e in events if e["kind"] == "model_result").pop("outcome")
    assert "model_response_ledger_invalid" in audit(
        replace(state, execution_metadata=metadata), result, client
    )


def test_explicit_pro_environment_selection_preserves_default():
    with (
        patch.dict(
            os.environ,
            {
                "YIELD_RCA_AGENT_MODE": "llm",
                "YIELD_RCA_LLM_PROVIDER": "deepseek",
                "YIELD_RCA_LLM_MODEL": "deepseek-v4-pro",
                "DEEPSEEK_API_KEY": KEY,
            },
            clear=True,
        ),
        patch.object(httpx, "Client") as transport,
    ):
        cfg = gateway.LLMSettings.from_env()
        client = gateway.build_llm_client(cfg)
        assert configured_identity(client).model == "deepseek-v4-pro"
        assert configured_identity(client).provider == "deepseek"
        transport.assert_not_called()
        del os.environ["YIELD_RCA_LLM_MODEL"]
        assert gateway.LLMSettings.from_env().model == "deepseek-flash"
