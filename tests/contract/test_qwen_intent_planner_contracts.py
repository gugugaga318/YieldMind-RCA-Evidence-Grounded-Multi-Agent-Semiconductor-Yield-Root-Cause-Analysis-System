from __future__ import annotations

import copy
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    MAX_CROSS_DOMAIN_ACTIONS,
    EvidenceGapStatus,
    IntentPlan,
    IntentPlanOutcome,
    InvestigationIntent,
    InvestigationValidationError,
    PlannerAttemptOutcome,
    PlannerFailureCategory,
    QwenIntentPlanner,
    QwenIntentPlannerError,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMOutputValidationError,
    LLMRequest,
    LLMResponse,
    capture_llm_usage,
    load_prompt,
)


class RecordingIntentClient(FakeLLMClient):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return super().complete_json(request)


class InvalidThenValidIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if len(self.requests) == 1:
            return LLMResponse(
                data={**response.data, "unknown_top_level_field": True},
                usage=response.usage,
            )
        return response


class AlwaysInvalidIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        return LLMResponse(
            data={**response.data, "unknown_top_level_field": True},
            usage=response.usage,
        )


class ChangedLotIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        payload = copy.deepcopy(response.data)
        payload["goal"]["known_facts"]["lot_id"] = "LOT_99"
        payload["questions"][0]["scope"]["lot_id"] = "LOT_99"
        return LLMResponse(data=payload, usage=response.usage)


class ChangedKnownFactIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        payload = copy.deepcopy(response.data)
        payload["goal"]["known_facts"]["defect"] = "particle"
        return LLMResponse(data=payload, usage=response.usage)


class ParseErrorThenValidIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if not self.requests:
            self.requests.append(request)
            raise LLMOutputValidationError("model response is not valid JSON")
        return super().complete_json(request)


class SensitiveInvalidIntentClient(RecordingIntentClient):
    secret = "sk-sensitive-value-that-must-not-be-retained"

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        return LLMResponse(
            data={
                **response.data,
                "authorization": f"Bearer {self.secret}",
                "unexpected": self.secret,
            },
            usage=response.usage,
        )


class CrossGoalQuestionClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        payload = copy.deepcopy(response.data)
        payload["questions"][0]["goal_id"] = "GOAL_UNREQUESTED"
        return LLMResponse(data=payload, usage=response.usage)


