"""Freeze existing Qwen wire/error/count semantics without production changes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from yield_rca_core.llm_gateway import (
    DashScopeLLMClient,
    LLMCallError,
    LLMOutputValidationError,
    LLMRequest,
    LLMSettings,
    capture_llm_usage,
    load_prompt,
)

REQUEST = LLMRequest(
    agent="planner",
    prompt_name="planner",
    prompt_version="v1",
    payload={"query": "量测", "nested": [1, {"valid": True}]},
)


def response(status=200, content='{"accepted":true}'):
    return httpx.Response(
        status,
        request=httpx.Request("POST", "https://example.test/chat/completions"),
        json=(
            {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
            if status == 200
            else {"error": {"code": "test-code", "message": "test"}}
        ),
    )


def invoke(outcomes):
    context = MagicMock()
    context.__enter__.return_value.post.side_effect = outcomes
    settings = LLMSettings(
        agent_mode="llm",
        api_key="mock-only",
        base_url="https://example.test",
        timeout_seconds=120,
        max_retries=1,
    )
    with patch("yield_rca_core.llm_gateway.httpx.Client", return_value=context) as factory:
        with capture_llm_usage() as events:
            try:
                result = DashScopeLLMClient(settings).complete_json(REQUEST)
            except (LLMCallError, LLMOutputValidationError) as error:
                result = error
    return result, events, context.__enter__.return_value.post, factory


def test_exact_wire_and_response_contract():
    result, events, post, factory = invoke([response()])
    assert result.data == {"accepted": True}
    assert result.usage is events[0]
    assert len(events) == post.call_count == 1
    assert (events[0].provider, events[0].model) == ("dashscope", "qwen-plus")
    assert (events[0].prompt_tokens, events[0].completion_tokens, events[0].total_tokens) == (
        10,
        2,
        12,
    )
    assert post.call_args.args == ("https://example.test/chat/completions",)
    assert post.call_args.kwargs == {
        "headers": {"Authorization": "Bearer mock-only", "Content-Type": "application/json"},
        "json": {
            "model": "qwen-plus",
            "messages": [
                {"role": "system", "content": load_prompt("planner", "v1")},
                {
                    "role": "user",
                    "content": json.dumps(
                        REQUEST.payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        },
    }
    factory.assert_called_once_with(timeout=120)


@pytest.mark.parametrize(
    "status,attempts", [(400, 1), (401, 1), (403, 1), (408, 2), (429, 2), (500, 2), (503, 2)]
)
def test_http_error_retry_and_legacy_usage(status, attempts):
    result, events, post, _ = invoke([response(status), response(status)])
    assert isinstance(result, LLMCallError)
    assert result.failure_category == "provider_http_error"
    assert result.status_code == status
    assert result.call_attempt_count == attempts == post.call_count
    assert len(events) == 1 and events[0].status == "failed"
    assert events[0].total_tokens == 0  # legacy unknown-as-zero; do not infer free usage


def test_successful_http_retry_is_one_legacy_usage_event():
    result, events, post, _ = invoke([response(429), response()])
    assert result.data == {"accepted": True}
    assert post.call_count == 2 and len(events) == 1
    assert events[0].status == "success"


def test_transport_timeout_preserves_attempts_and_exception_chain():
    error = httpx.ReadTimeout("mock timeout")
    result, events, post, _ = invoke([error, error])
    assert isinstance(result, LLMCallError)
    assert result.failure_category == "transport_error"
    assert result.__cause__ is error
    assert result.call_attempt_count == post.call_count == 2
    assert len(events) == 1 and events[0].status == "failed"


@pytest.mark.parametrize("content", ["not-json", "[]"])
def test_invalid_content_is_not_retried_and_usage_is_not_validation_success(content):
    result, events, post, _ = invoke([response(content=content)])
    assert isinstance(result, LLMOutputValidationError)
    assert post.call_count == len(events) == 1
    assert events[0].status == "success"  # baseline records usage BEFORE JSON decoding


def test_invalid_envelope_records_failed_usage_without_retry():
    bad = httpx.Response(200, json={}, request=httpx.Request("POST", "https://example.test"))
    result, events, post, _ = invoke([bad])
    assert isinstance(result, LLMOutputValidationError)
    assert len(events) == post.call_count == 1
    assert events[0].status == "failed"


def test_code_fence_json_compatibility_is_retained():
    result, events, post, _ = invoke([response(content='```json\n{"accepted":true}\n```')])
    assert result.data == {"accepted": True}
    assert len(events) == post.call_count == 1
