"""Same response tape through old Qwen and Runtime/Qwen; compare full workflow hashes.

Old-source exceptions and usage sink are bound to the caller's imports, exactly
as they were in a single installed old module. The old HTTP/parse code is intact.
The current path consumes the recorded tape, never regenerates model responses.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.model_telemetry import capture_model_telemetry

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from provider_runtime_support import load_legacy  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "workflow_capture_for_runtime_parity",
    ROOT / "tests/integration/test_provider_workflow_baseline.py",
)
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)
LEGACY = load_legacy()


@pytest.mark.parametrize("scenario", freeze.SCENARIOS)
def test_whole_workflow_on_identical_old_response_tape(scenario):
    source_type, mode = freeze.SCENARIOS[scenario]
    tape = []
    old_wire = []
    new_wire = []

    def client_type(module, playback):
        class WireClient:
            provider = "dashscope"
            model = "qwen-plus"

            def __init__(self):
                self.delegate = module.DashScopeLLMClient(
                    module.LLMSettings(
                        agent_mode="llm",
                        api_key="mock-only",
                        base_url="https://example.test",
                        max_retries=1,
                    )
                )
                self.source = None if playback else source_type()
                self.index = 0

            def complete_json(self, request):
                if playback:
                    expected_request, responses = tape[self.index]
                    assert asdict(request) == expected_request
                else:
                    try:
                        # The response generator is a fixture, not an extra model usage.
                        with patch.object(gateway, "_record_usage", lambda event: None):
                            generated = self.source.complete_json(request)
                        responses = [
                            (
                                200,
                                {
                                    "choices": [
                                        {
                                            "message": {"content": json.dumps(generated.data)},
                                            "finish_reason": "stop",
                                        }
                                    ],
                                    "usage": {
                                        "prompt_tokens": 10,
                                        "completion_tokens": 2,
                                        "total_tokens": 12,
                                    },
                                },
                            )
                        ]
                    except gateway.LLMCallError:
                        responses = [
                            (429, {"error": {"code": "Throttling"}, "request_id": "req-parity"})
                        ] * 2
                    tape.append((copy.deepcopy(asdict(request)), copy.deepcopy(responses)))
                self.index += 1
                with patch.object(module.httpx, "Client") as factory:
                    post = factory.return_value.__enter__.return_value.post
                    post.side_effect = [
                        httpx.Response(
                            status,
                            json=body,
                            request=httpx.Request("POST", "https://example.test/chat/completions"),
                        )
                        for status, body in responses
                    ]
                    try:
                        return self.delegate.complete_json(request)
                    finally:
                        wire = new_wire if playback else old_wire
                        wire.extend(
                            [(c.args, copy.deepcopy(c.kwargs)) for c in post.call_args_list]
                        )

        return WireClient

    # Only shared exception identity and observer routing; old algorithm unchanged.
    with (
        patch.object(LEGACY, "LLMCallError", gateway.LLMCallError),
        patch.object(LEGACY, "LLMOutputValidationError", gateway.LLMOutputValidationError),
        patch.object(LEGACY, "_record_usage", gateway._record_usage),
    ):
        with patch.dict(freeze.SCENARIOS, {scenario: (client_type(LEGACY, False), mode)}):
            old = freeze.capture(scenario)
    with capture_model_telemetry() as telemetry:
        with patch.dict(freeze.SCENARIOS, {scenario: (client_type(gateway, True), mode)}):
            new = freeze.capture(scenario)
    assert old == new  # requests/responses, events, tools, complete State and key subtrees
    assert old_wire == new_wire
    assert len(tape) == old["logical_invocations"]
    assert sum(e.kind == "logical_call" for e in telemetry.events) == len(tape)
    assert sum(e.kind == "http_attempt" for e in telemetry.events) == len(old_wire)
    assert telemetry.complete
    if scenario == "next_action_failure":
        # Caller retry remains above adapter retry: two logical failures, four HTTP calls.
        failures = [
            e for e in telemetry.events if e.kind == "model_result" and e.outcome == "failed"
        ]
        assert len(failures) == 2
        failure_ids = {e.logical_call_id for e in failures}
        assert (
            sum(
                e.kind == "http_attempt" and e.logical_call_id in failure_ids
                for e in telemetry.events
            )
            == 4
        )
        assert new["fallback"]