class ModelSelectedImpactIntentClient(RecordingIntentClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        payload = copy.deepcopy(response.data)
        goal_id = payload["goal"]["goal_id"]
        payload["goal"]["intent"] = InvestigationIntent.IMPACT_SCOPE.value
        payload["goal"]["required_evidence"] = ["shared_exposure", "impact_scope"]
        payload["questions"] = [
            {
                "question_id": f"{goal_id}:q:model_impact_scope",
                "goal_id": goal_id,
                "question": "Which Lots share the relevant source-Lot exposure?",
                "rationale": "The model interpreted the requested outcome as impact scope.",
                "scope": {"lot_id": "LOT_01"},
                "status": EvidenceGapStatus.OPEN.value,
                "answer": None,
                "evidence_ids": [],
                "unavailable_reason": None,
            }
        ]
        return LLMResponse(data=payload, usage=response.usage)


class QwenIntentPlannerContractTest(unittest.TestCase):
    def test_fake_qwen_extracts_full_rca_goal_without_selecting_actions(self) -> None:
        client = RecordingIntentClient()
        planner = QwenIntentPlanner(client)

        plan = planner.plan(
            "LOT_01 has scratch at Cu CMP; investigate root cause and impact Lots.",
            lot_id="lot_01",
        )

        self.assertEqual(plan.goal.intent, InvestigationIntent.FULL_RCA.value)
        self.assertEqual(plan.goal.known_facts["lot_id"], "LOT_01")
        self.assertEqual(plan.goal.known_facts["defect"], "scratch")
        self.assertEqual(plan.goal.known_facts["module"], "CU_CMP")
        self.assertEqual(plan.goal.max_steps, MAX_CROSS_DOMAIN_ACTIONS)
        self.assertEqual(len(plan.questions), 3)
        self.assertTrue(
            all(question.status == EvidenceGapStatus.OPEN.value for question in plan.questions)
        )
        serialized = json.dumps(plan.to_dict()).lower()
        self.assertNotIn("next_action", serialized)
        self.assertNotIn("tool_name", serialized)
        self.assertNotIn("root_cause\":", serialized)

        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertEqual(request.prompt_name, "intent_planner")
        self.assertEqual(request.payload["requested_goal_id"], plan.goal.goal_id)
        self.assertEqual(request.payload["fixed_max_steps"], 8)
        self.assertEqual(request.payload["explicit_lot_id"], "LOT_01")

    def test_different_user_intents_produce_different_goal_and_question_sets(self) -> None:
        planner = QwenIntentPlanner(FakeLLMClient())

        impact = planner.plan("Find impact Lots sharing exposure with LOT_01.", lot_id="LOT_01")
        spc = planner.plan("Check the SPC excursion for LOT_01.", lot_id="LOT_01")
        history = planner.plan(
            "Find a similar historical case for LOT_01.",
            lot_id="LOT_01",
        )
        root_cause = planner.plan(
            "Investigate the root cause of scratch on LOT_01.",
            lot_id="LOT_01",
        )

        self.assertEqual(impact.goal.intent, InvestigationIntent.IMPACT_SCOPE.value)
        self.assertEqual(spc.goal.intent, InvestigationIntent.SPC_CHECK.value)
        self.assertEqual(history.goal.intent, InvestigationIntent.HISTORICAL_LOOKUP.value)
        self.assertEqual(root_cause.goal.intent, InvestigationIntent.ROOT_CAUSE.value)
        self.assertEqual(len(impact.questions), 1)
        self.assertEqual(len(spc.questions), 1)
        self.assertEqual(len(history.questions), 1)
        self.assertEqual(len(root_cause.questions), 2)

    def test_valid_qwen_intent_is_not_overridden_by_python_baseline_policy(self) -> None:
        client = ModelSelectedImpactIntentClient()

        plan = QwenIntentPlanner(client).plan(
            "Analyze the requested scope for LOT_01.",
            lot_id="LOT_01",
        )

        self.assertEqual(plan.goal.intent, InvestigationIntent.IMPACT_SCOPE.value)
        self.assertEqual(
            plan.questions[0].question_id,
            f"{plan.goal.goal_id}:q:model_impact_scope",
        )
        self.assertEqual(len(client.requests), 1)

    def test_intent_plan_round_trip_is_strict_and_json_serializable(self) -> None:
        plan = QwenIntentPlanner(FakeLLMClient()).plan(
            "Investigate scratch root cause for LOT_01.",
            lot_id="LOT_01",
        )
        restored = IntentPlan.from_dict(plan.to_dict())

        self.assertEqual(restored, plan)
        json.dumps(restored.to_dict())

        payload = plan.to_dict()
        payload["unexpected_objective"] = "Investigate every impact Lot recursively."
        with self.assertRaisesRegex(InvestigationValidationError, "unknown fields"):
            IntentPlan.from_dict(payload)

    def test_invalid_output_is_retried_once_with_validation_feedback(self) -> None:
        client = InvalidThenValidIntentClient()
        outcome = QwenIntentPlanner(client).plan_with_diagnostics(
            "Investigate scratch root cause for LOT_01.",
            lot_id="LOT_01",
        )
        plan = outcome.plan

        self.assertEqual(plan.goal.intent, InvestigationIntent.ROOT_CAUSE.value)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[1].payload["output_attempt"], 2)
        self.assertIn(
            "unknown fields",
            str(client.requests[1].payload["previous_validation_error"]),
        )
        self.assertEqual(len(outcome.attempt_diagnostics), 2)
        first, second = outcome.attempt_diagnostics
        self.assertEqual(first.outcome, PlannerAttemptOutcome.FAILURE.value)
        self.assertEqual(
            first.failure_category,
            PlannerFailureCategory.CONTRACT_VALIDATION_ERROR.value,
        )
        self.assertEqual(first.reason_code, "malformed_output")
        self.assertEqual(first.field_path, "$")
        self.assertTrue(first.repair_feedback_sent)
        self.assertEqual(second.outcome, PlannerAttemptOutcome.SUCCESS.value)
        self.assertFalse(second.repair_feedback_sent)

    def test_two_invalid_outputs_require_explicit_controlled_react_fallback(self) -> None:
        client = AlwaysInvalidIntentClient()

        with self.assertRaises(QwenIntentPlannerError) as context:
            QwenIntentPlanner(client).plan(
                "Investigate scratch root cause for LOT_01.",
                lot_id="LOT_01",
            )

        self.assertEqual(context.exception.attempts, 2)
        self.assertEqual(context.exception.fallback_mode, "controlled_react")
        self.assertEqual(len(context.exception.validation_errors), 2)
        self.assertEqual(len(context.exception.attempt_diagnostics), 2)
        self.assertTrue(context.exception.attempt_diagnostics[0].repair_feedback_sent)
        self.assertFalse(context.exception.attempt_diagnostics[1].repair_feedback_sent)
        self.assertEqual(len(client.requests), 2)

    def test_semantic_known_fact_change_has_stable_reason_and_field_path(self) -> None:
        client = ChangedKnownFactIntentClient()

        with self.assertRaises(QwenIntentPlannerError) as context:
            QwenIntentPlanner(client).plan(
                "Investigate scratch root cause for LOT_01.",
                lot_id="LOT_01",
            )

        diagnostic = context.exception.attempt_diagnostics[0]
        self.assertEqual(
            diagnostic.failure_category,
            PlannerFailureCategory.SEMANTIC_VALIDATION_ERROR.value,
        )
        self.assertEqual(diagnostic.reason_code, "known_fact_changed")
        self.assertEqual(diagnostic.field_path, "$.goal.known_facts.defect")
        self.assertEqual(
            diagnostic.baseline_diff["known_fact_keys_changed"],
            ["defect"],
        )

    def test_output_parse_and_semantic_validation_are_distinct(self) -> None:
        parse_outcome = QwenIntentPlanner(
            ParseErrorThenValidIntentClient()
        ).plan_with_diagnostics(
            "Investigate scratch root cause for LOT_01.",
            lot_id="LOT_01",
        )
        parse_diagnostic = parse_outcome.attempt_diagnostics[0]
        self.assertEqual(
            parse_diagnostic.failure_category,
            PlannerFailureCategory.OUTPUT_PARSE_ERROR.value,
        )
        self.assertEqual(parse_diagnostic.reason_code, "malformed_output")

        with self.assertRaises(QwenIntentPlannerError) as context:
            QwenIntentPlanner(ChangedKnownFactIntentClient()).plan(
                "Investigate scratch root cause for LOT_01.",
                lot_id="LOT_01",
            )
        self.assertEqual(
            context.exception.attempt_diagnostics[0].failure_category,
            PlannerFailureCategory.SEMANTIC_VALIDATION_ERROR.value,
        )

    def test_intent_plan_outcome_diagnostics_round_trip_without_sensitive_payload(self) -> None:
        outcome = QwenIntentPlanner(FakeLLMClient()).plan_with_diagnostics(
            "Investigate scratch root cause for LOT_01.",
            lot_id="LOT_01",
        )
        restored = IntentPlanOutcome.from_dict(outcome.to_dict())

        self.assertEqual(restored, outcome)
        serialized = json.dumps(restored.to_dict())
        self.assertNotIn("user_query", serialized)
        self.assertNotIn("deterministic_intent_plan", serialized)

        client = SensitiveInvalidIntentClient()
        with self.assertRaises(QwenIntentPlannerError) as context:
            QwenIntentPlanner(client).plan(
                "Investigate scratch root cause for LOT_01.",
                lot_id="LOT_01",
            )
        diagnostics = json.dumps(
            [item.to_dict() for item in context.exception.attempt_diagnostics]
        )
        self.assertNotIn(client.secret, diagnostics)
        self.assertNotIn("authorization", diagnostics.casefold())

    def test_legacy_plan_interface_returns_only_intent_plan(self) -> None:
        plan = QwenIntentPlanner(FakeLLMClient()).plan(
            "Find impact Lots for LOT_01.",
            lot_id="LOT_01",
        )

        self.assertIsInstance(plan, IntentPlan)

    def test_qwen_cannot_change_explicit_lot_or_create_cross_goal_question(self) -> None:
        cases = (
            (ChangedLotIntentClient(), None),
            (CrossGoalQuestionClient(), "LOT_01"),
        )
        for client, lot_id in cases:
            with self.subTest(client=type(client).__name__):
                with self.assertRaises(QwenIntentPlannerError):
                    QwenIntentPlanner(client).plan(
                        "Investigate scratch root cause for LOT_01.",
                        lot_id=lot_id,
                    )
                self.assertEqual(len(client.requests), 2)

    def test_fake_intent_call_is_recorded_by_existing_llm_observability(self) -> None:
        with capture_llm_usage() as usage:
            QwenIntentPlanner(FakeLLMClient()).plan(
                "Find impact Lots for LOT_01.",
                lot_id="LOT_01",
            )

        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].agent, "planner")
        self.assertEqual(usage[0].provider, "fake")
        self.assertGreater(usage[0].total_tokens, 0)

    def test_intent_prompt_and_runtime_have_no_tool_or_repository_dependency(self) -> None:
        import yield_rca_core.intent_planner as intent_planner

        prompt = load_prompt("intent_planner", "v1").lower()
        self.assertIn("do not choose an agent, action, tool", prompt)
        source = inspect.getsource(intent_planner).lower()
        self.assertNotIn("yield_rca_core.repositories", source)
        self.assertNotIn("yield_rca_core.tool_layer", source)


if __name__ == "__main__":
    unittest.main()
