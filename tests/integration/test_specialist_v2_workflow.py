from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import ActionRecord  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
)
from yield_rca_core.models import AgentFinding, RCAState  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
SPC_SEED_DIR = ROOT / "data" / "seeds" / "spc_case"
ROOT_CAUSE_QUERY = "Investigate the root cause of LOT_A_001 scratch in Cu CMP."
FIXED_QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


def _request_action(request: LLMRequest) -> dict[str, Any]:
    raw_action = request.payload.get("action", {})
    return raw_action if isinstance(raw_action, dict) else {}


def _request_agent(request: LLMRequest) -> str:
    raw_agent = request.payload.get("agent")
    if isinstance(raw_agent, str):
        return raw_agent
    action = _request_action(request)
    return str(action.get("agent", ""))


def _request_action_id(request: LLMRequest) -> str:
    raw_action_id = request.payload.get("action_id")
    if isinstance(raw_action_id, str):
        return raw_action_id
    return str(_request_action(request).get("action_id", ""))


def _request_candidates(request: LLMRequest) -> list[dict[str, Any]]:
    for key in ("candidates", "available_candidates", "tool_candidates"):
        raw_candidates = request.payload.get(key)
        if isinstance(raw_candidates, list):
            return [
                candidate
                for candidate in raw_candidates
                if isinstance(candidate, dict)
            ]
    raise AssertionError("Specialist Tool planner request did not expose candidates")


def _select_candidate(
    response: LLMResponse,
    request: LLMRequest,
    *,
    tool_name: str,
) -> LLMResponse:
    candidate = next(
        (
            item
            for item in _request_candidates(request)
            if item.get("tool_name") == tool_name
        ),
        None,
    )
    if candidate is None:
        available = [
            str(item.get("tool_name")) for item in _request_candidates(request)
        ]
        raise AssertionError(
            f"{tool_name!r} is not an advertised candidate; got {available}"
        )
    data = dict(response.data)
    data.update(
        {
            "decision_type": "call_tool",
            "reason": f"Integration test selects the legal {tool_name} candidate.",
            "candidate_id": candidate["candidate_id"],
            "stop_reason": None,
        }
    )
    return LLMResponse(data=data, usage=response.usage)


class RecordingSpecialistClient(FakeLLMClient):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return super().complete_json(request)


class SelectFDCToolsClient(RecordingSpecialistClient):
    """Make two legal, model-owned FDC choices while Python owns parameters."""

    def __init__(self, *tool_names: str) -> None:
        super().__init__()
        self.tool_names = tool_names
        self.calls_by_action: defaultdict[str, int] = defaultdict(int)

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if (
            request.prompt_name != "specialist_tool_planner"
            or _request_agent(request) != "fdc"
        ):
            return response
        action_id = _request_action_id(request)
        selection_index = self.calls_by_action[action_id]
        if selection_index >= len(self.tool_names):
            return response
        self.calls_by_action[action_id] += 1
        return _select_candidate(
            response,
            request,
            tool_name=self.tool_names[selection_index],
        )


class InvalidMESDecisionAfterObservationClient(RecordingSpecialistClient):
    """Keep one successful MES observation, then fail both parse attempts."""

    def __init__(self) -> None:
        super().__init__()
        self.mes_planner_calls = 0

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if (
            request.prompt_name != "specialist_tool_planner"
            or _request_agent(request) != "mes"
        ):
            return response
        self.mes_planner_calls += 1
        if self.mes_planner_calls == 1:
            return _select_candidate(
                response,
                request,
                tool_name="get_lot_context",
            )
        data = dict(response.data)
        data.update(
            {
                "decision_type": "call_tool",
                "reason": "Attempt an unregistered cross-domain candidate.",
                "candidate_id": "FDC_CROSS_DOMAIN_NOT_ADVERTISED",
                "stop_reason": None,
            }
        )
        return LLMResponse(data=data, usage=response.usage)


