from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from yield_rca_core.investigation_models import ActionKind, InvestigationAction
from yield_rca_core.llm_gateway import LLMRequest, LLMResponse
from yield_rca_core.models import (
    AgentKind,
    Evidence,
    LLMUsageEvent,
    ToolInput,
    ToolOutput,
)
from yield_rca_core.specialist_v2 import SpecialistV2Error, SpecialistV2Executor

ResponseFactory = Callable[[LLMRequest, int], dict[str, Any]]


def _usage(request: LLMRequest) -> LLMUsageEvent:
    return LLMUsageEvent(
        call_id=f"TEST_{request.prompt_name}",
        agent=request.agent,
        provider="test",
        model="test-model",
        prompt_version=request.prompt_version,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        latency_ms=0.0,
    )


@dataclass
class ScriptedClient:
    planner: ResponseFactory | None = None
    analysis: ResponseFactory | None = None
    provider: str = "test"
    model: str = "test-model"
    planner_calls: int = 0
    analysis_calls: int = 0

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if request.prompt_name == "specialist_tool_planner":
            call_index = self.planner_calls
            self.planner_calls += 1
            data = (
                self.planner(request, call_index)
                if self.planner is not None
                else dict(request.payload["deterministic_specialist_decision"])
            )
        elif request.prompt_name == "specialist_analysis":
            call_index = self.analysis_calls
            self.analysis_calls += 1
            data = (
                self.analysis(request, call_index)
                if self.analysis is not None
                else dict(request.payload["deterministic_specialist_analysis"])
            )
        else:
            raise AssertionError(f"unexpected prompt: {request.prompt_name}")
        return LLMResponse(data=data, usage=_usage(request))


@dataclass
class RecordingTool:
    tool_name: str
    data: dict[str, Any]
    evidence_id: str
    calls: list[ToolInput] = field(default_factory=list)

    def run(self, tool_input: ToolInput) -> ToolOutput:
        self.calls.append(tool_input)
        evidence = Evidence(
            evidence_id=self.evidence_id,
            source_type="analytics",
            source_id=f"test:{self.tool_name}",
            summary=f"Observed {self.tool_name}.",
        )
        return ToolOutput(
            tool_name=self.tool_name,
            request_id=tool_input.request_id,
            success=True,
            data=dict(self.data),
            evidence_ids=[evidence.evidence_id],
            evidence=[evidence],
        )


def _action(kind: str, agent: str, *, inputs: dict[str, Any] | None = None) -> InvestigationAction:
    return InvestigationAction(
        action_id=f"ACT_{kind}",
        kind=kind,
        agent=agent,
        reason="Collect the next bounded engineering observation.",
        inputs=inputs or {},
    )


def _select_tools(*tool_names: str) -> ResponseFactory:
    def choose(request: LLMRequest, call_index: int) -> dict[str, Any]:
        selected_name = tool_names[call_index]
        candidates = request.payload["tool_candidates"]
        selected = next(
            item for item in candidates if item["tool_name"] == selected_name
        )
        return {
            "decision_id": f"DEC_{call_index + 1}",
            "action_id": request.payload["action_id"],
            "agent": request.payload["agent"],
            "decision_type": "call_tool",
            "reason": f"Select {selected_name} for this bounded test.",
            "candidate_id": selected["candidate_id"],
            "stop_reason": None,
        }

    return choose


def _fdc_context() -> dict[str, Any]:
    return {
        "investigation_intent": "root_cause",
        "lot_ids": ["LOT-01", "LOT-02"],
        "operation_no": "6400",
        "equipment_id": "CMP_01",
        "chamber_id": "CH_A",
    }


