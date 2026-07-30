from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_core.investigation_models import (  # noqa: E402
    ConclusionLevel,
    DecisionType,
    StopReason,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
)
from yield_rca_core.models import RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
ROOT_CAUSE_QUERY = (
    "Investigate the root cause of LOT_A_001 scratch in Cu CMP."
)
IMPACT_QUERY = "Identify the impact lots for LOT_A_001."
PRODUCT_IMPACT_QUERY = (
    "Identify impact lots for 40N_SOC from 2026-07-01 to 2026-07-31."
)
PRODUCT_ROOT_QUERY = (
    "Investigate 40N_SOC yield loss root cause from 2026-07-01 to 2026-07-31."
)
PRODUCT_HISTORY_QUERY = (
    "Find a similar historical case for 40N_SOC from 2026-07-01 to 2026-07-31."
)


class RecordingFakeClient(FakeLLMClient):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return super().complete_json(request)


class InvalidNextActionAfterFirstClient(RecordingFakeClient):
    """Run one real observation, then fail both structured-output attempts."""

    def __init__(self) -> None:
        super().__init__()
        self.next_action_call_count = 0

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "next_action_planner":
            return response
        self.next_action_call_count += 1
        if self.next_action_call_count == 1:
            return response
        return LLMResponse(data={}, usage=response.usage)


