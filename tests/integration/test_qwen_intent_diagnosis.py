from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT))

from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMCallError,
    LLMRequest,
    LLMResponse,
    LLMSettings,
)

from scripts.run_qwen_intent_diagnosis import (  # noqa: E402
    run_qwen_intent_diagnosis,
)


class InvalidContractIntentClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name == "intent_planner":
            return LLMResponse(data={}, usage=response.usage)
        return response


class ChangedKnownFactIntentClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "intent_planner":
            return response
        payload = copy.deepcopy(response.data)
        payload["goal"]["known_facts"]["defect"] = "particle"
        return LLMResponse(data=payload, usage=response.usage)


class ProviderFailureIntentClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        raise LLMCallError(
            "provider rejected request",
            status_code=429,
            provider_code="Throttling",
            provider_message="Authorization: Bearer diagnosis-secret",
            request_id="req-intent-diagnosis",
            failure_category="provider_http_error",
        )


class QwenIntentDiagnosisIntegrationTest(unittest.TestCase):
    settings = LLMSettings(agent_mode="fake", api_key="diagnosis-api-secret")

    def test_contract_failures_are_aggregated_by_stable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            evaluation = run_qwen_intent_diagnosis(
                settings=self.settings,
                output_dir=output_dir,
                runs=2,
                client_factory=lambda settings: InvalidContractIntentClient(),
            )

            self.assertTrue(evaluation["diagnosis_complete"])
            self.assertEqual(evaluation["accepted_run_count"], 0)
            self.assertEqual(evaluation["rejected_run_count"], 2)
            self.assertEqual(evaluation["paid_llm_call_count"], 4)
            self.assertEqual(
                evaluation["primary_diagnosis"],
                "contract_validation_error:malformed_output",
            )
            self.assertEqual(
                evaluation["failure_category_counts"],
                {"contract_validation_error": 4},
            )
            self.assertEqual(
                evaluation["reason_code_counts"],
                {"malformed_output": 4},
            )
            self.assertTrue((output_dir / "results.json").is_file())
            self.assertTrue((output_dir / "report.md").is_file())

    def test_semantic_failure_preserves_field_path_without_raw_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = run_qwen_intent_diagnosis(
                settings=self.settings,
                output_dir=Path(directory),
                runs=1,
                client_factory=lambda settings: ChangedKnownFactIntentClient(),
            )

        self.assertEqual(
            evaluation["primary_diagnosis"],
            "semantic_validation_error:known_fact_changed",
        )
        self.assertEqual(
            evaluation["field_path_counts"],
            {"$.goal.known_facts.defect": 2},
        )
        serialized = json.dumps(evaluation)
        self.assertNotIn(self.settings.api_key, serialized)
        self.assertNotIn("deterministic_intent_plan", serialized)
        self.assertNotIn("user_query", serialized)
        self.assertFalse(evaluation["security"]["raw_model_response_stored"])
        self.assertFalse(evaluation["security"]["prompt_stored"])
        self.assertFalse(evaluation["security"]["api_key_stored"])

    def test_accepted_plan_isolated_to_one_paid_intent_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = run_qwen_intent_diagnosis(
                settings=self.settings,
                output_dir=Path(directory),
                runs=1,
                client_factory=lambda settings: FakeLLMClient(),
            )

        self.assertTrue(evaluation["diagnosis_complete"])
        self.assertEqual(evaluation["accepted_run_count"], 1)
        self.assertEqual(evaluation["paid_llm_call_count"], 1)
        self.assertEqual(evaluation["primary_diagnosis"], "intent_plan_accepted")
        run = evaluation["runs"][0]
        self.assertEqual(run["attempt_count"], 1)
        self.assertEqual(run["plan_summary"]["intent"], "root_cause")
        self.assertEqual(run["attempt_diagnostics"][0]["outcome"], "success")

    def test_provider_failure_is_safe_and_keeps_diagnosis_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = run_qwen_intent_diagnosis(
                settings=self.settings,
                output_dir=Path(directory),
                runs=1,
                client_factory=lambda settings: ProviderFailureIntentClient(),
            )

        self.assertFalse(evaluation["diagnosis_complete"])
        self.assertEqual(evaluation["provider_failure_count"], 1)
        self.assertEqual(
            evaluation["primary_diagnosis"],
            "transport_or_provider_failure",
        )
        serialized = json.dumps(evaluation)
        self.assertNotIn("diagnosis-secret", serialized)
        self.assertIn("[REDACTED]", serialized)


if __name__ == "__main__":
    unittest.main()