def test_qwen_selects_two_different_same_domain_fdc_tools_and_never_gets_third_step() -> None:
    parameter = RecordingTool(
        "analyze_parameter_shift",
        {
            "parameter_summary": [
                {"parameter_name": "carrier_pressure", "avg_delta_percent": -8.0}
            ]
        },
        "EV_PARAM",
    )
    ooc = RecordingTool(
        "find_ooc_events",
        {"event_count": 1, "severity_counts": {"HIGH": 1}, "events": [{}]},
        "EV_OOC",
    )
    basic = RecordingTool(
        "perform_basic_spc_analysis",
        {
            "method": {},
            "spc_results": [],
            "analyzed_parameter_count": 0,
            "ooc_parameter_count": 0,
            "calculated_point_violation_count": 0,
            "baseline_insufficient_parameters": [],
        },
        "EV_BASIC",
    )
    client = ScriptedClient(planner=_select_tools("find_ooc_events", "analyze_parameter_shift"))
    executor = SpecialistV2Executor(
        llm_client=client,
        analyze_parameter_shift_tool=parameter,
        find_ooc_events_tool=ooc,
        perform_basic_spc_analysis_tool=basic,
    )

    finding = executor.execute(
        _action(ActionKind.INSPECT_FDC_SPC.value, AgentKind.FDC.value),
        request_id="REQ_FDC",
        context=_fdc_context(),
    )

    assert [item["tool_name"] for item in finding.details["specialist_v2"]["tool_steps"]] == [
        "find_ooc_events",
        "analyze_parameter_shift",
    ]
    assert finding.details["specialist_v2"]["tool_call_count"] == 2
    assert client.planner_calls == 2
    assert len(parameter.calls) == len(ooc.calls) == 1
    assert basic.calls == []
    assert finding.evidence_ids == ["EV_OOC", "EV_PARAM"]


def test_root_cause_deterministic_fdc_baseline_is_parameter_then_basic_spc() -> None:
    parameter = RecordingTool(
        "analyze_parameter_shift",
        {"parameter_summary": []},
        "EV_PARAM_BASELINE",
    )
    basic = RecordingTool(
        "perform_basic_spc_analysis",
        {
            "method": {"engine": "basic"},
            "spc_results": [],
            "analyzed_parameter_count": 0,
            "ooc_parameter_count": 0,
            "calculated_point_violation_count": 0,
            "baseline_insufficient_parameters": [],
        },
        "EV_BASIC_BASELINE",
    )
    ooc = RecordingTool(
        "find_ooc_events",
        {"event_count": 0, "severity_counts": {}, "events": []},
        "EV_OOC_NOT_SELECTED",
    )
    finding = SpecialistV2Executor(
        llm_client=ScriptedClient(),
        analyze_parameter_shift_tool=parameter,
        perform_basic_spc_analysis_tool=basic,
        find_ooc_events_tool=ooc,
    ).execute(
        _action(ActionKind.INSPECT_FDC_SPC.value, AgentKind.FDC.value),
        request_id="REQ_FDC_BASELINE",
        context=_fdc_context(),
    )

    assert [item["tool_name"] for item in finding.details["specialist_v2"]["tool_steps"]] == [
        "analyze_parameter_shift",
        "perform_basic_spc_analysis",
    ]
    assert len(parameter.calls) == len(basic.calls) == 1
    assert ooc.calls == []


def test_real_llm_mode_skips_model_selection_when_only_one_tool_is_legal() -> None:
    defect = RecordingTool(
        "summarize_defect_wat",
        {
            "lot_ids": ["LOT-01"],
            "defect_counts": {"scratch": 2},
            "defect_patterns": {"linear": 2},
            "wat_fail_modes": {},
            "wat_fail_count": 0,
            "wat_fail_record_count": 0,
            "missing_wat_lot_ids": [],
            "metrology_summaries": [],
            "metrology_fail_count": 0,
        },
        "EV_DEFECT_DIRECT",
    )
    client = ScriptedClient()

    finding = SpecialistV2Executor(
        llm_client=client,
        summarize_defect_wat_tool=defect,
        agent_mode="llm",
        direct_single_candidate=True,
    ).execute(
        _action(
            ActionKind.INSPECT_DEFECT_PATTERN.value,
            AgentKind.DEFECT_WAT.value,
        ),
        request_id="REQ_DIRECT_SINGLE_CANDIDATE",
        context={"source_lot_id": "LOT-01"},
    )

    assert client.planner_calls == 0
    assert client.analysis_calls == 1
    assert len(defect.calls) == 1
    assert (
        finding.details["specialist_v2"][
            "direct_single_candidate_selection_count"
        ]
        == 1
    )


