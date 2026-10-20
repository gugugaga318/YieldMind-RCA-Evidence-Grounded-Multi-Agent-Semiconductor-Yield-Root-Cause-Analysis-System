"""Batch 3: offline identity, legacy decode, binding and authority compatibility."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from yield_rca_api.app import _state_api_payload
from yield_rca_api.schemas import RCAJobStateResponse, ResultIdentityResponse
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.causal_candidate_comparison import QwenHypothesisCandidateComparator
from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix
from yield_rca_core.causal_hypothesis import CausalHypothesis
from yield_rca_core.causal_investigation_models import CandidateSemanticProfile
from yield_rca_core.investigation_models import PlannerDecisionOutcome
from yield_rca_core.model_contracts import ModelCapabilities, ModelOutput, ModelUsage
from yield_rca_core.model_runtime import ModelRuntime
from yield_rca_core.model_telemetry import capture_model_telemetry
from yield_rca_core.models import AgentFinding, RCAJob, RCAState
from yield_rca_core.result_identity import (
    ResultIdentity,
    configured_identity,
    model_source,
    resolve_identity,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
from model_benchmark_telemetry import runtime_call_records  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load(
    "identity_candidate_fixtures", "tests/contract/test_qwen_hypothesis_candidate_contracts.py"
)
workflow = load(
    "identity_workflow_fixtures", "tests/integration/test_react_loop_characterization.py"
)
runner = load("identity_formal_runner", "scripts/run_formal_blind_rca.py")


@pytest.mark.parametrize("source", ["qwen", "llm_qwen", "model", "llm_model"])
def test_legacy_authorship_is_not_proof_of_actual_provider(source):
    identity = resolve_identity(source)
    assert identity == ResultIdentity("model")
    assert ResultIdentity.from_dict(identity.to_dict()) == identity
    binding = ResultIdentity("model", "deepseek", "actual-model", "runtime_config")
    assert resolve_identity(source, binding.to_dict()) == binding


@pytest.mark.parametrize(
    "source",
    [
        "python",
        "python_runtime",
        "python_evidence_gate",
        "python_controlled",
        "deterministic_fallback",
    ],
)
def test_python_origin_does_not_inherit_provider_from_run(source):
    bound = ResultIdentity("model", "deepseek", "actual-model", "runtime_config")
    assert resolve_identity(source, bound.to_dict()) == ResultIdentity(
        "python_runtime", basis="python_runtime"
    )


def test_identity_contract_rejects_conflated_authorship_and_missing_binding():
    with pytest.raises(ValueError):
        ResultIdentity("python_runtime", "qwen", "qwen-plus")
    with pytest.raises(ValueError):
        ResultIdentity("model", basis="runtime_config")
    with pytest.raises(ValueError):
        ResultIdentity("model", "qwen", "qwen-plus", "legacy_source")


@pytest.mark.parametrize("source", ["qwen", "model"])
def test_semantic_profile_legacy_and_neutral_roundtrip(source):
    profile = CandidateSemanticProfile(
        candidate_id="C1",
        scope_relation="focal_only",
        claimed_lane_ids=("L1",),
        comparison_lane_ids=("L1",),
        mechanism_claim="pressure change",
        source=source,
    )
    assert CandidateSemanticProfile.from_dict(profile.to_dict()) == profile
    legacy = profile.to_dict()
    legacy.pop("source")
    assert CandidateSemanticProfile.from_dict(legacy).source == "qwen"


@pytest.mark.parametrize("bound", [False, True])
def test_state_roundtrip_and_read_view_never_rewrite_source(bound):
    evidence = fixtures.causal_findings()[0].evidence
    finding = AgentFinding(
        finding_id="F1",
        agent="rca_reasoning",
        summary="test",
        confidence=0.5,
        evidence_ids=[e.evidence_id for e in evidence],
        details={
            "hypothesis_candidate_generation": {"source": "qwen"},
            "hypothesis_engine_result": {"candidate_comparison": {"source": "python"}},
        },
    )
    metadata = {"provider": "dashscope", "model": "qwen-plus"}
    if bound:
        metadata["model_identity"] = ResultIdentity(
            "model", "dashscope", "qwen-plus", "runtime_config"
        ).to_dict()
    state = RCAState(
        job=RCAJob(job_id="J1", user_query="test"),
        findings=[finding],
        execution_metadata=metadata,
        authoritative_rca_finding_id="F1",
        evidence=list(evidence),
    )
    old = state.to_dict()
    decoded = RCAState.from_dict(json.loads(json.dumps(old)))
    view = decoded.result_identities
    assert decoded.to_dict() == old
    assert view["findings/F1/hypothesis_candidate_generation"]["provider"] == (
        "dashscope" if bound else None
    )
    assert view["findings/F1/candidate_comparison"]["provider"] is None
    api = RCAJobStateResponse.model_validate(_state_api_payload(decoded))
    assert api.result_identities["findings/F1/hypothesis_candidate_generation"].author == "model"


class FixtureAdapter:
    """Offline protocol stub only; no implementation or API for a second provider."""

    capabilities = ModelCapabilities()

    def __init__(self, provider, data=None):
        self.provider, self.data = provider, data
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.data is None:
            llm_request = gateway.LLMRequest(
                agent=request.agent,
                prompt_name=request.stage,
                prompt_version=request.prompt_version,
                payload=json.loads(request.messages[-1].content),
            )
            with patch.object(gateway, "_record_usage", lambda event: None):
                generated = gateway.FakeLLMClient().complete_json(llm_request)
            data = generated.data
        else:
            data = self.data
        return ModelOutput(json.dumps(data), ModelUsage(10, 2, 12), "stop", "untrusted-report")


def test_runtime_binding_cannot_be_forged_by_model_json_or_reported_model():
    forged = {"author": "python_runtime", "provider": "qwen", "model": "fake-name"}
    adapter = FixtureAdapter("deepseek", forged)
    client = gateway.RuntimeLLMClient(ModelRuntime(adapter), model="actual-model")
    with capture_model_telemetry() as capture:
        response = client.complete_json(gateway.LLMRequest("planner", "planner", "v1", {}))
    assert response.data == forged  # Runtime still does not own business validation.
    assert response.identity == ResultIdentity(
        "model", "deepseek", "actual-model", "runtime_config"
    )
    assert configured_identity(client) == response.identity
    assert model_source(client) == "model"
    assert len(adapter.requests) == 1
    assert all((e.provider, e.model) == ("deepseek", "actual-model") for e in capture.events)
    assert all(e.stage == "planner" and e.agent == "planner" for e in capture.events)
    assert capture.events[-1].reported_model == "untrusted-report"
    assert capture.events[-1].latency_ms >= 0
    ResultIdentityResponse.model_validate(response.identity.to_dict())


@pytest.mark.parametrize("provider,source", [("dashscope", "qwen"), ("deepseek", "model")])
def test_neutral_full_workflow_authorship_with_unchanged_authority(provider, source):
    client = gateway.RuntimeLLMClient(ModelRuntime(FixtureAdapter(provider)), model="actual-model")
    run = workflow.build_csv_workflow(
        workflow.SEED_DIR,
        llm_settings=gateway.LLMSettings(agent_mode="llm", api_key="offline-only"),
        llm_client=client,
        orchestration_mode="llm_react",
    )
    state = run.run(workflow.ROOT_CAUSE_QUERY, job_id="IDENTITY_WORKFLOW", lot_id="LOT_A_001")
    assert state.job.status == "completed"
    assert (
        state.execution_metadata["model_identity"]
        == ResultIdentity("model", provider, "actual-model", "runtime_config").to_dict()
    )
    assert state.execution_metadata.get("orchestration_fallback_reason") is None
    generation = state.authoritative_rca_finding.details["hypothesis_candidate_generation"]
    assert generation["source"] == source
    # The generic fixture returns a legal empty candidate set in llm mode.
    # Preserve its conservative result; admitted profiles are tested independently.
    assert generation["candidate_count"] == 0
    assert not generation["candidate_output_invalid"]
    assert state.authoritative_rca_result.conclusion_status == "inconclusive"
    assert state.result_identities
    model_results = [v for v in state.result_identities.values() if v["author"] == "model"]
    assert model_results and all(v["provider"] == provider for v in model_results)
    origins = state.execution_metadata["planner_origins"]
    assert any(o["decision"] == source for o in origins.values())
    assert all(o["decision"] in {source, "python_runtime"} for o in origins.values())
    serialized = state.to_dict()
    assert RCAState.from_dict(serialized).to_dict() == serialized
    api = RCAJobStateResponse.model_validate(_state_api_payload(state))
    assert api.execution_metadata.model_identity.provider == provider
    assert api.result_identities == {
        k: ResultIdentityResponse.model_validate(v) for k, v in state.result_identities.items()
    }


@pytest.mark.parametrize("provider,source", [("dashscope", "qwen"), ("deepseek", "model")])
def test_comparator_identity_keeps_original_validation(provider, source):
    candidate = fixtures.proposal()
    matrix = build_causal_evidence_matrix(
        CausalHypothesis.from_mapping(candidate),
        [e for f in fixtures.causal_findings() for e in f.evidence],
    )
    data = {
        "preferred_candidate_index": 0,
        "comparison_explanation": "supported comparison",
        "selected_gap_id": None,
    }
    client = gateway.RuntimeLLMClient(ModelRuntime(FixtureAdapter(provider, data)), model="actual")
    result = QwenHypothesisCandidateComparator(client).compare(
        request_id="COMPARE", candidates=[candidate], matrices=[matrix], evidence_gaps=[]
    )
    assert result["source"] == source
    assert result["preferred_candidate_index"] == 0
    client.runtime.adapter.data = {**data, "provider": "qwen"}
    with pytest.raises(gateway.LLMOutputValidationError, match="exactly"):
        QwenHypothesisCandidateComparator(client).compare(
            request_id="COMPARE", candidates=[candidate], matrices=[matrix], evidence_gaps=[]
        )


def test_strict_qwen_is_not_silently_relaxed_for_another_provider():
    good = {
        "job_status": "completed",
        "actual_orchestration_mode": "llm_react",
        "hypothesis_candidate_source": "qwen",
        "planner_stop_proposed_by": "qwen",
        "terminal_question_updates_source": "python_evidence_gate",
    }
    assert (
        runner._strict_qwen_acceptance_reasons(good, requested_mode="llm_react", agent_mode="llm")
        == []
    )
    other = {**good, "hypothesis_candidate_source": "model", "planner_stop_proposed_by": "model"}
    assert runner._strict_qwen_acceptance_reasons(
        other, requested_mode="llm_react", agent_mode="llm"
    ) == ["hypothesis_candidate_not_qwen", "planner_stop_source_invalid"]


@pytest.mark.parametrize("status,content", [(200, "{}"), (200, "invalid"), (429, "{}")])
def test_telemetry_benchmark_identity_attempts_and_json_outcome(status, content):
    client = gateway.DashScopeLLMClient(gateway.LLMSettings(agent_mode="llm", api_key="offline"))
    response = httpx.Response(
        status,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        },
        request=httpx.Request("POST", "https://example.test"),
    )
    with capture_model_telemetry() as capture, patch.object(gateway.httpx, "Client") as factory:
        factory.return_value.__enter__.return_value.post.return_value = response
        try:
            client.complete_json(gateway.LLMRequest("planner", "planner", "v1", {}))
        except (gateway.LLMCallError, gateway.LLMOutputValidationError):
            assert status != 200 or content == "invalid"
    telemetry = {
        "events": [asdict(e) for e in capture.events],
        "complete": True,
        "dropped_count": 0,
    }
    (record,) = runtime_call_records(telemetry)
    assert record.provider == "dashscope" and record.model == "qwen-plus"
    assert record.http_attempt_count == (2 if status == 429 else 1)
    assert record.prompt_name == "planner" and record.latency_ms >= 0
    assert all(a.latency_ms >= 0 for a in record.http_attempts)
    assert record.caller_retry_index is None and record.output_attempt_index is None
    if status == 200:
        assert record.input_tokens == 10 and record.output_tokens == 2
        assert record.validation[0].outcome == ("passed" if content == "{}" else "failed")
    else:
        assert record.input_tokens is None and record.validation[0].outcome == "not_observed"
    telemetry["complete"] = False
    telemetry["dropped_count"] = 1
    (partial,) = runtime_call_records(telemetry)
    assert partial.http_attempt_count is None and not partial.http_attempts_complete


def test_planner_outcome_neutral_source_preserves_python_stop_origin():
    planner = load(
        "identity_planner_fixtures", "tests/contract/test_planner_interface_contracts.py"
    )
    decision = workflow.ImmediateUnsupportedStopClient().complete_json(
        gateway.LLMRequest(
            "planner",
            "next_action_planner",
            "v1",
            {
                "goal": planner._goal().to_dict(),
                "questions": [],
                "deterministic_planner_decision": {
                    "decision_id": "D",
                    "goal_id": planner._goal().goal_id,
                    "decision_type": "stop",
                    "reason": "stop",
                    "goal_status": "blocked",
                    "proposed_conclusion_level": "inconclusive",
                    "stop_reason": "data_unavailable",
                    "next_action": None,
                    "target_question_ids": [],
                    "question_updates": [],
                    "new_questions": [],
                },
            },
        )
    )
    from yield_rca_core.investigation_models import PlannerDecision

    outcome = PlannerDecisionOutcome(
        PlannerDecision.from_dict(decision.data), decision_proposed_by="model"
    )
    assert PlannerDecisionOutcome.from_dict(outcome.to_dict()) == outcome
    python = replace(outcome, decision_proposed_by="python_runtime")
    client = SimpleNamespace(provider="deepseek", model="actual")
    fake = SimpleNamespace(llm_client=client, decide_with_review=lambda **kw: python)
    proposal = planner.QwenPlannerAdapter(fake).decide(planner._context())
    assert proposal.proposed_by == "llm_model"
    assert proposal.decision_proposed_by == "python_runtime"
    assert proposal.native_decision is python.decision


@pytest.mark.parametrize("provider,source", [("dashscope", "qwen"), ("deepseek", "model")])
def test_admitted_candidate_and_semantic_profile_identity_is_python_bound(provider, source):
    closure = load(
        "identity_closure_fixtures",
        "tests/contract/test_candidate_evidence_closure_patch_contracts.py",
    )
    items = closure.closure_evidence()
    data = closure.response([e.evidence_id for e in items])
    adapter = FixtureAdapter(provider, data)
    client = gateway.RuntimeLLMClient(ModelRuntime(adapter), model="actual-model")
    generator = fixtures.QwenHypothesisCandidateGenerator(client)
    result = generator.generate(
        request_id="IDENTITY_CANDIDATE",
        findings=[closure.finding(items)],
        causal_lanes=[closure.lane(closure.LANE_A, "RCP_A")],
    )
    assert result.candidates
    assert result.candidate_semantic_profiles
    assert all(p.source == source for p in result.candidate_semantic_profiles)
    assert result.candidates[0].supporting_evidence_ids == tuple(e.evidence_id for e in items)
    # An injected provider field cannot be accepted as semantic-profile authority.
    data["candidate_semantic_profiles"][0]["provider"] = "forged"
    result = generator.generate(
        request_id="IDENTITY_FORGED",
        findings=[closure.finding(items)],
        causal_lanes=[closure.lane(closure.LANE_A, "RCP_A")],
    )
    assert result.semantic_validation_errors
    assert not result.candidate_semantic_profiles
    assert configured_identity(client).provider == provider


def test_legacy_optional_detail_shapes_do_not_break_additive_identity_view():
    evidence = fixtures.causal_findings()[0].evidence
    finding = AgentFinding(
        finding_id="LEGACY",
        agent="rca_reasoning",
        summary="legacy",
        confidence=0.5,
        evidence_ids=[e.evidence_id for e in evidence],
        details={
            "hypothesis_candidate_generation": None,
            "hypothesis_engine_result": {"candidate_comparison": None},
            "candidate_semantic_profiles": [None, {}],
            "specialist_v2": None,
        },
    )
    state = RCAState(
        job=RCAJob(job_id="L", user_query="legacy"),
        findings=[finding],
        evidence=list(evidence),
        authoritative_rca_finding_id="LEGACY",
    )
    before = state.to_dict()
    assert state.result_identities == {}
    assert state.to_dict() == before
    assert RCAState.from_dict(before).to_dict() == before


def test_identity_binding_cannot_override_author_from_result_origin():
    with pytest.raises(ValueError, match="requires model"):
        resolve_identity(
            "model", ResultIdentity("python_runtime", basis="python_runtime").to_dict()
        )
    with pytest.raises(ValueError, match="Python identity basis"):
        ResultIdentity("model", "deepseek", "actual", "python_runtime")
