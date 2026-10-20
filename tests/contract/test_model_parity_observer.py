"""Prove the external Batch 2 observer is transparent before paid execution."""

from __future__ import annotations

import importlib.util
import sys
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from yield_rca_core import llm_gateway as gateway

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from provider_runtime_support import load_legacy  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "parity_smoke_observer", ROOT / "tools/run_model_parity_smoke.py"
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def run(module, observed, scenario):
    settings = module.LLMSettings(
        agent_mode="llm", api_key="offline-secret", base_url="https://example.test"
    )
    request = module.LLMRequest(
        agent="planner",
        prompt_name="next_action_planner",
        prompt_version="v1",
        payload={"output_attempt": 1},
    )
    bodies = []

    def handler(req):
        bodies.append(req.content)
        if scenario == "timeout":
            raise httpx.ReadTimeout("offline timeout")
        status = (
            429
            if scenario == "caller_retry" or (scenario == "http_retry" and len(bodies) == 1)
            else 200
        )
        payload = {
            "choices": [
                {
                    "message": {"content": "invalid" if scenario == "invalid_json" else "{}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        if scenario == "envelope":
            payload = {}
        return httpx.Response(status, json=payload)

    real_client = httpx.Client
    observer = smoke.ModelObserver(module, "offline-secret")
    outcomes = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "perf_counter", return_value=10.0))
        stack.enter_context(patch.object(module, "uuid4", return_value=UUID(int=1)))
        events = stack.enter_context(module.capture_llm_usage())
        if observed:
            observer.install(stack)
        stack.enter_context(
            patch.object(
                httpx,
                "Client",
                side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
            )
        )
        client = module.DashScopeLLMClient(settings)
        for _ in range(2 if scenario == "caller_retry" else 1):
            try:
                outcomes.append(asdict(client.complete_json(request)))
            except (module.LLMCallError, module.LLMOutputValidationError) as exc:
                outcomes.append(
                    (type(exc).__name__, str(exc), vars(exc), type(exc.__cause__).__name__)
                )
    return outcomes, [e.to_dict() for e in events], bodies, observer


@pytest.mark.parametrize("module", [gateway, load_legacy()], ids=["runtime", "old"])
@pytest.mark.parametrize(
    "scenario", ["success", "http_retry", "caller_retry", "timeout", "invalid_json", "envelope"]
)
def test_observer_on_off_has_identical_wire_results_usage_and_exceptions(module, scenario):
    bare = run(module, False, scenario)
    observed = run(module, True, scenario)
    assert bare[:3] == observed[:3]
    observer = observed[3]
    assert observer.dropped == 0
    assert sum(len(c["http_attempts"]) for c in observer.calls) == len(observed[2])
    assert len(observer.calls) == (2 if scenario == "caller_retry" else 1)
    assert sum(c["planner_transient_retry"] for c in observer.calls) == (
        1 if scenario == "caller_retry" else 0
    )
    assert all(c.get("logical_call_id") for c in observer.calls)
    assert "offline-secret" not in str(observer.calls)


def test_request_markers_separate_output_retry_and_transient_retry():
    observer = smoke.ModelObserver(gateway)
    request = gateway.LLMRequest(
        agent="planner",
        prompt_name="next_action_planner",
        prompt_version="v1",
        payload={"output_attempt": 2},
    )
    assert observer.attempt_metadata(request)["bounded_output_retry"]
    observer.previous_request = request
    observer.previous_error = gateway.LLMCallError("rate", status_code=429)
    result = observer.attempt_metadata(request)
    assert result["planner_transient_retry"] and not result["bounded_output_retry"]


def test_telemetry_sink_failure_does_not_change_response():
    observer = smoke.ModelObserver(gateway)
    observer.calls = None  # broken external collection; model outcome remains unchanged
    sentinel = object()
    request = gateway.LLMRequest(
        agent="planner", prompt_name="planner", prompt_version="v1", payload={}
    )
    client = type("Client", (), {"provider": "dashscope", "model": "qwen-plus"})()
    assert observer.wrap_complete(lambda self, req: sentinel)(client, request) is sentinel
    assert observer.dropped == 1