def test_cross_domain_candidate_is_rejected_then_valid_same_domain_retry_runs() -> None:
    defect = RecordingTool(
        "summarize_defect_wat",
        {
            "lot_ids": ["LOT-01"],
            "defect_counts": {"scratch": 2},
            "defect_patterns": {"linear": 2},
            "wat_fail_modes": {},
            "wat_fail_count": 0,
            "wat_fail_record_count": 0,
            "missing_wat_lot_ids": [],
            "metrology_summaries": [],
            "metrology_fail_count": 0,
        },
        "EV_DEFECT",
    )

    def planner(request: LLMRequest, call_index: int) -> dict[str, Any]:
        if call_index == 0:
            candidate_id = "ACT:knowledge:retrieve_similar_case"
        else:
            candidate_id = request.payload["tool_candidates"][0]["candidate_id"]
        return {
            "decision_id": f"DEC_{call_index}",
            "action_id": request.payload["action_id"],
            "agent": request.payload["agent"],
            "decision_type": "call_tool",
            "reason": "Attempt a candidate.",
            "candidate_id": candidate_id,
            "stop_reason": None,
        }

    client = ScriptedClient(planner=planner)
    finding = SpecialistV2Executor(
        llm_client=client,
        summarize_defect_wat_tool=defect,
    ).execute(
        _action(ActionKind.INSPECT_DEFECT_PATTERN.value, AgentKind.DEFECT_WAT.value),
        request_id="REQ_DEFECT",
        context={"lot_id": "LOT-01"},
    )

    assert len(defect.calls) == 1
    assert defect.calls[0].requested_by == AgentKind.DEFECT_WAT.value
    assert finding.details["specialist_v2"]["validation_retry_count"] == 1
    assert finding.details["specialist_v2"]["fallback_reason"] is None


def test_parameter_tampering_is_rejected_twice_and_python_fallback_keeps_trusted_scope() -> None:
    defect = RecordingTool(
        "summarize_defect_wat",
        {
            "lot_ids": ["LOT-01"],
            "defect_counts": {},
            "defect_patterns": {},
            "wat_fail_modes": {},
            "wat_fail_count": 0,
            "wat_fail_record_count": 0,
            "missing_wat_lot_ids": [],
            "metrology_summaries": [],
            "metrology_fail_count": 0,
        },
        "EV_NO_DEFECT",
    )

    def tampered(request: LLMRequest, call_index: int) -> dict[str, Any]:
        decision = dict(request.payload["deterministic_specialist_decision"])
        decision["decision_id"] = f"TAMPER_{call_index}"
        decision["parameters"] = {"lot_ids": ["IMPACT-LOT-AS-NEW-SOURCE"]}
        return decision

    finding = SpecialistV2Executor(
        llm_client=ScriptedClient(planner=tampered),
        summarize_defect_wat_tool=defect,
    ).execute(
        _action(
            ActionKind.INSPECT_DEFECT_PATTERN.value,
            AgentKind.DEFECT_WAT.value,
            inputs={"lot_id": "UNTRUSTED-ACTION-LOT"},
        ),
        request_id="REQ_BOUNDARY",
        context={"source_lot_id": "lot-01"},
    )

    assert defect.calls[0].parameters["lot_ids"] == ["LOT-01"]
    assert finding.details["specialist_v2"]["validation_retry_count"] == 2
    assert (
        finding.details["specialist_v2"]["fallback_reason"]
        == "tool_selection_output_invalid"
    )


def test_impact_lot_only_scope_cannot_replace_fixed_source_lot() -> None:
    defect = RecordingTool(
        "summarize_defect_wat",
        {},
        "EV_MUST_NOT_RUN",
    )
    executor = SpecialistV2Executor(
        llm_client=ScriptedClient(),
        summarize_defect_wat_tool=defect,
    )

    with pytest.raises(SpecialistV2Error) as exc_info:
        executor.execute(
            _action(
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value,
                AgentKind.DEFECT_WAT.value,
            ),
            request_id="REQ_SOURCE_BOUNDARY",
            context={
                "source_lot_id": "LOT-01",
                "lot_ids": ["IMPACT-02", "IMPACT-03"],
            },
        )

    assert exc_info.value.stage == "candidate_generation"
    assert exc_info.value.reason == "source_lot_scope_violation"
    assert defect.calls == []


