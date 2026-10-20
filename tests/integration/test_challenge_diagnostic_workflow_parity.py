"""Compare frozen pre-observability and captured workflow/Planner behavior."""

from __future__ import annotations

import copy
import socket
import sys
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from itertools import count
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from challenge_diagnostics_support import before_observability
from yield_rca_core import causal_adversarial, rca_reasoning_agent, tool_layer
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.challenge_diagnostics import capture_challenge_diagnostics
from yield_rca_core.llm_gateway import (
    FakeLLMClient,
    LLMCallError,
    LLMOutputValidationError,
    LLMResponse,
    LLMSettings,
)
from yield_rca_core.models import RCAState
from yield_rca_core.next_action_planner import _compact_finding
from yield_rca_core.workflow import build_csv_workflow
from yield_rca_core.workflow_events import capture_workflow_events

ROOT = Path(__file__).resolve().parents[2]


class Source(FakeLLMClient):
    def complete_json(self, request):
        base = super().complete_json(request)
        p = request.payload
        if request.prompt_name == "hypothesis_candidate_generator":
            ids = [
                e["evidence_id"]
                for e in p["typed_evidence_register"]
                if e["evidence_type"] == "parameter_deviation"
            ]
            data = {
                "candidates": [
                    {
                        "root_cause": "A process excursion may alter removal.",
                        "causal_explanation": "A physical intermediate remains unverified.",
                        "supporting_evidence_ids": ids,
                        "contradicting_evidence_ids": [],
                    }
                ],
                "analysis_summary": "Bounded candidate.",
            }
            return LLMResponse(data=data, usage=base.usage)
        if request.prompt_name == "causal_adversarial_challenge":
            contract = p["challenge_output_contract"]
            candidate = p["candidates"][0]["candidate_id"]
            lane = p["causal_lanes"][0]
            gaps = contract["highest_information_gain_gap_ids_by_candidate_and_lane"]
            evidence = [
                e["evidence_id"]
                for e in p["typed_evidence_register"]
                if lane["equipment"] in e["entity_ids_by_type"].get("equipment", [])
            ]
            data = {
                "challenges": [
                    {
                        "candidate_id": candidate,
                        "alternative_candidate_id": None,
                        "evidence_probe_lane_id": lane["lane_id"],
                        "challenge_kind": "mechanism",
                        "mechanism_relation": "unknown",
                        "supporting_evidence_ids": evidence,
                        "contradicting_evidence_ids": [],
                        "unexplained_precursor_evidence_ids": [],
                        "distinguishing_gap_ids": gaps[candidate][lane["lane_id"]][:1],
                        "distinguishing_questions": [],
                        "challenge_explanation": "The physical intermediate remains unverified.",
                        "status": (
                            "unresolved"
                            if gaps[candidate][lane["lane_id"]]
                            else "non_discriminative"
                        ),
                    }
                ],
                "analysis_summary": "Keep investigation unresolved.",
            }
            return LLMResponse(data=data, usage=base.usage)
        return base


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 4, 0, 0, 0, tzinfo=tz)


def semantic_view(value):
    # Observability must preserve the entire raw State/request, including count.
    return value


def planner_counts(requests):
    counts = []

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("adversarial_challenge_generation"), dict):
                count = value["adversarial_challenge_generation"].get("attempt_count")
                if count is not None:
                    counts.append(count)
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for request in requests:
        if request["prompt_name"] == "next_action_planner":
            visit(request["payload"])
    return counts