class PrematureMESFinishAfterContextClient(RecordingSpecialistClient):
    """Try to stop MES before the required impact-scope observation."""

    def __init__(self) -> None:
        super().__init__()
        self.mes_planner_calls = 0

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if (
            request.prompt_name != "specialist_tool_planner"
            or _request_agent(request) != "mes"
        ):
            return response
        self.mes_planner_calls += 1
        if self.mes_planner_calls == 1:
            return _select_candidate(
                response,
                request,
                tool_name="get_lot_context",
            )
        data = dict(response.data)
        data.update(
            {
                "decision_type": "finish",
                "reason": (
                    "The source Lot context alone is sufficient; skip impact scope."
                ),
                "candidate_id": None,
                "stop_reason": "model_claimed_sufficient_evidence",
            }
        )
        return LLMResponse(data=data, usage=response.usage)


def _run_llm_react(
    client: FakeLLMClient,
    *,
    job_id: str,
    seed_dir: Path = GOLDEN_SEED_DIR,
) -> RCAState:
    workflow = build_csv_workflow(
        seed_dir,
        llm_settings=LLMSettings(agent_mode="fake"),
        llm_client=client,
        orchestration_mode="llm_react",
    )
    return workflow.run(
        ROOT_CAUSE_QUERY,
        job_id=job_id,
        lot_id="LOT_A_001",
    )


def _finding_for_action(
    state: RCAState,
    action_record: ActionRecord,
) -> AgentFinding:
    finding_id = action_record.produced_finding_ids[0]
    return next(
        finding for finding in state.findings if finding.finding_id == finding_id
    )


def _tool_names(state: Any) -> list[tuple[str, str]]:
    return [
        (str(item["agent"]), str(item["tool_name"]))
        for item in state.execution_metadata["tool_latencies"]
    ]