def test_analysis_evidence_must_be_exact_closure_or_python_analysis_is_used() -> None:
    knowledge = RecordingTool(
        "retrieve_similar_case",
        {
            "query": "scratch cu cmp",
            "cases": [
                {
                    "case_id": "CASE-01",
                    "similarity": 0.8,
                    "root_cause": "pad debris",
                }
            ],
            "top_case": {
                "case_id": "CASE-01",
                "similarity": 0.8,
                "root_cause": "pad debris",
            },
            "documents": [],
        },
        "EV_KNOWLEDGE",
    )

    def invented_analysis(request: LLMRequest, call_index: int) -> dict[str, Any]:
        deterministic = dict(request.payload["deterministic_specialist_analysis"])
        deterministic["summary"] = f"Invented analysis attempt {call_index}."
        deterministic["confidence"] = 1.0
        deterministic["evidence_ids"] = ["EV_KNOWLEDGE", "EV_INVENTED"]
        return deterministic

    finding = SpecialistV2Executor(
        llm_client=ScriptedClient(analysis=invented_analysis),
        retrieve_similar_case_tool=knowledge,
    ).execute(
        _action(
            ActionKind.VALIDATE_HISTORICAL_CASE.value,
            AgentKind.KNOWLEDGE.value,
        ),
        request_id="REQ_KNOWLEDGE",
        context={
            "query": "scratch cu cmp",
            "module": "cu_cmp",
            "equipment_type": "cmp",
        },
    )

    assert finding.evidence_ids == ["EV_KNOWLEDGE"]
    assert finding.confidence == 0.8
    assert "Invented" not in finding.summary
    assert finding.details["specialist_v2"]["analysis_source"] == "deterministic_fallback"
    assert finding.details["specialist_v2"]["fallback_reason"] == "analysis_output_invalid"


def test_advanced_spc_without_parameters_falls_back_to_basic_inside_two_call_limit() -> None:
    advanced = RecordingTool(
        "analyze_spc_evidence",
        {
            "method": {"engine": "advanced"},
            "spc_results": [],
            "analyzed_parameter_count": 0,
            "ooc_parameter_count": 0,
            "calculated_point_violation_count": 0,
            "baseline_insufficient_parameters": ["carrier_pressure"],
        },
        "EV_ADVANCED_MISSING",
    )
    basic = RecordingTool(
        "perform_basic_spc_analysis",
        {
            "method": {"engine": "basic"},
            "spc_results": [{"parameter_name": "carrier_pressure", "status": "OOC"}],
            "analyzed_parameter_count": 1,
            "ooc_parameter_count": 1,
            "calculated_point_violation_count": 1,
            "baseline_insufficient_parameters": [],
        },
        "EV_BASIC_SELECTED",
    )
    client = ScriptedClient(planner=_select_tools("analyze_spc_evidence"))
    finding = SpecialistV2Executor(
        llm_client=client,
        analyze_spc_evidence_tool=advanced,
        perform_basic_spc_analysis_tool=basic,
    ).execute(
        _action(ActionKind.INSPECT_FDC_SPC.value, AgentKind.FDC.value),
        request_id="REQ_SPC",
        context={**_fdc_context(), "investigation_intent": "spc_check"},
    )

    assert len(advanced.calls) == len(basic.calls) == 1
    assert finding.details["specialist_v2"]["tool_call_count"] == 2
    assert finding.evidence_ids == ["EV_BASIC_SELECTED"]
    assert "EV_ADVANCED_MISSING" not in finding.evidence_ids
    assert finding.details["spc_method"] == {"engine": "basic"}
    assert finding.details["specialist_v2"]["superseded_step_ids"] == [
        f"ACT_{ActionKind.INSPECT_FDC_SPC.value}:specialist-step:1"
    ]


