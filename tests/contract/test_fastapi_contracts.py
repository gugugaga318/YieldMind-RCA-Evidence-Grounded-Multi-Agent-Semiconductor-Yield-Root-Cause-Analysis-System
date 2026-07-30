from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.schemas import (  # noqa: E402
    CreateRCAJobRequest,
    DecisionEvaluationResponse,
    RCAJobStateResponse,
    RunEvaluationResponse,
)


class FastAPIContractTest(unittest.TestCase):
    def test_required_rca_routes_are_registered(self) -> None:
        app = create_app()
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertIn(("POST", "/rca/jobs"), routes)
        self.assertIn(("GET", "/rca/jobs/{job_id}"), routes)
        self.assertIn(("GET", "/rca/jobs/{job_id}/report"), routes)
        self.assertIn(("GET", "/ready"), routes)

    def test_create_request_normalizes_query_and_rejects_unknown_fields(self) -> None:
        request = CreateRCAJobRequest(user_query="  Analyze the July yield drop.  ")
        self.assertEqual(request.user_query, "Analyze the July yield drop.")

        with self.assertRaises(ValueError):
            CreateRCAJobRequest(user_query="Analyze.", generate_synthetic_data=True)

    def test_api_has_no_synthetic_generator_dependency(self) -> None:
        import yield_rca_api.app as api_app

        source = inspect.getsource(api_app).lower()
        forbidden = (
            "generate_synthetic_fab_data",
            "scripts.generate_synthetic",
            "subprocess",
            "seed_database",
        )
        for dependency in forbidden:
            self.assertNotIn(dependency, source)

    def test_job_response_openapi_describes_typed_evidence(self) -> None:
        app = create_app()
        schema = app.openapi()
        components = schema["components"]["schemas"]

        evidence_schema = components["EvidenceResponse"]["properties"]
        self.assertIn("evidence_type", evidence_schema)
        self.assertIn("source_agent", evidence_schema)
        self.assertIn("source_tool", evidence_schema)
        self.assertIn("observation", evidence_schema)
        self.assertIn("entities", evidence_schema)
        self.assertIn("confidence", evidence_schema)
        self.assertIn("evidence_schema_version", evidence_schema)

    def test_job_state_accepts_typed_run_evaluation_and_legacy_omission(
        self,
    ) -> None:
        evaluation = RunEvaluationResponse(
            goal_id="GOAL_LOT_01",
            goal_success=True,
            stop_correct=True,
            summary="The impact-scope goal was answered before stopping.",
            decision_evaluations=[
                DecisionEvaluationResponse(
                    decision_id="DECISION_MES_01",
                    decision_valid=True,
                    evidence_gain=True,
                    redundant=False,
                    reason="The MES action added new impact-lot Evidence.",
                    new_evidence_ids=["EV_MES_IMPACT_LOTS"],
                )
            ],
        )

        state = RCAJobStateResponse(
            job={"job_id": "JOB_EVALUATED"},
            run_evaluation=evaluation,
        )
        legacy_state = RCAJobStateResponse(job={"job_id": "JOB_LEGACY"})

        run_evaluation = state.run_evaluation
        assert run_evaluation is not None
        self.assertIsInstance(run_evaluation, RunEvaluationResponse)
        self.assertEqual(run_evaluation.goal_id, "GOAL_LOT_01")
        self.assertEqual(
            run_evaluation.decision_evaluations[0].new_evidence_ids,
            ["EV_MES_IMPACT_LOTS"],
        )
        self.assertIsNone(legacy_state.run_evaluation)

    def test_run_evaluation_response_rejects_unagreed_score_fields(self) -> None:
        with self.assertRaises(ValueError):
            RunEvaluationResponse(
                goal_id="GOAL_LOT_01",
                goal_success=True,
                stop_correct=True,
                summary="Only the five agreed boolean metrics are exposed.",
                decision_evaluations=[
                    DecisionEvaluationResponse(
                        decision_id="DECISION_STOP",
                        decision_valid=True,
                        evidence_gain=False,
                        redundant=False,
                        reason="The typed stop decision passed runtime checks.",
                    )
                ],
                weighted_score=0.95,  # type: ignore[call-arg]
            )

    def test_openapi_exposes_nested_run_evaluation_contracts(self) -> None:
        components = create_app().openapi()["components"]["schemas"]

        state_properties = components["RCAJobStateResponse"]["properties"]
        run_properties = components["RunEvaluationResponse"]["properties"]
        decision_properties = components["DecisionEvaluationResponse"]["properties"]

        self.assertIn("run_evaluation", state_properties)
        self.assertEqual(
            set(run_properties),
            {
                "goal_id",
                "goal_success",
                "stop_correct",
                "summary",
                "decision_evaluations",
            },
        )
        self.assertEqual(
            set(decision_properties),
            {
                "decision_id",
                "decision_valid",
                "evidence_gain",
                "redundant",
                "reason",
                "new_evidence_ids",
            },
        )


if __name__ == "__main__":
    unittest.main()
