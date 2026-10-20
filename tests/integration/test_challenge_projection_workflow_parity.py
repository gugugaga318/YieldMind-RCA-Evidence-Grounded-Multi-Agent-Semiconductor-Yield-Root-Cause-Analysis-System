"""Whole workflows replay the same model responses through frozen/new Challenge."""

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
from challenge_projection_support import legacy
from yield_rca_core import llm_gateway as gateway
from yield_rca_core import rca_reasoning_agent, tool_layer
from yield_rca_core.challenge_projection import expand_challenge_payload
from yield_rca_core.llm_gateway import FakeLLMClient, LLMResponse, LLMSettings
from yield_rca_core.models import RCAState
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
    """Check, then normalize only the explicitly approved size diagnostics."""
    if isinstance(value, dict):
        if "projection_contract" in value:
            value = expand_challenge_payload(value)
        value = {k: semantic_view(v) for k, v in value.items()}
        if value.get("projection") == "lossless_canonical_v1":
            assert value["payload_chars"] == value["prompt_payload_char_count"]
            assert value["remaining_chars"] == 64000 - value["payload_chars"]
            # Public citation-contract alignment adds 2,099 system characters.
            assert value["system_prompt_chars"] == 10231
            assert value["combined_request_chars"] == value["payload_chars"] + 10231
            value["prompt_payload_char_count"] = value.pop("unprojected_payload_chars")
            for key in (
                "projection",
                "payload_chars",
                "remaining_chars",
                "system_prompt_chars",
                "combined_request_chars",
            ):
                value.pop(key)
        return value
    if isinstance(value, list):
        return [semantic_view(v) for v in value]
    if isinstance(value, tuple):
        return tuple(semantic_view(v) for v in value)
    return value


@pytest.mark.parametrize("mode", ["fixed", "controlled_react", "llm_react"])
@pytest.mark.parametrize("outcome", ["valid", "repair", "invalid"])
def test_complete_workflow_on_frozen_response_tape(mode, outcome, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline workflow must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    tape = []
    results = []
    actual_challenges = []
    for playback in (False, True):
        requests, events, tools = [], [], []

        class Client(Source):
            def complete_json(self, request, recorded=requests, replay=playback):
                recorded.append(copy.deepcopy(asdict(request)))
                index = len(recorded) - 1
                if replay:
                    old_request, response = tape[index]
                    assert semantic_view(asdict(request)) == old_request
                    response = copy.deepcopy(response)
                    gateway._record_usage(response.usage)
                    return response
                response = super().complete_json(request)
                if request.prompt_name == "causal_adversarial_challenge":
                    if outcome == "invalid" or (
                        outcome == "repair" and request.payload["output_attempt"] == 1
                    ):
                        response = LLMResponse(data={}, usage=response.usage)
                tape.append((copy.deepcopy(asdict(request)), copy.deepcopy(response)))
                return response

        with ExitStack() as stack:
            if not playback:
                stack.enter_context(
                    patch.object(
                        rca_reasoning_agent,
                        "QwenAdversarialChallenger",
                        legacy().QwenAdversarialChallenger,
                    )
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
        stages = [r["prompt_name"] for r in requests]
        assert "causal_adversarial_challenge" in stages  # Not a vacuous workflow comparison.
        assert state.job.status == "completed"
        challenge_results = [
            f.details["adversarial_challenge_generation"]
            for f in state.findings
            if f.agent == "rca_reasoning"
        ]
        assert challenge_results
        actual_challenges.append(sum(r["challenge_count"] for r in challenge_results))
        if outcome != "invalid":
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