def test_mes_lot_executes_context_then_impact_and_derives_commonality() -> None:
    context_tool = RecordingTool(
        "get_lot_context",
        {
            "lot_id": "LOT-01",
            "product_id": "PROD-A",
            "route_id": "ROUTE-A",
            "lot": {"lot_id": "LOT-01"},
            "wat_failed": True,
            "fail_modes": ["THK_LOW"],
            "recipe_changes": [],
            "hold_records": [],
        },
        "EV_LOT_CONTEXT",
    )
    impact_tool = RecordingTool(
        "find_impact_lots",
        {
            "source_lot_id": "LOT-01",
            "affected_lots": ["LOT-01", "LOT-02"],
            "impact_lots": ["LOT-02"],
            "affected_wafers": ["LOT-01_W01", "LOT-02_W01"],
            "impact_wafers": ["LOT-02_W01"],
            "scope_level": "mixed",
            "target_operation_no": "6400",
            "source_exposure": {
                "equipment_id": "CMP_01",
                "chamber_id": "CH_A",
                "recipe_id": "RCP_01",
                "module": "cu_cmp",
            },
            "impact_criteria": {"selection_rule": "same chamber and overlapping window"},
        },
        "EV_IMPACT",
    )
    finding = SpecialistV2Executor(
        llm_client=ScriptedClient(),
        get_lot_context_tool=context_tool,
        find_impact_lots_tool=impact_tool,
    ).execute(
        _action(ActionKind.FIND_SHARED_EXPOSURE.value, AgentKind.MES.value),
        request_id="REQ_MES",
        context={"source_lot_id": "lot-01"},
    )

    assert len(context_tool.calls) == len(impact_tool.calls) == 1
    assert finding.details["specialist_v2"]["tool_call_count"] == 2
    assert finding.details["target_commonality"] == {
        "equipment_id": "CMP_01",
        "chamber_id": "CH_A",
        "recipe_id": "RCP_01",
        "lot_count": 2,
        "wafer_count": 2,
        "coverage": 1.0,
        "wafer_coverage": 1.0,
        "derivation": "find_impact_lots.source_exposure",
    }
    assert finding.details["impact_lots"] == ["LOT-02"]


def test_mes_lot_cannot_finish_after_context_and_retry_receives_validation_error() -> None:
    context_tool = RecordingTool(
        "get_lot_context",
        {
            "lot_id": "LOT-01",
            "product_id": "PROD-A",
            "route_id": "ROUTE-A",
            "lot": {"lot_id": "LOT-01"},
            "wat_failed": False,
            "fail_modes": [],
            "recipe_changes": [],
            "hold_records": [],
        },
        "EV_CONTEXT_ONLY",
    )
    impact_tool = RecordingTool(
        "find_impact_lots",
        {
            "source_lot_id": "LOT-01",
            "affected_lots": ["LOT-01"],
            "impact_lots": [],
            "affected_wafers": ["LOT-01_W01"],
            "impact_wafers": [],
            "scope_level": "wafer",
            "target_operation_no": "6400",
            "source_exposure": {
                "equipment_id": "CMP_01",
                "chamber_id": "CH_A",
                "recipe_id": "RCP_01",
            },
            "impact_criteria": {},
        },
        "EV_IMPACT_REQUIRED",
    )

    def planner(request: LLMRequest, call_index: int) -> dict[str, Any]:
        if call_index == 0:
            return dict(request.payload["deterministic_specialist_decision"])
        if call_index == 1:
            assert request.payload["validation_errors"] == []
            return {
                "decision_id": "DEC_PREMATURE_FINISH",
                "action_id": request.payload["action_id"],
                "agent": request.payload["agent"],
                "decision_type": "finish",
                "reason": "Context alone is enough.",
                "candidate_id": None,
                "stop_reason": "sufficient_evidence",
            }
        assert request.payload["validation_errors"]
        assert "cannot finish" in request.payload["validation_errors"][0]
        return dict(request.payload["deterministic_specialist_decision"])

    client = ScriptedClient(planner=planner)
    finding = SpecialistV2Executor(
        llm_client=client,
        get_lot_context_tool=context_tool,
        find_impact_lots_tool=impact_tool,
    ).execute(
        _action(ActionKind.FIND_SHARED_EXPOSURE.value, AgentKind.MES.value),
        request_id="REQ_MES_FINISH",
        context={"source_lot_id": "LOT-01"},
    )

    assert client.planner_calls == 3
    assert len(context_tool.calls) == len(impact_tool.calls) == 1
    assert finding.evidence_ids == ["EV_CONTEXT_ONLY", "EV_IMPACT_REQUIRED"]
    assert finding.details["specialist_v2"]["validation_retry_count"] == 1
