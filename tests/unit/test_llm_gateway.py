from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.llm_gateway import (  # noqa: E402
    DashScopeLLMClient,
    LLMCallError,
    LLMRequest,
    LLMSettings,
)
from yield_rca_core.models import AgentKind  # noqa: E402


def _request() -> LLMRequest:
    return LLMRequest(
        agent=AgentKind.PLANNER.value,
        prompt_name="planner",
        prompt_version="v1",
        payload={"fallback_plan": {"tasks": []}},
    )


def test_dashscope_error_preserves_request_contract_and_provider_diagnostic() -> None:
    response = httpx.Response(
        400,
        json={
            "error": {"code": "InvalidParameter", "message": "unsupported parameter"},
            "request_id": "req-123",
        },
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )
    client_context = MagicMock()
    client_context.__enter__.return_value.post.return_value = response

    settings = LLMSettings(
        agent_mode="llm",
        base_url="https://example.test",
        api_key="test-secret",
        max_retries=0,
    )
    with patch("yield_rca_core.llm_gateway.httpx.Client", return_value=client_context):
        with pytest.raises(LLMCallError) as captured:
            DashScopeLLMClient(settings).complete_json(_request())

    request_body = client_context.__enter__.return_value.post.call_args.kwargs["json"]
    assert request_body["response_format"] == {"type": "json_object"}
    assert request_body["temperature"] == 0.0
    assert captured.value.status_code == 400
    assert captured.value.provider_code == "InvalidParameter"
    assert captured.value.provider_message == "unsupported parameter"
    assert captured.value.request_id == "req-123"


def test_dashscope_provider_error_is_bounded_and_redacts_credentials() -> None:
    api_key = "dashscope-secret-value"
    response = httpx.Response(
        400,
        json={
            "code": "BadRequest",
            "message": f"Authorization: Bearer {api_key} api_key={api_key}",
        },
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )
    client_context = MagicMock()
    client_context.__enter__.return_value.post.return_value = response
    settings = LLMSettings(
        agent_mode="llm",
        base_url="https://example.test",
        api_key=api_key,
        max_retries=0,
    )

    with patch("yield_rca_core.llm_gateway.httpx.Client", return_value=client_context):
        with pytest.raises(LLMCallError) as captured:
            DashScopeLLMClient(settings).complete_json(_request())

    assert captured.value.provider_message is not None
    assert api_key not in captured.value.provider_message
    assert api_key not in str(captured.value)
