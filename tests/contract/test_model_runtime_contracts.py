"""Model boundary tests; no network, no business validators moved into Runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from unittest.mock import Mock, patch

import httpx
import pytest
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.model_contracts import (
    LLMCallError,
    LLMConfigurationError,
    LLMOutputValidationError,
    ModelCapabilities,
    ModelMessage,
    ModelOutput,
    ModelRequest,
    ModelUsage,
)
from yield_rca_core.model_runtime import ModelRuntime
from yield_rca_core.model_telemetry import capture_model_telemetry, emit_model_event
from yield_rca_core.providers.qwen import QwenAdapter

REQUEST = ModelRequest(
    "dashscope",
    "qwen-plus",
    (
        ModelMessage("system", "Respond with JSON."),
        ModelMessage("user", "量测"),
    ),
    temperature=0.25,
    logical_call_id="call-1",
)


def adapter(output=None):
    result = Mock(provider="dashscope", capabilities=ModelCapabilities())
    result.complete.return_value = output or ModelOutput(
        '{"candidates":[{"bad":true},{"valid":true}]}', ModelUsage(10, 2, 1), "stop", "qwen-plus"
    )
    return result


def test_runtime_forwards_request_and_normalizes_usage_without_business_validation():
    impl = adapter()
    seen = []
    result = ModelRuntime(impl).complete_json(REQUEST, on_usage=seen.append)
    impl.complete.assert_called_once_with(REQUEST)
    assert result.data == {"candidates": [{"bad": True}, {"valid": True}]}
    assert result.usage.total_tokens == 12
    assert seen == [result.usage]
    assert result.stop_reason == "stop" and result.reported_model == "qwen-plus"


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "not-configured"},
        {"output_mode": "native_schema"},
        {"messages": (ModelMessage("tool", "x"),)},
    ],
)
def test_capability_and_adapter_identity_rejected_before_transport(change):
    impl = adapter()
    with pytest.raises(LLMConfigurationError):
        ModelRuntime(impl).complete_json(replace(REQUEST, **change))
    impl.complete.assert_not_called()


def test_adapter_without_json_capability_is_rejected():
    impl = adapter()
    impl.capabilities = ModelCapabilities(output_modes=frozenset())
    with pytest.raises(LLMConfigurationError):
        ModelRuntime(impl).complete_json(REQUEST)
    impl.complete.assert_not_called()


@pytest.mark.parametrize(
    "content, expected",
    [
        ('{"ok":true}', {"ok": True}),
        ('```json\n{"ok":true}\n```', {"ok": True}),
        ("[]", None),
        ("null", None),
        ("not-json", None),
    ],
)
def test_runtime_json_decode_and_legacy_usage_order(content, expected):
    impl = adapter(ModelOutput(content, ModelUsage(1, 2, 3)))
    seen = []
    runtime = ModelRuntime(impl)
    if expected is None:
        with pytest.raises(LLMOutputValidationError):
            runtime.complete_json(REQUEST, on_usage=seen.append)
    else:
        assert runtime.complete_json(REQUEST, on_usage=seen.append).data == expected
    assert len(seen) == impl.complete.call_count == 1
    assert seen[0].total_tokens == 3


@pytest.mark.parametrize(
    "error",
    [
        LLMCallError("timeout", failure_category="transport_error", call_attempt_count=2),
        LLMCallError("rate limit", status_code=429, failure_category="provider_http_error"),
        LLMOutputValidationError("model response envelope is invalid"),
    ],
)
def test_runtime_preserves_exception_instance_and_never_retries(error):
    impl = adapter()
    impl.complete.side_effect = error
    seen = []
    with pytest.raises(type(error)) as exc:
        ModelRuntime(impl).complete_json(REQUEST, on_usage=seen.append)
    assert exc.value is error
    assert seen == [None]
    assert impl.complete.call_count == 1


def test_factory_modes_and_compatibility_exception_aliases():
    assert gateway.LLMCallError is LLMCallError
    assert gateway.LLMOutputValidationError is LLMOutputValidationError
    assert gateway.build_llm_client(gateway.LLMSettings()) is None
    assert isinstance(
        gateway.build_llm_client(gateway.LLMSettings(agent_mode="fake")), gateway.FakeLLMClient
    )
    client = gateway.build_llm_client(gateway.LLMSettings(agent_mode="llm", api_key="mock"))
    assert isinstance(client.runtime, ModelRuntime)
    assert isinstance(client.runtime.adapter, QwenAdapter)
    assert (client.provider, client.model) == ("dashscope", "qwen-plus")


def test_qwen_adapter_wire_envelope_and_optional_metadata():
    response = httpx.Response(
        200,
        json={
            "model": "qwen-plus-reported",
            "choices": [{"message": {"content": "not-json"}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": "10",
                "completion_tokens": 2,
                "total_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        },
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )
    with patch("yield_rca_core.providers.qwen.httpx.Client") as client:
        client.return_value.__enter__.return_value.post.return_value = response
        output = QwenAdapter(
            base_url="https://example.test/", api_key="mock", timeout_seconds=120, max_retries=1
        ).complete(REQUEST)
    client.assert_called_once_with(timeout=120)
    client.return_value.__enter__.return_value.post.assert_called_once_with(
        "https://example.test/chat/completions",
        headers={"Authorization": "Bearer mock", "Content-Type": "application/json"},
        json={
            "model": "qwen-plus",
            "messages": [asdict(m) for m in REQUEST.messages],
            "temperature": 0.25,
            "response_format": {"type": "json_object"},
        },
    )
    assert output.content == "not-json"  # only Runtime decodes content
    assert output.usage == ModelUsage(
        10, 2, 1, 4, 1, ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    assert output.stop_reason == "length" and output.reported_model == "qwen-plus-reported"


def test_model_telemetry_nested_bounded_fail_open_and_no_payloads():
    runtime = ModelRuntime(adapter())
    with capture_model_telemetry() as outer:
        with capture_model_telemetry(max_events=1) as inner:
            runtime.complete_json(REQUEST)
        assert len(inner.events) == 1 and inner.dropped_count == 1 and not inner.complete
    assert len(outer.events) == 2 and outer.complete  # no duplicate merge on exit
    assert "量测" not in repr(outer.events)
    with capture_model_telemetry() as broken:
        broken.events = Mock()
        broken.events.__len__ = Mock(side_effect=RuntimeError("observer failed"))
        assert runtime.complete_json(REQUEST).data["candidates"]
        assert not broken.complete


def test_model_telemetry_async_context_isolation():
    async def one(call_id):
        with capture_model_telemetry() as captured:
            await asyncio.sleep(0)
            emit_model_event(replace(REQUEST, logical_call_id=call_id), "logical_call")
        return captured.events

    async def run():
        return await asyncio.gather(one("a"), one("b"))

    a, b = asyncio.run(run())
    assert [e.logical_call_id for e in a] == ["a"]
    assert [e.logical_call_id for e in b] == ["b"]


def test_direct_runtime_assigns_id_and_preserves_configured_model_identity():
    impl = adapter(ModelOutput("{}", ModelUsage(), "stop", "provider-reported-alias"))
    with capture_model_telemetry() as captured:
        result = ModelRuntime(impl).complete_json(replace(REQUEST, logical_call_id=""))
    ids = {event.logical_call_id for event in captured.events}
    assert len(ids) == 1 and next(iter(ids)).startswith("MODEL_")
    assert all(
        event.provider == "dashscope" and event.model == "qwen-plus" for event in captured.events
    )
    assert result.reported_model == "provider-reported-alias"
    assert impl.complete.call_count == 1


def test_optional_provider_metadata_is_bounded_redacted_and_not_a_business_gate():
    response = httpx.Response(
        200,
        json={
            "model": "mock-secret" + "x" * 200,
            "choices": [{"message": {"content": "{}"}, "finish_reason": "Bearer mock-secret"}],
        },
        request=httpx.Request("POST", "https://example.test"),
    )
    impl = QwenAdapter(base_url="https://example.test", api_key="mock-secret")
    with patch("yield_rca_core.providers.qwen.httpx.Client") as client:
        client.return_value.__enter__.return_value.post.return_value = response
        with capture_model_telemetry() as captured:
            result = ModelRuntime(impl).complete_json(REQUEST)
    assert result.data == {}
    assert "mock-secret" not in repr(captured.events)
    assert len(result.reported_model) <= 100
    assert result.usage.reported_fields == ()  # missing usage is not known-free usage


def test_telemetry_result_distinguishes_json_failure_from_http_failure():
    runtime = ModelRuntime(adapter(ModelOutput("not-json", ModelUsage(10, 2, 12))))
    with capture_model_telemetry() as captured:
        with pytest.raises(LLMOutputValidationError):
            runtime.complete_json(REQUEST)
    result = captured.events[-1]
    assert result.outcome == "invalid_json"
    assert result.error_category == "invalid_json" and result.usage.total_tokens == 12


def test_telemetry_outside_capture_does_not_leak_into_next_capture():
    emit_model_event(REQUEST, "logical_call")
    with capture_model_telemetry() as captured:
        pass
    assert captured.events == [] and captured.complete