class InvalidIntentClient(RecordingFakeClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name == "intent_planner":
            return LLMResponse(data={}, usage=response.usage)
        return response


class ImmediateUnsupportedStopClient(RecordingFakeClient):
    """Model proposes a supported conclusion before collecting any Evidence."""

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "next_action_planner":
            return response
        goal_id = str(request.payload["goal"]["goal_id"])
        return LLMResponse(
            data={
                "decision_id": f"{goal_id}:model-stop",
                "goal_id": goal_id,
                "decision_type": DecisionType.STOP.value,
                "reason": "The model cannot obtain the requested source data.",
                "goal_status": "blocked",
                "proposed_conclusion_level": ConclusionLevel.SUPPORTED.value,
                "next_action": None,
                "target_question_ids": [],
                "new_questions": [],
                "stop_reason": StopReason.DATA_UNAVAILABLE.value,
                "question_updates": [],
            },
            usage=response.usage,
        )


def fake_llm_workflow(client: FakeLLMClient):
    return build_csv_workflow(
        SEED_DIR,
        llm_settings=LLMSettings(agent_mode="fake"),
        llm_client=client,
        orchestration_mode="llm_react",
    )


def run_lot(
    client: FakeLLMClient,
    query: str,
    *,
    job_id: str,
) -> RCAState:
    return fake_llm_workflow(client).run(
        query,
        job_id=job_id,
        lot_id="LOT_A_001",
    )


class LLMReactWorkflowIntegrationTest(unittest.TestCase):
    def test_fake_qwen_intents_produce_different_bounded_action_chains(self) -> None:
        impact = run_lot(
            RecordingFakeClient(),
            IMPACT_QUERY,
            job_id="JOB_LLM_REACT_IMPACT",
        )
        root_cause = run_lot(
            RecordingFakeClient(),
            ROOT_CAUSE_QUERY,
            job_id="JOB_LLM_REACT_ROOT_CAUSE",
        )

        self.assertEqual(impact.investigation_goal.intent, "impact_scope")
        self.assertEqual(
            [record.action.kind for record in impact.action_history],
            ["find_shared_exposure"],
        )
        self.assertEqual(root_cause.investigation_goal.intent, "root_cause")
        self.assertEqual(
            [record.action.kind for record in root_cause.action_history],
            [
                "inspect_defect_pattern",
                "find_shared_exposure",
                "validate_shared_defect_pattern",
                "inspect_fdc_spc",
                "run_rca_reasoning",
            ],
        )
        self.assertNotEqual(
            [record.action.kind for record in impact.action_history],
            [record.action.kind for record in root_cause.action_history],
        )

    def test_scratch_cu_cmp_replans_after_observation_and_keeps_auditable_links(
        self,
    ) -> None:
        client = RecordingFakeClient()

        state = run_lot(
            client,
            ROOT_CAUSE_QUERY,
            job_id="JOB_LLM_REACT_REPLAN",
        )

        planner_requests = [
            request
            for request in client.requests
            if request.prompt_name == "next_action_planner"
        ]
        self.assertEqual(len(planner_requests), len(state.action_history) + 1)
        self.assertEqual(planner_requests[0].payload["findings"], [])
        self.assertEqual(planner_requests[0].payload["action_history"], [])
        self.assertEqual(len(planner_requests[1].payload["findings"]), 1)
        self.assertEqual(
            planner_requests[1].payload["findings"][0]["agent"],
            "defect_wat",
        )
        self.assertEqual(len(planner_requests[1].payload["action_history"]), 1)
        self.assertTrue(planner_requests[1].payload["available_evidence_ids"])

        act_decisions = [
            decision
            for decision in state.planner_decisions
            if decision.decision_type == DecisionType.ACT.value
        ]
        self.assertEqual(len(act_decisions), len(state.action_history))
        finding_ids = {finding.finding_id for finding in state.findings}
        evidence_ids = {evidence.evidence_id for evidence in state.evidence}
        for decision, record in zip(act_decisions, state.action_history, strict=True):
            self.assertIsNotNone(decision.next_action)
            self.assertEqual(
                decision.next_action.action_id,
                record.action.action_id,
            )
            self.assertEqual(
                decision.next_action.kind,
                record.action.kind,
            )
            self.assertTrue(set(record.produced_finding_ids) <= finding_ids)
            self.assertTrue(set(record.produced_evidence_ids) <= evidence_ids)

        self.assertEqual(
            state.planner_decisions[-1].decision_type,
            DecisionType.STOP.value,
        )
        self.assertEqual(RCAState.from_dict(state.to_dict()), state)

    def test_qwen_stop_is_downgraded_by_evidence_gate_without_failing_run(
        self,
    ) -> None:
        state = run_lot(
            ImmediateUnsupportedStopClient(),
            ROOT_CAUSE_QUERY,
            job_id="JOB_LLM_REACT_GATED_STOP",
        )

        self.assertEqual(state.job.status, TaskStatus.COMPLETED.value)
        self.assertEqual(state.action_history, [])
        self.assertEqual(state.evidence, [])
        self.assertEqual(
            state.planner_decisions[-1].proposed_conclusion_level,
            ConclusionLevel.SUPPORTED.value,
        )
        self.assertEqual(
            state.conclusion_level,
            ConclusionLevel.INCONCLUSIVE.value,
        )
        self.assertEqual(state.stop_reason, StopReason.DATA_UNAVAILABLE.value)
        self.assertTrue(state.report is None or state.report.markdown)

    def test_two_invalid_next_actions_fallback_from_current_state(self) -> None:
        client = InvalidNextActionAfterFirstClient()

        state = run_lot(
            client,
            ROOT_CAUSE_QUERY,
            job_id="JOB_LLM_REACT_MID_LOOP_FALLBACK",
        )

        self.assertEqual(client.next_action_call_count, 3)
        self.assertEqual(
            state.execution_metadata["orchestration_requested_mode"],
            "llm_react",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_mode"],
            "controlled_react",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_reason"],
            "qwen_next_action_output_invalid",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_stage"],
            "next_action_planning",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_after_action_count"],
            1,
        )
        self.assertEqual(len(state.planner_decisions), 1)
        self.assertEqual(
            state.planner_decisions[0].next_action.action_id,
            state.action_history[0].action.action_id,
        )
        self.assertTrue(state.action_history[0].produced_evidence_ids)
        self.assertTrue(
            set(state.action_history[0].produced_evidence_ids)
            <= {item.evidence_id for item in state.evidence}
        )
        self.assertEqual(
            [record.action.kind for record in state.action_history],
            [
                "inspect_defect_pattern",
                "find_shared_exposure",
                "validate_shared_defect_pattern",
                "inspect_fdc_spc",
                "run_rca_reasoning",
            ],
        )
        self.assertTrue(state.investigation_questions)
        self.assertTrue(
            all(
                question.status == "closed"
                for question in state.investigation_questions
            )
        )
        self.assertEqual(state.evidence_gaps, [])

    def test_two_invalid_intent_outputs_fallback_before_first_action(self) -> None:
        client = InvalidIntentClient()

        state = run_lot(
            client,
            ROOT_CAUSE_QUERY,
            job_id="JOB_LLM_REACT_INTENT_FALLBACK",
        )

        intent_requests = [
            request
            for request in client.requests
            if request.prompt_name == "intent_planner"
        ]
        self.assertEqual(len(intent_requests), 2)
        self.assertEqual(state.planner_decisions, [])
        self.assertEqual(
            state.execution_metadata["orchestration_requested_mode"],
            "llm_react",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_mode"],
            "controlled_react",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_reason"],
            "qwen_intent_output_invalid",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_stage"],
            "intent_planning",
        )
        self.assertEqual(
            state.execution_metadata["orchestration_fallback_after_action_count"],
            0,
        )
        self.assertEqual(
            state.action_history[0].action.kind,
            "inspect_defect_pattern",
        )

    def test_product_root_cause_and_history_use_mes_selected_lots(self) -> None:
        workflow = fake_llm_workflow(RecordingFakeClient())

        root_cause = workflow.run(
            PRODUCT_ROOT_QUERY,
            job_id="JOB_LLM_REACT_PRODUCT_ROOT",
        )
        history = workflow.run(
            PRODUCT_HISTORY_QUERY,
            job_id="JOB_LLM_REACT_PRODUCT_HISTORY",
        )

        self.assertEqual(root_cause.job.investigation_mode, "product_window")
        self.assertEqual(root_cause.job.source_lot_id, None)
        self.assertEqual(root_cause.action_history[0].action.kind, "find_shared_exposure")
        self.assertIn(
            "inspect_defect_pattern",
            [record.action.kind for record in root_cause.action_history],
        )
        defect = next(
            finding
            for finding in root_cause.findings
            if finding.agent == "defect_wat"
        )
        self.assertTrue(defect.details["lot_ids"])
        self.assertEqual(root_cause.job.status, TaskStatus.COMPLETED.value)

        self.assertEqual(history.job.investigation_mode, "product_window")
        self.assertIn(
            "validate_historical_case",
            [record.action.kind for record in history.action_history],
        )
        self.assertEqual(history.job.status, TaskStatus.COMPLETED.value)


class LLMReactAPIIntegrationTest(unittest.TestCase):
    def test_api_accepts_non_scratch_lot_and_product_window_in_llm_mode(self) -> None:
        app = create_app(
            workflow=fake_llm_workflow(RecordingFakeClient()),
            runtime_dataset="golden_case",
        )

        with TestClient(app) as client:
            ready = client.get("/ready")
            lot_created = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "lot",
                    "lot_id": "LOT_A_001",
                    "user_query": IMPACT_QUERY,
                },
            )
            product_created = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "product_window",
                    "user_query": PRODUCT_IMPACT_QUERY,
                },
            )
            product_root_created = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "product_window",
                    "user_query": PRODUCT_ROOT_QUERY,
                },
            )

            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["orchestration_mode"], "llm_react")
            self.assertEqual(lot_created.status_code, 201)
            self.assertEqual(product_created.status_code, 201)
            self.assertEqual(product_root_created.status_code, 201)

            for created in (
                lot_created.json(),
                product_created.json(),
                product_root_created.json(),
            ):
                state_response = client.get(created["state_url"])
                self.assertEqual(state_response.status_code, 200)
                metadata = state_response.json()["state"]["execution_metadata"]
                self.assertEqual(
                    metadata["orchestration_requested_mode"],
                    "llm_react",
                )
                self.assertEqual(metadata["orchestration_mode"], "llm_react")
                self.assertNotIn("orchestration_fallback_reason", metadata)

    def test_api_exposes_mid_loop_fallback_metadata(self) -> None:
        app = create_app(
            workflow=fake_llm_workflow(InvalidNextActionAfterFirstClient()),
            runtime_dataset="golden_case",
        )

        with TestClient(app) as client:
            created = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "lot",
                    "lot_id": "LOT_A_001",
                    "user_query": ROOT_CAUSE_QUERY,
                },
            )
            self.assertEqual(created.status_code, 201)
            state = client.get(created.json()["state_url"]).json()["state"]

        metadata = state["execution_metadata"]
        self.assertEqual(metadata["orchestration_requested_mode"], "llm_react")
        self.assertEqual(metadata["orchestration_mode"], "controlled_react")
        self.assertEqual(
            metadata["orchestration_fallback_reason"],
            "qwen_next_action_output_invalid",
        )
        self.assertEqual(
            metadata["orchestration_fallback_after_action_count"],
            1,
        )
        self.assertEqual(len(state["planner_decisions"]), 1)
        self.assertTrue(state["evidence"])

    def test_api_immediate_stop_is_completed_and_never_returns_500(self) -> None:
        app = create_app(
            workflow=fake_llm_workflow(ImmediateUnsupportedStopClient()),
            runtime_dataset="golden_case",
        )

        with TestClient(app) as client:
            created = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "lot",
                    "lot_id": "LOT_A_001",
                    "user_query": ROOT_CAUSE_QUERY,
                },
            )
            self.assertEqual(created.status_code, 201)
            state_response = client.get(created.json()["state_url"])
            report_response = client.get(created.json()["report_url"])

        self.assertEqual(state_response.status_code, 200)
        state = state_response.json()["state"]
        self.assertEqual(state["job"]["status"], TaskStatus.COMPLETED.value)
        self.assertEqual(
            state["conclusion_level"],
            ConclusionLevel.INCONCLUSIVE.value,
        )
        self.assertIn(report_response.status_code, {200, 409})


if __name__ == "__main__":
    unittest.main()