def _write_non_matching_spc_profile(seed_dir: Path) -> None:
    source_path = SPC_SEED_DIR / "spc_baseline_profile.csv"
    with source_path.open(encoding="utf-8", newline="") as source_file:
        reader = csv.DictReader(source_file)
        row = dict(next(reader))
        fieldnames = list(reader.fieldnames or [])
    row["baseline_id"] = "SPC_BASE_NON_MATCHING_INTEGRATION"
    row["operation_no"] = "9999"
    target_path = seed_dir / "spc_baseline_profile.csv"
    with target_path.open("w", encoding="utf-8", newline="") as target_file:
        writer = csv.DictWriter(target_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


class SpecialistV2WorkflowIntegrationTest(unittest.TestCase):
    def test_scratch_cu_cmp_replans_with_bounded_specialist_traces(self) -> None:
        client = RecordingSpecialistClient()

        state = _run_llm_react(
            client,
            job_id="JOB_SPECIALIST_V2_REPLAN",
        )

        planner_request_indexes = [
            index
            for index, request in enumerate(client.requests)
            if request.prompt_name == "next_action_planner"
        ]
        specialist_request_indexes = [
            index
            for index, request in enumerate(client.requests)
            if request.prompt_name == "specialist_tool_planner"
        ]
        self.assertEqual(
            len(planner_request_indexes),
            len(state.action_history) + 1,
        )
        self.assertTrue(
            planner_request_indexes[0]
            < specialist_request_indexes[0]
            < planner_request_indexes[1]
        )
        second_planner_request = client.requests[planner_request_indexes[1]]
        self.assertEqual(len(second_planner_request.payload["findings"]), 1)
        self.assertEqual(
            second_planner_request.payload["findings"][0]["agent"],
            "defect_wat",
        )

        tool_latencies = state.execution_metadata["tool_latencies"]
        for record in state.action_history:
            if record.action.agent == "rca_reasoning":
                continue
            finding = _finding_for_action(state, record)
            trace = finding.details["specialist_v2"]
            steps = trace["tool_steps"]
            self.assertEqual(trace["version"], "v2")
            self.assertGreaterEqual(len(steps), 1)
            self.assertLessEqual(len(steps), 2)
            self.assertTrue(trace["stop_reason"])
            self.assertEqual(finding.details["agent_mode"], "fake")
            self.assertTrue(finding.details["engineering_interpretation"])
            self.assertTrue(
                all(step["action_id"] == record.action.action_id for step in steps)
            )

            request_prefix = f"{state.job.job_id}:{record.action.action_id}"
            action_tool_calls = [
                latency
                for latency in tool_latencies
                if str(latency["tool_request_id"]).startswith(request_prefix)
            ]
            self.assertLessEqual(len(action_tool_calls), 2)
            self.assertEqual(
                [item["tool_name"] for item in action_tool_calls],
                [step["tool_name"] for step in steps],
            )

        fdc = next(finding for finding in state.findings if finding.agent == "fdc")
        self.assertEqual(
            [
                step["tool_name"]
                for step in fdc.details["specialist_v2"]["tool_steps"]
            ],
            ["analyze_parameter_shift", "perform_basic_spc_analysis"],
        )

    def test_fixed_and_controlled_react_keep_legacy_tool_paths(self) -> None:
        fixed = build_csv_workflow(
            GOLDEN_SEED_DIR,
            orchestration_mode="fixed",
        ).run(
            FIXED_QUERY,
            job_id="JOB_SPECIALIST_V2_FIXED_BASELINE",
        )
        controlled = build_csv_workflow(
            GOLDEN_SEED_DIR,
            orchestration_mode="controlled_react",
        ).run(
            ROOT_CAUSE_QUERY,
            job_id="JOB_SPECIALIST_V2_CONTROLLED_BASELINE",
            lot_id="LOT_A_001",
        )

        self.assertEqual(
            _tool_names(fixed),
            [
                ("mes", "find_affected_lots"),
                ("mes", "analyze_lot_genealogy"),
                ("fdc", "analyze_parameter_shift"),
                ("fdc", "find_ooc_events"),
                ("fdc", "perform_basic_spc_analysis"),
                ("defect_wat", "summarize_defect_wat"),
                ("knowledge", "retrieve_similar_case"),
                ("knowledge", "retrieve_similar_case"),
            ],
        )
        self.assertEqual(
            [record.action.kind for record in controlled.action_history],
            [
                "inspect_defect_pattern",
                "find_shared_exposure",
                "validate_shared_defect_pattern",
                "inspect_fdc_spc",
                "run_rca_reasoning",
            ],
        )
        self.assertEqual(
            _tool_names(controlled),
            [
                ("defect_wat", "summarize_defect_wat"),
                ("mes", "get_lot_context"),
                ("mes", "find_impact_lots"),
                ("mes", "analyze_lot_genealogy"),
                ("defect_wat", "summarize_defect_wat"),
                ("fdc", "analyze_parameter_shift"),
                ("fdc", "find_ooc_events"),
                ("fdc", "perform_basic_spc_analysis"),
            ],
        )
        for state in (fixed, controlled):
            self.assertTrue(
                all(
                    "specialist_v2" not in finding.details
                    for finding in state.findings
                )
            )

    def test_invalid_mes_output_falls_back_locally_without_replaying_tool(
        self,
    ) -> None:
        client = InvalidMESDecisionAfterObservationClient()

        state = _run_llm_react(
            client,
            job_id="JOB_SPECIALIST_V2_LOCAL_FALLBACK",
        )

        self.assertEqual(
            state.execution_metadata["orchestration_mode"],
            "llm_react",
        )
        self.assertNotIn(
            "orchestration_fallback_reason",
            state.execution_metadata,
        )
        mes_record = next(
            record
            for record in state.action_history
            if record.action.agent == "mes"
        )
        mes_finding = _finding_for_action(state, mes_record)
        trace = mes_finding.details["specialist_v2"]
        self.assertEqual(trace["analysis_source"], "qwen")
        self.assertIn(
            "tool_selection_output_invalid",
            trace["fallback_reason"],
        )
        self.assertEqual(trace["validation_retry_count"], 2)

        request_prefix = f"{state.job.job_id}:{mes_record.action.action_id}"
        mes_tool_calls = [
            item
            for item in state.execution_metadata["tool_latencies"]
            if str(item["tool_request_id"]).startswith(request_prefix)
        ]
        self.assertEqual(
            sum(
                item["tool_name"] == "get_lot_context"
                for item in mes_tool_calls
            ),
            1,
        )
        self.assertLessEqual(len(mes_tool_calls), 2)
        self.assertEqual(
            len({step["step_id"] for step in trace["tool_steps"]}),
            len(trace["tool_steps"]),
        )

    def test_mes_cannot_finish_after_context_without_impact_scope(self) -> None:
        client = PrematureMESFinishAfterContextClient()

        state = _run_llm_react(
            client,
            job_id="JOB_SPECIALIST_V2_PREMATURE_MES_FINISH",
        )

        mes_record = next(
            record
            for record in state.action_history
            if record.action.agent == "mes"
        )
        mes_finding = _finding_for_action(state, mes_record)
        trace = mes_finding.details["specialist_v2"]
        self.assertEqual(
            [step["tool_name"] for step in trace["tool_steps"]],
            ["get_lot_context", "find_impact_lots"],
        )
        self.assertEqual(client.mes_planner_calls, 3)
        self.assertIn(
            "tool_selection_output_invalid",
            trace["fallback_reason"],
        )
        self.assertEqual(trace["validation_retry_count"], 2)
        self.assertTrue(mes_finding.details["impact_lots"])
        rca_finding = state.authoritative_rca_finding
        self.assertIsNotNone(rca_finding)
        impact_gate = rca_finding.details["impact_lot_gate"]
        self.assertEqual(
            state.impact_lots,
            impact_gate["confirmed_impact_lots"],
        )

        request_prefix = f"{state.job.job_id}:{mes_record.action.action_id}"
        mes_tool_names = [
            str(item["tool_name"])
            for item in state.execution_metadata["tool_latencies"]
            if str(item["tool_request_id"]).startswith(request_prefix)
        ]
        self.assertEqual(
            mes_tool_names,
            ["get_lot_context", "find_impact_lots"],
        )

    def test_advanced_spc_fallback_keeps_only_selected_basic_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_seed_dir = Path(temporary_directory) / "golden_with_profile"
            shutil.copytree(GOLDEN_SEED_DIR, copied_seed_dir)
            _write_non_matching_spc_profile(copied_seed_dir)
            state = _run_llm_react(
                SelectFDCToolsClient(
                    "analyze_spc_evidence",
                    "perform_basic_spc_analysis",
                ),
                job_id="JOB_SPECIALIST_V2_ADVANCED_BASIC",
                seed_dir=copied_seed_dir,
            )

        fdc = next(finding for finding in state.findings if finding.agent == "fdc")
        trace = fdc.details["specialist_v2"]
        steps_by_tool = {
            step["tool_name"]: step for step in trace["tool_steps"]
        }
        advanced_step = steps_by_tool["analyze_spc_evidence"]
        basic_step = steps_by_tool["perform_basic_spc_analysis"]

        self.assertEqual(len(trace["tool_steps"]), 2)
        self.assertIn(
            advanced_step["step_id"],
            trace["superseded_step_ids"],
        )
        self.assertTrue(advanced_step["evidence_ids"])
        self.assertTrue(
            set(advanced_step["evidence_ids"]).isdisjoint(fdc.evidence_ids)
        )
        self.assertTrue(set(basic_step["evidence_ids"]) <= set(fdc.evidence_ids))
        self.assertNotIn(
            "WARN_SPC_PROFILE_NOT_FOUND",
            {warning.warning_id for warning in fdc.warnings},
        )

    def test_qwen_legal_fdc_tool_choices_are_respected(self) -> None:
        parameter_and_ooc = _run_llm_react(
            SelectFDCToolsClient(
                "analyze_parameter_shift",
                "find_ooc_events",
            ),
            job_id="JOB_SPECIALIST_V2_FDC_OOC",
        )
        parameter_and_spc = _run_llm_react(
            SelectFDCToolsClient(
                "analyze_parameter_shift",
                "perform_basic_spc_analysis",
            ),
            job_id="JOB_SPECIALIST_V2_FDC_SPC",
        )

        observed_paths: list[list[str]] = []
        for state in (parameter_and_ooc, parameter_and_spc):
            fdc = next(
                finding for finding in state.findings if finding.agent == "fdc"
            )
            path = [
                step["tool_name"]
                for step in fdc.details["specialist_v2"]["tool_steps"]
            ]
            observed_paths.append(path)
            self.assertLessEqual(len(path), 2)
            self.assertEqual(
                [
                    item["tool_name"]
                    for item in state.execution_metadata["tool_latencies"]
                    if item["agent"] == "fdc"
                ],
                path,
            )

        self.assertEqual(
            observed_paths,
            [
                ["analyze_parameter_shift", "find_ooc_events"],
                ["analyze_parameter_shift", "perform_basic_spc_analysis"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