@pytest.mark.parametrize("mode", ["fixed", "controlled_react", "llm_react"])
@pytest.mark.parametrize(
    "outcome", ["valid", "repair", "invalid", "preflight", "repair_preflight", "provider_failure"]
)
def test_capture_workflow_and_planner_raw_parity(mode, outcome, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline workflow must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    tape = []
    results = []
    actual_challenges = []
    planner_observations = []
    call_observations = []
    projected_observations = []
    for playback in (False, True):
        requests, events, tools = [], [], []
        measured = []

        class Client(Source):
            def complete_json(self, request, recorded=requests, replay=playback):
                recorded.append(copy.deepcopy(asdict(request)))
                index = len(recorded) - 1
                if replay:
                    old_request, response = tape[index]
                    assert semantic_view(asdict(request)) == semantic_view(old_request)
                    response = copy.deepcopy(response)
                    if isinstance(response, Exception):
                        raise response
                    gateway._record_usage(response.usage)
                    return response
                if request.prompt_name == "causal_adversarial_challenge" and outcome in (
                    "repair_preflight",
                    "provider_failure",
                ):
                    error = (
                        LLMOutputValidationError("invalid JSON: " + "x" * 80000)
                        if outcome == "repair_preflight"
                        else LLMCallError(
                            "offline timeout", failure_category="timeout", call_attempt_count=2
                        )
                    )
                    tape.append((copy.deepcopy(asdict(request)), error))
                    raise error
                response = super().complete_json(request)
                if request.prompt_name == "causal_adversarial_challenge":
                    if outcome == "invalid" or (
                        outcome == "repair" and request.payload["output_attempt"] == 1
                    ):
                        response = LLMResponse(data={}, usage=response.usage)
                tape.append((copy.deepcopy(asdict(request)), copy.deepcopy(response)))
                return response

        with ExitStack() as stack:
            captured = (
                stack.enter_context(capture_challenge_diagnostics(include_response_json=True))
                if playback
                else None
            )
            base = causal_adversarial if playback else before_observability()

            class ObservedChallenger(base.QwenAdversarialChallenger):
                def generate(self, recorded=requests, observations=measured, **kwargs):
                    if outcome == "preflight":
                        kwargs["candidates"] = copy.deepcopy(kwargs["candidates"])
                        kwargs["candidates"][0]["causal_explanation"] = "x" * 80000
                    before = sum(
                        r["prompt_name"] == "causal_adversarial_challenge" for r in recorded
                    )
                    generated = super().generate(**kwargs)
                    calls = (
                        sum(r["prompt_name"] == "causal_adversarial_challenge" for r in recorded)
                        - before
                    )
                    observations.append((generated.attempt_count, calls))
                    return generated

            stack.enter_context(
                patch.object(rca_reasoning_agent, "QwenAdversarialChallenger", ObservedChallenger)
            )
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
                            module,
                            "uuid4",
                            side_effect=lambda seq=sequence: UUID(int=next(seq)),
                        )
                    )
            for value in list(vars(tool_layer).values()):
                if not isinstance(value, type):
                    continue
                method = value.__dict__.get("run")
                if method is None or not hasattr(method, "__wrapped__"):
                    continue

                def record_tool(self, request, original=method, recorded=tools):
                    recorded.append(copy.deepcopy(request.to_dict()))
                    return original(self, request)

                stack.enter_context(patch.object(value, "run", record_tool))
            stack.enter_context(
                capture_workflow_events(
                    lambda event, data, recorded=events: recorded.append(
                        (event, copy.deepcopy(data))
                    )
                )
            )
            workflow = build_csv_workflow(
                ROOT / "data/seeds/golden_case",
                llm_settings=LLMSettings(agent_mode="llm", api_key="mock-only"),
                llm_client=Client(),
                orchestration_mode=mode,
            )
            state = workflow.run(
                "Investigate the scratch found in Cu CMP and identify root cause and impact lots.",
                job_id="CHALLENGE_PROJECTION_TAPE",
                lot_id="LOT_A_001",
            )
            serialized = state.to_dict()
            assert RCAState.from_dict(serialized).to_dict() == serialized
        if playback:
            assert captured.records
            assert [r["attempt_count"] for r in captured.records] == [v for v, _ in measured]
            assert captured.dropped_count == 0
        stages = [r["prompt_name"] for r in requests]
        assert measured  # Actual Challenge construction, even when preflight blocks calls.
        if outcome == "preflight":
            assert "causal_adversarial_challenge" not in stages
        else:
            assert "causal_adversarial_challenge" in stages
        for diagnostic, calls in measured:
            if playback:
                assert diagnostic == calls
            else:
                assert diagnostic == calls
        call_observations.append(measured)
        planner_observations.append(planner_counts(requests))
        assert state.job.status == "completed"
        challenge_results = [
            f.details["adversarial_challenge_generation"]
            for f in state.findings
            if f.agent == "rca_reasoning"
        ]
        assert challenge_results
        projected_counts = []
        for finding in state.findings:
            if finding.agent == "rca_reasoning":
                projected = _compact_finding(finding)["adversarial_challenge_generation"]
                original = finding.details["adversarial_challenge_generation"]
                assert projected["attempt_count"] == original["attempt_count"]
                projected_counts.append(projected["attempt_count"])
        assert projected_counts == [diagnostic for diagnostic, _ in measured]
        projected_observations.append(projected_counts)
        actual_challenges.append(sum(r["challenge_count"] for r in challenge_results))
        if outcome in ("valid", "repair"):
            assert actual_challenges[-1] > 0
        assert any(
            f.details.get("ranked_candidates") for f in state.findings if f.agent == "rca_reasoning"
        )
        results.append(
            semantic_view(
                {
                    "requests": requests,
                    "state": serialized,
                    "events": events,
                    "tools": tools,
                }
            )
        )
    assert len(tape) == len(results[1]["requests"])
    assert results[0] == results[1]
    assert actual_challenges[0] == actual_challenges[1]

    assert [calls for _, calls in call_observations[0]] == [
        calls for _, calls in call_observations[1]
    ]
    if outcome in ("preflight", "repair_preflight"):
        assert set(projected_observations[0]) == {0 if outcome == "preflight" else 1}
        assert set(projected_observations[1]) == {0 if outcome == "preflight" else 1}
    else:
        assert projected_observations[0] == projected_observations[1]
    if mode == "llm_react":
        assert bool(planner_observations[0]) == bool(planner_observations[1])
        if outcome in ("valid", "repair"):
            assert planner_observations[0]  # Real post-Challenge Planner request.
        if planner_observations[0]:
            if outcome in ("preflight", "repair_preflight"):
                assert set(planner_observations[0]) == {0 if outcome == "preflight" else 1}
                assert set(planner_observations[1]) == {0 if outcome == "preflight" else 1}
            else:
                assert planner_observations[0] == planner_observations[1]
        else:
            # Conservative failures can finalize without another model Planner
            # request. The production reader is still verified above.
            assert outcome in ("invalid", "preflight", "repair_preflight", "provider_failure")
