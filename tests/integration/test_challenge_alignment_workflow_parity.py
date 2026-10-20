"""Frozen-response workflows: citation feedback changes requests, never authority."""

from __future__ import annotations

import copy
import socket
import sys
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from itertools import count
from unittest.mock import patch
from uuid import UUID

import pytest
from challenge_diagnostics_support import before_observability
from test_challenge_diagnostic_workflow_parity import ROOT, FrozenDatetime, Source
from yield_rca_core import causal_adversarial, llm_gateway, rca_reasoning_agent, tool_layer
from yield_rca_core.challenge_projection import expand_challenge_payload
from yield_rca_core.llm_gateway import LLMResponse, LLMSettings
from yield_rca_core.workflow import build_csv_workflow
from yield_rca_core.workflow_events import capture_workflow_events


def semantic(value):
    if isinstance(value, dict):
        return {
            k: semantic(v) for k, v in value.items()
            if k not in {
                "citation_failures", "prompt_payload_char_count", "unprojected_payload_chars",
                "payload_chars", "system_prompt_chars", "combined_request_chars", "remaining_chars",
            }
        }
    if isinstance(value, (list, tuple)):
        return [semantic(v) for v in value]
    return value


@pytest.mark.parametrize("mode", ["fixed", "controlled_react", "llm_react"])
@pytest.mark.parametrize("repair_accepts", [True, False])
def test_frozen_citation_response_workflow_parity(mode, repair_accepts, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline workflow cannot connect to a network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    tape, runs = [], []
    saw_feedback = False
    for playback in (False, True):
        requests, events, tool_inputs = [], [], []
        current_inputs = {}

        class Client(Source):
            def complete_json(
                self, request, requests=requests, playback=playback, current_inputs=current_inputs,
            ):
                nonlocal saw_feedback
                requests.append(copy.deepcopy(asdict(request)))
                if playback:
                    old_request, response = tape[len(requests) - 1]
                    old_payload = expand_challenge_payload(old_request["payload"])
                    new_payload = expand_challenge_payload(request.payload)
                    assert semantic(new_payload) == semantic(old_payload)
                    if request.prompt_name == "causal_adversarial_challenge":
                        feedback = new_payload.get("previous_validation_feedback") or {}
                        if request.payload["output_attempt"] == 2:
                            assert feedback["citation_failures"]
                            saw_feedback = True
                    llm_gateway._record_usage(response.usage)
                    return copy.deepcopy(response)
                response = super().complete_json(request)
                if request.prompt_name == "causal_adversarial_challenge":
                    data = copy.deepcopy(response.data)
                    raw = data["challenges"][0]
                    lane = next(
                        lane for lane in current_inputs["lane_contexts"]
                        if lane["lane_id"] == raw["evidence_probe_lane_id"]
                    )
                    wrong = sorted(
                        eid for eid, evidence in current_inputs["evidence_by_id"].items()
                        if eid in current_inputs["evidence_ids"]
                        and not causal_adversarial._evidence_matches_lane(evidence, lane)
                    )
                    assert wrong
                    if not repair_accepts or request.payload["output_attempt"] == 1:
                        raw["supporting_evidence_ids"] = wrong[:1]
                        raw["contradicting_evidence_ids"] = []
                        raw["unexplained_precursor_evidence_ids"] = []
                    response = LLMResponse(data=data, usage=response.usage)
                tape.append((copy.deepcopy(asdict(request)), copy.deepcopy(response)))
                return response

        base = causal_adversarial if playback else before_observability()

        class Challenger(base.QwenAdversarialChallenger):
            def generate(self, current_inputs=current_inputs, **kwargs):
                current_inputs.clear()
                current_inputs.update(kwargs)
                return super().generate(**kwargs)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(rca_reasoning_agent, "QwenAdversarialChallenger", Challenger)
            )
            for name, module in list(sys.modules.items()):
                if not name.startswith("yield_rca_core.") or module is None:
                    continue
                if getattr(module, "datetime", None) is datetime:
                    stack.enter_context(patch.object(module, "datetime", FrozenDatetime))
                if "perf_counter" in vars(module):
                    stack.enter_context(patch.object(module, "perf_counter", return_value=100.0))
                if "uuid4" in vars(module):
                    seq = count(1)
                    stack.enter_context(
                        patch.object(
                            module, "uuid4", side_effect=lambda seq=seq: UUID(int=next(seq)),
                        )
                    )
            for cls in list(vars(tool_layer).values()):
                if not isinstance(cls, type):
                    continue
                method = cls.__dict__.get("run")
                if method is None or not hasattr(method, "__wrapped__"):
                    continue

                def record(self, request, original=method, recorded=tool_inputs):
                    recorded.append(copy.deepcopy(request.to_dict()))
                    return original(self, request)
                stack.enter_context(patch.object(cls, "run", record))
            stack.enter_context(capture_workflow_events(
                lambda event, data, recorded=events: recorded.append((event, copy.deepcopy(data)))
            ))
            workflow = build_csv_workflow(
                ROOT / "data/seeds/golden_case",
                llm_settings=LLMSettings(agent_mode="llm", api_key="mock-only"),
                llm_client=Client(), orchestration_mode=mode,
            )
            state = workflow.run(
                "Investigate the scratch found in Cu CMP and identify root cause and impact lots.",
                job_id="ALIGNMENT_RESPONSE_TAPE", lot_id="LOT_A_001",
            )
            assert state.job.status == "completed"
            generations = [
                f.details["adversarial_challenge_generation"]
                for f in state.findings if f.agent == "rca_reasoning"
            ]
            assert generations
            assert all(g["attempt_count"] == 2 for g in generations)
            assert all(bool(g["challenge_count"]) == repair_accepts for g in generations)
            runs.append(semantic({
                "requests": requests, "state": state.to_dict(),
                "events": events, "tools": tool_inputs,
            }))
    assert saw_feedback
    assert len(tape) == len(runs[1]["requests"])
    assert runs[0] == runs[1]
