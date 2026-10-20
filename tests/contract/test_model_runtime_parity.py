"""Paired old/new wire executions against identical offline response sequences."""

from __future__ import annotations

import copy
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.model_telemetry import capture_model_telemetry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provider_runtime_support import load_legacy  # noqa: E402

LEGACY = load_legacy()


def envelope(content='{"ok":true}', usage=None, **extra):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": usage if usage is not None else {"prompt_tokens": 10, "completion_tokens": 2},
        **extra,
    }


def scenarios():
    cases = {
        "success": [(200, envelope())],
        "fence": [(200, envelope('```json\n{"ok":true}\n```'))],
        "invalid_json": [(200, envelope("not-json"))],
        "array": [(200, envelope("[]"))],
        "missing_choices": [(200, {})],
        "empty_choices": [(200, {"choices": []})],
        "nonstring_content": [(200, envelope(None))],
        "envelope_array": [(200, [])],
        "envelope_nonjson": [(200, "<html>")],
        "missing_usage": [(200, {"choices": [{"message": {"content": "{}"}}]})],
        "null_usage": [(200, {**envelope(), "usage": None})],
        "bad_tokens": [(200, envelope(usage={"prompt_tokens": "invalid"}))],
        "negative_tokens": [(200, envelope(usage={"prompt_tokens": -1}))],
        "details": [
            (
                200,
                envelope(
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 1,
                        "prompt_tokens_details": {"cached_tokens": 3},
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    }
                ),
            )
        ],
        "timeout": ["timeout", "timeout"],
        "connect_error": ["connect", "connect"],
        "timeout_then_success": ["timeout", (200, envelope())],
        "rate_then_success": [(429, {"error": {"code": "Throttle"}}), (200, envelope())],
        "server_then_transport": [(503, {"code": "Unavailable"}), "timeout"],
    }
    for status in (400, 401, 403, 404, 408, 429, 500, 502, 503):
        cases[str(status)] = [
            (
                status,
                {
                    "error": {"code": "test", "message": "Bearer mock-secret api_key=mock-secret"},
                    "request_id": "r1",
                },
            )
        ] * 2
    return cases


CASES = scenarios()


def error_shape(error):
    if error is None:
        return None
    attrs = {
        key: value
        for key, value in vars(error).items()
        if key
        in {
            "status_code",
            "provider_code",
            "provider_message",
            "request_id",
            "failure_category",
            "call_attempt_count",
        }
    }
    return {
        "type": type(error).__name__,
        "message": str(error),
        "attributes": attrs,
        "cause": error_shape(error.__cause__),
    }


def run(module, sequence, retries=1):
    request = module.LLMRequest(
        agent="planner",
        prompt_name="planner",
        prompt_version="v1",
        payload={"query": "量测"},
        temperature=0.7,
    )
    settings = module.LLMSettings(
        agent_mode="llm",
        api_key="mock-secret",
        base_url="https://example.test/",
        timeout_seconds=120,
        max_retries=retries,
    )
    outcomes = []
    for item in sequence:
        if item in ("timeout", "connect"):
            error = httpx.ReadTimeout if item == "timeout" else httpx.ConnectError
            outcomes.append(error("offline transport error"))
        else:
            status, payload = item
            kwargs = (
                {"text": payload} if isinstance(payload, str) else {"json": copy.deepcopy(payload)}
            )
            outcomes.append(
                httpx.Response(
                    status,
                    **kwargs,
                    request=httpx.Request("POST", "https://example.test/chat/completions"),
                )
            )
    with (
        patch.object(module.httpx, "Client") as client,
        patch.object(module, "perf_counter", side_effect=[10.0, 10.123, 10.246]),
        patch.object(module, "uuid4", return_value=UUID(int=1)),
        module.capture_llm_usage() as usage,
        capture_model_telemetry() as facts,
    ):
        post = client.return_value.__enter__.return_value.post
        post.side_effect = outcomes
        try:
            result = module.DashScopeLLMClient(settings).complete_json(request)
            visible = {"response": asdict(result), "error": None}
        except Exception as error:
            assert type(error).__name__ in {"LLMCallError", "LLMOutputValidationError"}
            visible = {"response": None, "error": error_shape(error)}
        visible["usage"] = [e.to_dict() for e in usage]
        visible["wire"] = [(call.args, call.kwargs) for call in post.call_args_list]
        visible["client_options"] = [(call.args, call.kwargs) for call in client.call_args_list]
    return visible, facts


@pytest.mark.parametrize("scenario", CASES)
def test_identical_wire_usage_results_errors_and_attempts(scenario):
    old, old_facts = run(LEGACY, CASES[scenario])
    new, facts = run(gateway, CASES[scenario])
    assert new == old
    assert len(old_facts.events) == 0
    assert sum(e.kind == "logical_call" for e in facts.events) == 1
    assert sum(e.kind == "http_attempt" for e in facts.events) == len(new["wire"])
    assert sum(e.kind == "model_result" for e in facts.events) == 1
    assert facts.complete


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_max_retries_retains_exact_http_meaning(retries):
    sequence = ["timeout"] * (retries + 1)
    old, _ = run(LEGACY, sequence, retries)
    new, facts = run(gateway, sequence, retries)
    assert old == new
    assert len(new["wire"]) == retries + 1
    assert sum(e.kind == "logical_call" for e in facts.events) == 1
