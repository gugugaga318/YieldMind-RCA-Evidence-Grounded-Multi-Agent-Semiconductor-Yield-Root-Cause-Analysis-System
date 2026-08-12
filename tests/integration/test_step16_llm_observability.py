from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.audit import InMemoryAuditSink  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMCallError,
    LLMConfigurationError,
    LLMRequest,
    LLMResponse,
    LLMSettings,
)
from yield_rca_core.models import AgentKind, RCAState  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


class UnknownRootCauseClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "rca_reasoning":
            return response
        return LLMResponse(
            data={
                "ranked_candidates": [
                    {
                        "root_cause": "unsupported model invention",
                        "score": 1.0,
                        "evidence_ids": ["EV_UNKNOWN"],
                    }
                ],
                "analysis_summary": "Invented conclusion.",
            },
            usage=response.usage,
        )


class AuthenticationFailureClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        raise LLMCallError("provider authentication failed", status_code=401)


class BillingFailureClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        raise LLMCallError(
            "provider billing failed",
            status_code=400,
            provider_code="Arrearage",
            provider_message="Access denied because the account is in arrears.",
            request_id="req-billing-test",
        )


class InvalidSpecialistClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "specialist":
            return response
        return LLMResponse(
            data={
                **response.data,
                "evidence_ids": ["EV_MODEL_INVENTED"],
            },
            usage=response.usage,
        )


class Step16LLMObservabilityTest(unittest.TestCase):
    def test_fake_mode_runs_every_agent_and_records_usage(self) -> None:
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
        )

        state = workflow.run(QUERY, job_id="RCA_FAKE_STEP16")

        self.assertEqual(state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)
        self.assertEqual(len(state.llm_usage), 7)
        self.assertEqual(
            {event.agent for event in state.llm_usage},
            {
                AgentKind.PLANNER.value,
                AgentKind.MES.value,
                AgentKind.FDC.value,
                AgentKind.DEFECT_WAT.value,
                AgentKind.KNOWLEDGE.value,
                AgentKind.IMPROVEMENT.value,
            },
        )
        self.assertGreater(state.execution_metadata["total_tokens"], 0)
        self.assertEqual(state.execution_metadata["agent_mode"], "fake")
        self.assertEqual(state.execution_metadata["tool_call_count"], 8)

        round_tripped = RCAState.from_dict(state.to_dict())
        self.assertEqual(round_tripped.llm_usage, state.llm_usage)
        self.assertEqual(round_tripped.execution_metadata, state.execution_metadata)

    def test_deterministic_mode_has_no_llm_calls(self) -> None:
        state = build_csv_workflow(SEED_DIR).run(QUERY, job_id="RCA_DETERMINISTIC_STEP16")

        self.assertEqual(state.llm_usage, [])
        self.assertEqual(state.execution_metadata["agent_mode"], "deterministic")
        self.assertEqual(state.execution_metadata["total_tokens"], 0)

    def test_llm_mode_requires_dashscope_key(self) -> None:
        with self.assertRaisesRegex(LLMConfigurationError, "DASHSCOPE_API_KEY"):
            LLMSettings(agent_mode="llm", api_key="")

    def test_retired_rca_llm_cannot_change_hypothesis_engine_output(self) -> None:
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
            llm_client=UnknownRootCauseClient(),
        )

        state = workflow.run(QUERY, job_id="RCA_INVALID_MODEL_OUTPUT")
        self.assertEqual(state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)

    def test_api_exposes_metrics_health_and_best_effort_audit(self) -> None:
        sink = InMemoryAuditSink()
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
        )
        app = create_app(workflow=workflow, audit_sink=sink, execute_jobs_inline=True)

        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "healthy"})
            self.assertEqual(client.get("/ready").json()["agent_mode"], "fake")
            response = client.post("/rca/jobs", json={"user_query": QUERY})
            self.assertEqual(response.status_code, 201)
            job_id = response.json()["job_id"]
            report_response = client.get(f"/rca/jobs/{job_id}/report")
            self.assertEqual(report_response.status_code, 200)
            metrics = client.get("/metrics").text

        actions = [event.action for event in sink.events]
        self.assertEqual(
            actions,
            [
                "RCA_JOB_CREATED",
                "MEMORY_CANDIDATE_CREATED",
                "RCA_JOB_COMPLETED",
                "RCA_REPORT_VIEWED",
            ],
        )
        self.assertEqual(len(sink.llm_usage), 7)
        self.assertIn("rca_jobs_total", metrics)
        self.assertIn("llm_calls_total", metrics)
        self.assertIn("llm_tokens_total", metrics)
        self.assertNotIn(job_id, metrics)

    def test_api_classifies_provider_authentication_failure(self) -> None:
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
            llm_client=AuthenticationFailureClient(),
        )
        app = create_app(workflow=workflow, execute_jobs_inline=True)

        with TestClient(app) as client:
            response = client.post("/rca/jobs", json={"user_query": QUERY})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error_code"], "LLM_AUTH_FAILED")

    def test_api_classifies_provider_billing_failure(self) -> None:
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
            llm_client=BillingFailureClient(),
        )
        app = create_app(workflow=workflow, execute_jobs_inline=True)

        with TestClient(app) as client:
            response = client.post("/rca/jobs", json={"user_query": QUERY})

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["detail"]["error_code"], "LLM_BILLING_ERROR")

    def test_api_classifies_invalid_specialist_output(self) -> None:
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=LLMSettings(agent_mode="fake"),
            llm_client=InvalidSpecialistClient(),
        )
        app = create_app(workflow=workflow, execute_jobs_inline=True)

        with TestClient(app) as client:
            response = client.post("/rca/jobs", json={"user_query": QUERY})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error_code"], "LLM_OUTPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
