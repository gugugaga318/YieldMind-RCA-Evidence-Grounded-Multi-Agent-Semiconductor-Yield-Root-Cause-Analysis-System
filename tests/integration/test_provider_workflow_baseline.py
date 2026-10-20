"""Frozen full-payload hashes from exact 8d9192f; no production instrumentation.

Identity and clocks are fixed at their generation sources. Requests, responses,
ToolInput and progress events are compared in full.
Batch 3 validates the additive identity/telemetry metadata fields separately;
all pre-existing State fields remain compared against the immutable old hashes.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from itertools import count
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
FIXTURE = ROOT / "tests/fixtures/provider_abstraction/workflow_baseline.json"
spec = importlib.util.spec_from_file_location(
    "provider_baseline_characterization",
    ROOT / "tests/integration/test_react_loop_characterization.py",
)
assert spec and spec.loader
characterization = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = characterization
spec.loader.exec_module(characterization)

from yield_rca_core import tool_layer  # noqa: E402
from yield_rca_core.models import RCAState  # noqa: E402
from yield_rca_core.result_identity import ResultIdentity  # noqa: E402

SCENARIOS = {
    "native_controlled": (characterization.RecordingFakeClient, "controlled_react"),
    "llm_normal": (characterization.RecordingFakeClient, "llm_react"),
    "next_action_failure": (characterization.PersistentNextActionCallFailureClient, "llm_react"),
    "intent_invalid": (characterization.InvalidIntentClient, "llm_react"),
    "llm_stop": (characterization.ImmediateUnsupportedStopClient, "llm_react"),
}


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 13, 0, 0, 0, tzinfo=tz)


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capture(scenario):
    client_type, mode = SCENARIOS[scenario]
    calls = []
    tools = []

    class Recording(client_type):
        def complete_json(self, request):
            record = {"request": copy.deepcopy(asdict(request))}
            calls.append(record)
            try:
                response = super().complete_json(request)
                record["response"] = copy.deepcopy(asdict(response))
                return response
            except Exception as exc:
                record["exception"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "attributes": copy.deepcopy(vars(exc)),
                }
                raise

    with ExitStack() as stack:
        # Fixed generators, not an after-the-fact ignore list.
        for name, module in list(sys.modules.items()):
            if not name.startswith("yield_rca_core.") or module is None:
                continue
            if getattr(module, "datetime", None) is datetime:
                stack.enter_context(patch.object(module, "datetime", FrozenDatetime))
            if "perf_counter" in vars(module):
                stack.enter_context(patch.object(module, "perf_counter", return_value=100.0))
            if "uuid4" in vars(module):
                sequence = count(1)
                stack.enter_context(
                    patch.object(
                        module, "uuid4", side_effect=lambda seq=sequence: UUID(int=next(seq))
                    )
                )
        for value in list(vars(tool_layer).values()):
            if not isinstance(value, type):
                continue
            method = value.__dict__.get("run")
            if method is None or not hasattr(method, "__wrapped__"):
                continue

            def record_tool(self, request, original=method):
                tools.append(copy.deepcopy(request.to_dict()))
                return original(self, request)

            stack.enter_context(patch.object(value, "run", record_tool))
        state, recorder = characterization._run(
            Recording(), orchestration_mode=mode, job_id="JOB_PROVIDER_BASELINE"
        )
        serialized = state.to_dict()
        assert RCAState.from_dict(serialized).to_dict() == serialized
        identity = serialized["execution_metadata"].pop("model_identity", None)
        if identity is not None:
            assert ResultIdentity.from_dict(identity).author == "model"
            assert identity["provider"] == state.execution_metadata["provider"]
            assert identity["model"] == state.execution_metadata["model"]
        telemetry = serialized["execution_metadata"].pop("model_telemetry", None)
        if telemetry is not None:
            assert telemetry["complete"] and telemetry["dropped_count"] == 0
            logical = [e for e in telemetry["events"] if e["kind"] == "logical_call"]
            assert len(logical) == len(calls)
            assert [e["stage"] for e in logical] == [c["request"]["prompt_name"] for c in calls]
        origins = serialized["execution_metadata"].pop("planner_origins", {})
        assert set(origins) <= {d.decision_id for d in state.planner_decisions}
        assert all(o["decision"] in {"qwen", "python_runtime"} for o in origins.values())
        captured = {
            "calls": calls,
            "tool_inputs": tools,
            "events": recorder.events,
            "state": serialized,
        }
        for field in (
            "evidence",
            "findings",
            "hypotheses",
            "action_history",
            "planner_decisions",
            "authoritative_rca_result",
            "impact_publication_result",
            "execution_metadata",
        ):
            captured[field] = serialized.get(field)
        return {
            "hashes": {key: digest(value) for key, value in captured.items()},
            "logical_invocations": len(calls),
            "usage_events": len(state.llm_usage),
            "tool_inputs": len(tools),
            "actions": len(state.action_history),
            "evidence": len(state.evidence),
            "request_stages": [call["request"]["prompt_name"] for call in calls],
            "actual_mode": state.execution_metadata["orchestration_mode"],
            "fallback": state.execution_metadata.get("orchestration_fallback_reason"),
            "stop_reason": state.stop_reason,
            "http_attempts": 0,
            "coverage": "golden-case fake callers; wire semantics tested separately",
        }


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_frozen_workflow(scenario):
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"][scenario]
    from copilot_evidence_closure_support import assert_evidence_closure_delta

    assert_evidence_closure_delta(scenario, capture(scenario), expected)
