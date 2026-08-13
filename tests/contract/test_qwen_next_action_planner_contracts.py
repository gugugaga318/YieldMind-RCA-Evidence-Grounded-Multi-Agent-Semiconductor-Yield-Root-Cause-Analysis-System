from __future__ import annotations

import copy
import inspect
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    LLM_REACT_EXECUTABLE_ACTION_KINDS,
    EvidenceGapStatus,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    InvestigationQuestion,
    PlannerDecision,
    QuestionEvidenceLink,
    QuestionEvidenceRelation,
    QuestionUpdateDisposition,
    QuestionUpdateReasonCode,
    QwenNextActionPlanner,
    QwenNextActionPlannerError,
)
from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ActionRecord,
    ConclusionLevel,
    DecisionType,
    GoalStatus,
    StopReason,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMCallError,
    LLMRequest,
    LLMResponse,
    capture_llm_usage,
    load_prompt,
)
from yield_rca_core.models import AgentFinding, AgentKind  # noqa: E402


def goal(*, max_steps: int = 8) -> InvestigationGoal:
    return InvestigationGoal(
        goal_id="GOAL_LOT_01",
        intent=InvestigationIntent.ROOT_CAUSE.value,
        summary="Investigate the Cu CMP scratch on LOT_01.",
        known_facts={
            "lot_id": "LOT_01",
            "module": "CU_CMP",
            "defect": "scratch",
        },
        required_evidence=["defect_signature", "process_mechanism"],
        max_steps=max_steps,
    )


def questions() -> list[InvestigationQuestion]:
    return [
        InvestigationQuestion(
            question_id="Q_DEFECT",
            goal_id="GOAL_LOT_01",
            question="What is the source Lot scratch signature?",
            rationale="The symptom must be characterized before mechanism analysis.",
            scope={"lot_id": "LOT_01", "module": "CU_CMP"},
        ),
        InvestigationQuestion(
            question_id="Q_MECHANISM",
            goal_id="GOAL_LOT_01",
            question="Which Cu CMP mechanism explains the scratch?",
            rationale="The requested outcome is an evidence-backed root cause.",
            scope={"lot_id": "LOT_01", "module": "CU_CMP"},
        ),
    ]


def finding(agent: str, *, evidence_id: str | None = None) -> AgentFinding:
    normalized_evidence_id = evidence_id or f"EV_{agent.upper()}"
    return AgentFinding(
        finding_id=f"FINDING_{agent.upper()}",
        agent=agent,
        summary=f"{agent} observation",
        confidence=0.8,
        evidence_ids=[normalized_evidence_id],
        details={"observation": f"{agent} evidence is available"},
    )


def action_record(
    *,
    kind: str,
    agent: str,
    scope: dict[str, Any],
    action_id: str | None = None,
) -> ActionRecord:
    return ActionRecord(
        action=InvestigationAction(
            action_id=action_id or f"PRIOR_{kind}",
            kind=kind,
            agent=agent,
            reason="Earlier investigation action.",
            inputs={"lot_id": "LOT_01"},
            scope=scope,
        ),
        status="completed",
        decision_summary="Earlier observation recorded.",
    )


def model_act_payload(
    request: LLMRequest,
    *,
    kind: str = ActionKind.FIND_SHARED_EXPOSURE.value,
    agent: str = AgentKind.MES.value,
) -> dict[str, Any]:
    return {
        "decision_id": f"MODEL_DECISION_{request.payload['output_attempt']}",
        "goal_id": request.payload["goal"]["goal_id"],
        "decision_type": DecisionType.ACT.value,
        "reason": "The model selected a useful registered action.",
        "goal_status": GoalStatus.IN_PROGRESS.value,
        "proposed_conclusion_level": ConclusionLevel.SIGNAL.value,
        "next_action": {
            "action_id": f"MODEL_ACTION_{request.payload['output_attempt']}",
            "kind": kind,
            "agent": agent,
            "reason": "Collect the observation needed by the open question.",
            "inputs": {"lot_id": "LOT_01"},
            "scope": {"lot_id": "LOT_01", "module": "CU_CMP"},
            "required_evidence_ids": [],
            "max_attempts": 1,
        },
        "target_question_ids": ["Q_MECHANISM"],
        "new_questions": [],
        "stop_reason": None,
        "question_updates": [],
    }


Mutation = Callable[[dict[str, Any], LLMRequest], None]


class RecordingNextActionClient(FakeLLMClient):
    def __init__(self, mutation: Mutation | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self.mutation = mutation

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = super().complete_json(request)
        payload = copy.deepcopy(response.data)
        if self.mutation is not None:
            self.mutation(payload, request)
        return LLMResponse(data=payload, usage=response.usage)


class ModelSelectedMESClient(RecordingNextActionClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        response = FakeLLMClient.complete_json(self, request)
        return LLMResponse(
            data=model_act_payload(request),
            usage=response.usage,
        )


class InvalidThenValidClient(RecordingNextActionClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if len(self.requests) == 1:
            payload = model_act_payload(
                request,
                kind=ActionKind.INSPECT_FDC_SPC.value,
                agent=AgentKind.FDC.value,
            )
            return LLMResponse(data=payload, usage=response.usage)
        return response


class TransientCallFailureClient(RecordingNextActionClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise LLMCallError(
                "temporary provider failure",
                status_code=429,
                provider_code="Throttling",
                failure_category="provider_http_error",
            )
        return FakeLLMClient.complete_json(self, request)


class PersistentCallFailureClient(RecordingNextActionClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        raise LLMCallError(
            "provider unavailable",
            status_code=429,
            provider_code="Throttling",
            provider_message=(
                "Authorization: Bearer planner-secret api_key=planner-secret"
            ),
            request_id="req-429",
            failure_category="provider_http_error",
        )


class QwenNextActionPlannerContractTest(unittest.TestCase):
    def test_fake_client_uses_a_registered_deterministic_baseline(self) -> None:
        client = RecordingNextActionClient()

        with capture_llm_usage() as usage:
            decision = QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        self.assertEqual(decision.decision_type, DecisionType.ACT.value)
        self.assertEqual(
            decision.next_action.kind,
            ActionKind.INSPECT_DEFECT_PATTERN.value,
        )
        self.assertTrue(decision.next_action.scope)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0].prompt_name, "next_action_planner")
        self.assertEqual(
            {
                item["kind"]
                for item in client.requests[0].payload["allowed_actions"]
            },
            {
                ActionKind.INSPECT_DEFECT_PATTERN.value,
                ActionKind.FIND_SHARED_EXPOSURE.value,
            },
        )
        self.assertEqual(
            client.requests[0].payload["legal_target_question_ids_by_action"],
            {
                ActionKind.FIND_SHARED_EXPOSURE.value: [
                    "Q_MECHANISM",
                ],
                ActionKind.INSPECT_DEFECT_PATTERN.value: [
                    "Q_DEFECT",
                    "Q_MECHANISM",
                ],
            },
        )
        self.assertNotIn(
            ActionKind.ASSESS_IMPACT_SCOPE.value,
            LLM_REACT_EXECUTABLE_ACTION_KINDS,
        )
        self.assertNotIn(
            ActionKind.INSPECT_RECIPE_CHANGE.value,
            LLM_REACT_EXECUTABLE_ACTION_KINDS,
        )
        self.assertNotIn(
            ActionKind.CONCLUDE_INCONCLUSIVE.value,
            LLM_REACT_EXECUTABLE_ACTION_KINDS,
        )
        self.assertIn(
            ActionKind.VALIDATE_HISTORICAL_CASE.value,
            LLM_REACT_EXECUTABLE_ACTION_KINDS,
        )
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].provider, "fake")

    def test_legal_model_action_is_not_overridden_by_deterministic_policy(self) -> None:
        client = ModelSelectedMESClient()

        decision = QwenNextActionPlanner(client).decide(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
        )

        self.assertEqual(
            client.requests[0].payload["deterministic_planner_decision"][
                "next_action"
            ]["kind"],
            ActionKind.INSPECT_DEFECT_PATTERN.value,
        )
        self.assertEqual(
            decision.next_action.kind,
            ActionKind.FIND_SHARED_EXPOSURE.value,
        )
        self.assertEqual(decision.decision_id, "MODEL_DECISION_1")
        self.assertEqual(len(client.requests), 1)

    def test_action_that_only_repeats_a_satisfied_group_is_retried_atomically(
        self,
    ) -> None:
        mechanism = questions()[1]
        spc_question = InvestigationQuestion(
            question_id="Q_SPC",
            goal_id=mechanism.goal_id,
            question="Which SPC signal is abnormal?",
            rationale="The process signal must be inspected.",
            question_kind="spc_signal",
            scope={"lot_id": "LOT_01", "module": "CU_CMP"},
        )
        prior_record = action_record(
            kind=ActionKind.INSPECT_FDC_SPC.value,
            agent=AgentKind.FDC.value,
            scope={
                "lot_id": "LOT_01",
                "module": "CU_CMP",
                "parameter": "THK",
            },
            action_id="PRIOR_FDC_PROCESS_SIGNAL",
        )
        prior_record = ActionRecord(
            action=prior_record.action,
            status=prior_record.status,
            produced_finding_ids=list(prior_record.produced_finding_ids),
            produced_evidence_ids=["EV_PROCESS_SIGNAL"],
            decision_summary=prior_record.decision_summary,
        )
        prior_decision = PlannerDecision(
            decision_id="PRIOR_FDC_DECISION",
            goal_id=mechanism.goal_id,
            decision_type=DecisionType.ACT.value,
            reason="The first FDC Action filled process_anomaly.",
            goal_status=GoalStatus.IN_PROGRESS.value,
            proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
            next_action=prior_record.action,
            target_question_ids=[mechanism.question_id],
        )
        process_link = QuestionEvidenceLink(
            question_id=mechanism.question_id,
            evidence_id="EV_PROCESS_SIGNAL",
            action_id=prior_record.action.action_id,
            relation=QuestionEvidenceRelation.SUPPORTS.value,
            matched_evidence_group="process_anomaly",
            reason="The first FDC observation filled process_anomaly.",
        )

        def repeat_fdc_for_both_questions(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_FDC_SPC.value,
                    agent=AgentKind.FDC.value,
                )
            )
            payload["next_action"]["scope"]["parameter"] = "PRESSURE"
            payload["target_question_ids"] = [
                spc_question.question_id,
                mechanism.question_id,
            ]

        client = RecordingNextActionClient(repeat_fdc_for_both_questions)
        with self.assertRaises(QwenNextActionPlannerError) as caught:
            QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=[mechanism, spc_question],
                findings=[finding(AgentKind.MES.value)],
                action_records=[prior_record],
                tool_call_count=1,
                evidence_ids=["EV_PROCESS_SIGNAL"],
                question_evidence_links=[process_link],
                prior_decisions=[prior_decision],
            )

        self.assertEqual(len(client.requests), 2)
        self.assertTrue(
            all(
                "no_expected_evidence_gain" in error
                and mechanism.question_id in error
                for error in caught.exception.validation_errors
            )
        )
        self.assertTrue(
            all(
                request.payload["previous_validation_error"] is None
                if index == 0
                else "no_expected_evidence_gain"
                in str(request.payload["previous_validation_error"])
                for index, request in enumerate(client.requests)
            )
        )
        retry_feedback = client.requests[1].payload[
            "previous_validation_feedback"
        ]
        self.assertEqual(
            retry_feedback["legal_target_question_ids_by_action"][
                ActionKind.INSPECT_FDC_SPC.value
            ],
            [spc_question.question_id],
        )
        self.assertNotIn(
            ActionKind.INSPECT_FDC_SPC.value,
            retry_feedback["question_action_capabilities"][
                mechanism.question_id
            ],
        )

    def test_product_defect_inspection_requires_mes_selected_lots(self) -> None:
        product_goal = InvestigationGoal(
            goal_id="GOAL_PRODUCT",
            intent=InvestigationIntent.ROOT_CAUSE.value,
            summary="Investigate the product-window yield loss.",
            known_facts={"product_id": "40N_SOC"},
            required_evidence=["defect_signature"],
        )
        product_questions = [
            InvestigationQuestion(
                question_id="Q_PRODUCT_DEFECT",
                goal_id=product_goal.goal_id,
                question="Which affected Lots share the defect signature?",
                rationale="MES must first select the bounded product-window Lots.",
                scope={"product_id": "40N_SOC"},
            )
        ]

        def choose_defect(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                    agent=AgentKind.DEFECT_WAT.value,
                )
            )
            payload["next_action"]["inputs"] = {"product_id": "40N_SOC"}
            payload["next_action"]["scope"] = {"product_id": "40N_SOC"}
            payload["target_question_ids"] = ["Q_PRODUCT_DEFECT"]

        without_mes = RecordingNextActionClient(choose_defect)
        with self.assertRaises(QwenNextActionPlannerError):
            QwenNextActionPlanner(without_mes).decide(
                goal=product_goal,
                questions=product_questions,
                findings=[],
                action_records=[],
                tool_call_count=0,
            )
        self.assertEqual(len(without_mes.requests), 2)

        decision = QwenNextActionPlanner(
            RecordingNextActionClient(choose_defect)
        ).decide(
            goal=product_goal,
            questions=product_questions,
            findings=[finding(AgentKind.MES.value)],
            action_records=[],
            tool_call_count=1,
        )
        self.assertEqual(
            decision.next_action.kind,
            ActionKind.INSPECT_DEFECT_PATTERN.value,
        )

    def test_legal_model_stop_and_proposed_level_are_not_policy_overridden(self) -> None:
        def choose_stop(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                {
                    "decision_id": "MODEL_STOP",
                    "goal_id": request.payload["goal"]["goal_id"],
                    "decision_type": DecisionType.STOP.value,
                    "reason": "The model considers the requested boundary complete.",
                    "goal_status": GoalStatus.SATISFIED.value,
                    "proposed_conclusion_level": ConclusionLevel.SUPPORTED.value,
                    "next_action": None,
                    "target_question_ids": [],
                    "new_questions": [],
                    "stop_reason": StopReason.GOAL_SATISFIED.value,
                    "question_updates": [],
                }
            )

        decision = QwenNextActionPlanner(
            RecordingNextActionClient(choose_stop)
        ).decide(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
        )

        self.assertEqual(decision.decision_type, DecisionType.STOP.value)
        self.assertEqual(
            decision.proposed_conclusion_level,
            ConclusionLevel.SUPPORTED.value,
        )

    def test_reviewed_goal_satisfied_stop_cannot_hide_rejected_open_updates(
        self,
    ) -> None:
        def stop_with_open_update(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(
                {
                    "decision_id": f"STOP_{request.payload['output_attempt']}",
                    "goal_id": request.payload["goal"]["goal_id"],
                    "decision_type": DecisionType.STOP.value,
                    "reason": "Incorrectly claim completion with an open update.",
                    "goal_status": GoalStatus.SATISFIED.value,
                    "proposed_conclusion_level": ConclusionLevel.SUPPORTED.value,
                    "next_action": None,
                    "target_question_ids": [],
                    "new_questions": [],
                    "stop_reason": StopReason.GOAL_SATISFIED.value,
                    "question_updates": [
                        {
                            "question_id": "Q_DEFECT",
                            "status": EvidenceGapStatus.OPEN.value,
                            "answer": "Partial progress is not terminal.",
                            "evidence_ids": ["EV_DEFECT"],
                            "unavailable_reason": None,
                        }
                    ],
                }
            )

        client = RecordingNextActionClient(stop_with_open_update)
        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(client).decide_with_review(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
                evidence_ids=["EV_DEFECT"],
            )

        self.assertEqual(len(client.requests), 2)
        self.assertTrue(
            all(
                "goal_satisfied stop cannot leave open investigation questions"
                in error
                for error in captured.exception.validation_errors
            )
        )
        stop_contract = client.requests[0].payload[
            "goal_satisfied_stop_contract"
        ]
        self.assertEqual(
            stop_contract["currently_open_question_ids"],
            ["Q_DEFECT", "Q_MECHANISM"],
        )
        self.assertTrue(
            stop_contract["require_terminal_update_for_every_open_question"]
        )
        retry_feedback = client.requests[1].payload[
            "previous_validation_feedback"
        ]
        self.assertEqual(
            retry_feedback["must_terminally_update_question_ids"],
            ["Q_DEFECT", "Q_MECHANISM"],
        )
        self.assertIn(
            "validator_ready_reference_question_updates",
            retry_feedback["repair_instruction"],
        )

    def test_reviewed_stop_accepts_supported_updates_for_every_open_question(
        self,
    ) -> None:
        def close_questions_and_stop(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(
                {
                    "decision_id": "STOP_SUPPORTED",
                    "goal_id": request.payload["goal"]["goal_id"],
                    "decision_type": DecisionType.STOP.value,
                    "reason": "Both requested Questions are evidence-backed.",
                    "goal_status": GoalStatus.SATISFIED.value,
                    "proposed_conclusion_level": ConclusionLevel.SUPPORTED.value,
                    "next_action": None,
                    "target_question_ids": [],
                    "new_questions": [],
                    "stop_reason": StopReason.GOAL_SATISFIED.value,
                    "question_updates": [
                        {
                            "question_id": "Q_DEFECT",
                            "status": EvidenceGapStatus.CLOSED.value,
                            "answer": "The source Lot has an edge scratch.",
                            "evidence_ids": ["EV_DEFECT"],
                            "unavailable_reason": None,
                        },
                        {
                            "question_id": "Q_MECHANISM",
                            "status": EvidenceGapStatus.CLOSED.value,
                            "answer": "The process Evidence supports the mechanism.",
                            "evidence_ids": ["EV_MECHANISM"],
                            "unavailable_reason": None,
                        },
                    ],
                }
            )

        client = RecordingNextActionClient(close_questions_and_stop)
        outcome = QwenNextActionPlanner(client).decide_with_review(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
            evidence_ids=["EV_DEFECT", "EV_MECHANISM"],
        )

        self.assertEqual(len(client.requests), 1)
        self.assertEqual(outcome.decision.decision_type, DecisionType.STOP.value)
        self.assertEqual(len(outcome.decision.question_updates), 2)
        self.assertTrue(
            all(
                review.disposition == QuestionUpdateDisposition.ACCEPTED.value
                for review in outcome.question_update_reviews
            )
        )

    def test_modified_input_echo_retry_also_repairs_goal_satisfied_stop(self) -> None:
        defect_question = questions()[0]
        defect_link = QuestionEvidenceLink(
            question_id=defect_question.question_id,
            evidence_id="EV_DEFECT",
            action_id="ACT_DEFECT",
            relation=QuestionEvidenceRelation.SUPPORTS.value,
            matched_evidence_group="product_signal",
            reason="The defect observation fills the product-signal group.",
        )

        def echo_then_repair(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            reference_updates = request.payload[
                "goal_satisfied_stop_contract"
            ]["validator_ready_reference_question_updates"]
            payload.clear()
            payload.update(
                {
                    "decision_id": f"STOP_{request.payload['output_attempt']}",
                    "goal_id": request.payload["goal"]["goal_id"],
                    "decision_type": DecisionType.STOP.value,
                    "reason": "The requested defect signature is evidence-backed.",
                    "goal_status": GoalStatus.SATISFIED.value,
                    "proposed_conclusion_level": ConclusionLevel.SIGNAL.value,
                    "next_action": None,
                    "target_question_ids": [],
                    "new_questions": [],
                    "stop_reason": StopReason.GOAL_SATISFIED.value,
                    "question_updates": reference_updates,
                }
            )
            if request.payload["output_attempt"] == 1:
                modified_contract = copy.deepcopy(
                    request.payload["goal_satisfied_stop_contract"]
                )
                modified_contract["unexpected_model_field"] = True
                payload["goal_satisfied_stop_contract"] = modified_contract

        client = RecordingNextActionClient(echo_then_repair)
        outcome = QwenNextActionPlanner(client).decide_with_review(
            goal=goal(),
            questions=[defect_question],
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
            evidence_ids=["EV_DEFECT"],
            question_evidence_links=[defect_link],
        )

        self.assertEqual(len(client.requests), 2)
        feedback = client.requests[1].payload["previous_validation_feedback"]
        self.assertIn(
            "goal_satisfied_stop_contract",
            feedback["input_only_fields_never_copy_to_output"],
        )
        self.assertEqual(
            feedback["validator_ready_reference_question_updates"],
            client.requests[1].payload["goal_satisfied_stop_contract"][
                "validator_ready_reference_question_updates"
            ],
        )
        self.assertEqual(outcome.decision.decision_type, DecisionType.STOP.value)
        self.assertEqual(len(outcome.decision.question_updates), 1)

    def test_exact_validator_ready_echo_becomes_python_owned_question_updates(
        self,
    ) -> None:
        defect_question = questions()[0]
        defect_link = QuestionEvidenceLink(
            question_id=defect_question.question_id,
            evidence_id="EV_DEFECT",
            action_id="ACT_DEFECT",
            relation=QuestionEvidenceRelation.SUPPORTS.value,
            matched_evidence_group="product_signal",
            reason="The defect observation fills the product-signal group.",
        )

        def echo_reference_at_top_level(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            reference_updates = copy.deepcopy(
                request.payload["goal_satisfied_stop_contract"][
                    "validator_ready_reference_question_updates"
                ]
            )
            payload.clear()
            payload.update(
                {
                    "decision_id": "STOP_WITH_REFERENCE_ECHO",
                    "goal_id": request.payload["goal"]["goal_id"],
                    "decision_type": DecisionType.STOP.value,
                    "reason": "Use the evidence-backed Python stop transition.",
                    "goal_status": GoalStatus.SATISFIED.value,
                    "proposed_conclusion_level": ConclusionLevel.SIGNAL.value,
                    "next_action": None,
                    "target_question_ids": [],
                    "new_questions": [],
                    "stop_reason": StopReason.GOAL_SATISFIED.value,
                    "question_updates": [],
                    "validator_ready_reference_question_updates": (
                        reference_updates
                    ),
                }
            )

        client = RecordingNextActionClient(echo_reference_at_top_level)
        outcome = QwenNextActionPlanner(client).decide_with_review(
            goal=goal(),
            questions=[defect_question],
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
            evidence_ids=["EV_DEFECT"],
            question_evidence_links=[defect_link],
        )

        self.assertEqual(len(client.requests), 1)
        self.assertEqual(outcome.decision.decision_type, DecisionType.STOP.value)
        self.assertEqual(len(outcome.decision.question_updates), 1)
        self.assertEqual(
            outcome.decision.question_updates[0].question_id,
            defect_question.question_id,
        )

    def test_modified_validator_ready_echo_cannot_change_python_transition(
        self,
    ) -> None:
        def echo_modified_reference(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload["validator_ready_reference_question_updates"] = [
                {
                    "question_id": "Q_INVENTED",
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "Invented transition.",
                    "evidence_ids": ["EV_INVENTED"],
                    "unavailable_reason": None,
                }
            ]

        client = RecordingNextActionClient(echo_modified_reference)
        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        self.assertEqual(len(client.requests), 2)
        self.assertTrue(
            all(
                "validator_ready_reference_question_updates" in error
                and "unknown fields" in error
                for error in captured.exception.validation_errors
            )
        )

    def test_invalid_output_is_retried_once_with_validation_feedback(self) -> None:
        client = InvalidThenValidClient()

        decision = QwenNextActionPlanner(client).decide(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
        )

        self.assertEqual(
            decision.next_action.kind,
            ActionKind.INSPECT_DEFECT_PATTERN.value,
        )
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[1].payload["output_attempt"], 2)
        self.assertIn(
            "prerequisite",
            str(client.requests[1].payload["previous_validation_error"]),
        )
        feedback = client.requests[1].payload["previous_validation_feedback"]
        self.assertEqual(feedback["category"], "core_decision_validation")
        self.assertTrue(feedback["must_repair_before_resubmission"])
        self.assertIn("prerequisite", feedback["message"])

    def test_exact_python_owned_context_echo_is_removed_before_strict_parse(
        self,
    ) -> None:
        def echo_context(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload["question_action_capabilities"] = copy.deepcopy(
                request.payload["question_action_capabilities"]
            )

        client = RecordingNextActionClient(echo_context)
        decision = QwenNextActionPlanner(client).decide(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
        )

        self.assertEqual(len(client.requests), 1)
        self.assertEqual(decision.decision_type, DecisionType.ACT.value)

    def test_modified_python_owned_context_echo_still_fails_closed(self) -> None:
        def modify_context(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload["question_action_capabilities"] = {"Q_INVENTED": []}

        client = RecordingNextActionClient(modify_context)
        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        self.assertEqual(len(client.requests), 2)
        self.assertTrue(
            all(
                "question_action_capabilities" in error
                and "unknown fields" in error
                for error in captured.exception.validation_errors
            )
        )

    def test_one_transient_call_failure_is_retried_without_advancing_output_attempt(
        self,
    ) -> None:
        client = TransientCallFailureClient()

        decision = QwenNextActionPlanner(client).decide(
            goal=goal(),
            questions=questions(),
            findings=[],
            action_records=[],
            tool_call_count=0,
        )

        self.assertEqual(
            decision.next_action.kind,
            ActionKind.INSPECT_DEFECT_PATTERN.value,
        )
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(
            [request.payload["output_attempt"] for request in client.requests],
            [1, 1],
        )

    def test_two_call_failures_exhaust_retry_with_sanitized_diagnostics(self) -> None:
        client = PersistentCallFailureClient()

        with self.assertRaises(LLMCallError) as captured:
            QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        error = captured.exception
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(error.call_attempt_count, 2)
        self.assertEqual(error.failure_category, "provider_http_error")
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.provider_code, "Throttling")
        self.assertEqual(error.request_id, "req-429")
        self.assertNotIn("planner-secret", error.provider_message or "")

    def test_review_path_still_falls_back_for_an_invalid_core_action(self) -> None:
        def wrong_agent(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.FIND_SHARED_EXPOSURE.value,
                    agent=AgentKind.FDC.value,
                )
            )

        client = RecordingNextActionClient(wrong_agent)
        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(client).decide_with_review(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        self.assertEqual(len(client.requests), 2)
        self.assertTrue(
            all(
                "must be executed by Agent mes" in error
                for error in captured.exception.validation_errors
            )
        )
        self.assertEqual(captured.exception.core_validation_error_count, 2)
        self.assertEqual(captured.exception.output_parse_error_count, 0)

    def test_strict_compatibility_path_still_retries_close_and_target(self) -> None:
        def close_and_target(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    "question_id": "Q_MECHANISM",
                    "status": EvidenceGapStatus.UNAVAILABLE.value,
                    "answer": None,
                    "evidence_ids": [],
                    "unavailable_reason": "No registered source can answer it.",
                }
            ]

        client = RecordingNextActionClient(close_and_target)
        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(client).decide(
                goal=goal(),
                questions=questions(),
                findings=[],
                action_records=[],
                tool_call_count=0,
            )

        self.assertEqual(len(client.requests), 2)
        retry_error = str(
            client.requests[1].payload["previous_validation_error"]
        )
        self.assertIn(
            "target_question_ids and question_updates overlap for ['Q_MECHANISM']",
            retry_error,
        )
        self.assertIn(
            "keep it open and omit its QuestionUpdate",
            retry_error,
        )
        self.assertEqual(len(captured.exception.validation_errors), 2)

    def test_review_path_rejects_overlap_without_retrying_or_changing_action(
        self,
    ) -> None:
        def close_and_target(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                    agent=AgentKind.DEFECT_WAT.value,
                )
            )
            payload["target_question_ids"] = ["Q_DEFECT"]
            payload["question_updates"] = [
                {
                    "question_id": "Q_DEFECT",
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "The selected Lot has an edge-dominant scratch.",
                    "evidence_ids": ["EV_DEFECT"],
                    "unavailable_reason": None,
                }
            ]

        client = RecordingNextActionClient(close_and_target)
        outcome = QwenNextActionPlanner(client).decide_with_review(
            goal=goal(),
            questions=questions(),
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
        )

        self.assertEqual(len(client.requests), 1)
        self.assertEqual(outcome.decision.target_question_ids, ["Q_DEFECT"])
        self.assertEqual(outcome.decision.question_updates, [])
        self.assertEqual(
            outcome.question_update_reviews[0].disposition,
            QuestionUpdateDisposition.REJECTED.value,
        )
        self.assertEqual(
            outcome.question_update_reviews[0].reason_code,
            QuestionUpdateReasonCode.TARGET_OVERLAP.value,
        )

    def test_review_path_rejects_open_status_without_retrying_core_action(
        self,
    ) -> None:
        def report_partial_progress(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    "question_id": "Q_DEFECT",
                    "status": EvidenceGapStatus.OPEN.value,
                    "answer": "The first observation provides partial progress.",
                    "evidence_ids": ["EV_DEFECT"],
                    "unavailable_reason": None,
                }
            ]

        client = RecordingNextActionClient(report_partial_progress)
        outcome = QwenNextActionPlanner(client).decide_with_review(
            goal=goal(),
            questions=questions(),
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
        )

        self.assertEqual(len(client.requests), 1)
        self.assertEqual(
            outcome.decision.next_action.kind,
            ActionKind.FIND_SHARED_EXPOSURE.value,
        )
        self.assertEqual(outcome.decision.question_updates, [])
        self.assertEqual(
            outcome.question_update_reviews[0].reason_code,
            QuestionUpdateReasonCode.NON_TERMINAL_STATUS.value,
        )

    def test_valid_question_update_requires_existing_evidence(self) -> None:
        def close_defect_question(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    "question_id": "Q_DEFECT",
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "The selected Lot has a scratch signature.",
                    "evidence_ids": ["EV_DEFECT"],
                    "unavailable_reason": None,
                }
            ]

        decision = QwenNextActionPlanner(
            RecordingNextActionClient(close_defect_question)
        ).decide(
            goal=goal(),
            questions=questions(),
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
        )

        self.assertEqual(len(decision.question_updates), 1)
        self.assertEqual(
            decision.question_updates[0].status,
            EvidenceGapStatus.CLOSED.value,
        )
        self.assertEqual(
            PlannerDecision.from_dict(decision.to_dict()),
            decision,
        )

    def test_open_question_update_is_repaired_after_indexed_validation_feedback(
        self,
    ) -> None:
        def repair_open_update(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            if request.payload["output_attempt"] == 1:
                payload["question_updates"] = [
                    {
                        "question_id": "Q_DEFECT",
                        "status": EvidenceGapStatus.OPEN.value,
                        "answer": "The scratch signature is partially characterized.",
                        "evidence_ids": ["EV_DEFECT"],
                        "unavailable_reason": None,
                    }
                ]
                return
            payload["question_updates"] = [
                {
                    "question_id": "Q_DEFECT",
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "The selected Lot has a scratch signature.",
                    "evidence_ids": ["EV_DEFECT"],
                    "unavailable_reason": None,
                }
            ]

        client = RecordingNextActionClient(repair_open_update)
        decision = QwenNextActionPlanner(client).decide(
            goal=goal(),
            questions=questions(),
            findings=[
                finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
            ],
            action_records=[],
            tool_call_count=1,
        )

        self.assertEqual(len(client.requests), 2)
        self.assertIn(
            "question_updates[0].status must be closed or unavailable",
            str(client.requests[1].payload["previous_validation_error"]),
        )
        self.assertEqual(
            decision.question_updates[0].status,
            EvidenceGapStatus.CLOSED.value,
        )

    def test_qwen_cannot_send_a_legacy_full_question_update(self) -> None:
        def copy_full_question(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    **request.payload["questions"][0],
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "The scratch signature is characterized.",
                    "evidence_ids": ["EV_DEFECT"],
                    "unavailable_reason": None,
                }
            ]

        with self.assertRaises(QwenNextActionPlannerError) as captured:
            QwenNextActionPlanner(
                RecordingNextActionClient(copy_full_question)
            ).decide(
                goal=goal(),
                questions=questions(),
                findings=[
                    finding(AgentKind.DEFECT_WAT.value, evidence_id="EV_DEFECT")
                ],
                action_records=[],
                tool_call_count=1,
            )

        self.assertTrue(
            all(
                "question_updates[0]" in error and "unknown fields" in error
                for error in captured.exception.validation_errors
            )
        )

    def test_invalid_safety_boundaries_fail_twice_with_typed_fallback(self) -> None:
        cases: list[
            tuple[
                str,
                Mutation,
                list[AgentFinding],
                list[ActionRecord],
                int,
            ]
        ] = []

        def unsupported(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.ASSESS_IMPACT_SCOPE.value,
                    agent=AgentKind.MES.value,
                )
            )

        cases.append(("allowlist", unsupported, [], [], 0))

        def wrong_agent(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                    agent=AgentKind.MES.value,
                )
            )

        cases.append(("kind_agent", wrong_agent, [], [], 0))

        def missing_prerequisite(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_FDC_SPC.value,
                    agent=AgentKind.FDC.value,
                )
            )

        cases.append(("precondition", missing_prerequisite, [], [], 0))

        def empty_scope(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["next_action"]["scope"] = {}

        cases.append(("scope", empty_scope, [], [], 0))

        def changed_lot(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["next_action"]["scope"]["lot_id"] = "LOT_99"

        cases.append(("lot_boundary", changed_lot, [], [], 0))

        def unknown_evidence(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["next_action"]["required_evidence_ids"] = ["EV_UNKNOWN"]

        cases.append(("evidence", unknown_evidence, [], [], 0))

        def multiple_attempts(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["next_action"]["max_attempts"] = 2

        cases.append(("max_attempts", multiple_attempts, [], [], 0))

        def unknown_question(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["target_question_ids"] = ["Q_UNREQUESTED"]

        cases.append(("question", unknown_question, [], [], 0))

        def unknown_question_update(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    "question_id": "Q_UNKNOWN",
                    "status": EvidenceGapStatus.UNAVAILABLE.value,
                    "answer": None,
                    "evidence_ids": [],
                    "unavailable_reason": "No registered source can answer it.",
                }
            ]

        cases.append(("question_update", unknown_question_update, [], [], 0))

        def unsupported_question_answer(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            payload["question_updates"] = [
                {
                    "question_id": "Q_DEFECT",
                    "status": EvidenceGapStatus.CLOSED.value,
                    "answer": "An unsupported answer.",
                    "evidence_ids": ["EV_NOT_OBSERVED"],
                    "unavailable_reason": None,
                }
            ]

        cases.append(
            ("question_evidence", unsupported_question_answer, [], [], 0)
        )

        prior_mes = action_record(
            kind=ActionKind.FIND_SHARED_EXPOSURE.value,
            agent=AgentKind.MES.value,
            scope={"lot_id": "LOT_01", "module": "DIFFUSION"},
        )

        def second_mes(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(model_act_payload(request))

        cases.append(
            (
                "single_use_mes",
                second_mes,
                [],
                [prior_mes],
                1,
            )
        )

        prior_same_scope = action_record(
            kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
            agent=AgentKind.DEFECT_WAT.value,
            scope={"lot_id": "LOT_01", "module": "CU_CMP"},
        )

        def duplicate_scope(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                    agent=AgentKind.DEFECT_WAT.value,
                )
            )

        cases.append(("dedup", duplicate_scope, [], [prior_same_scope], 1))

        def premature_rca(payload: dict[str, Any], request: LLMRequest) -> None:
            payload.clear()
            payload.update(
                model_act_payload(
                    request,
                    kind=ActionKind.RUN_RCA_REASONING.value,
                    agent=AgentKind.RCA_REASONING.value,
                )
            )

        cases.append(
            (
                "shared_pattern_precondition",
                premature_rca,
                [
                    finding(AgentKind.MES.value),
                    finding(AgentKind.FDC.value),
                    finding(AgentKind.DEFECT_WAT.value),
                ],
                [],
                3,
            )
        )

        for name, mutation, current_findings, records, tool_calls in cases:
            with self.subTest(boundary=name):
                client = RecordingNextActionClient(mutation)
                with self.assertRaises(QwenNextActionPlannerError) as context:
                    QwenNextActionPlanner(client).decide(
                        goal=goal(),
                        questions=questions(),
                        findings=current_findings,
                        action_records=records,
                        tool_call_count=tool_calls,
                    )
                error = context.exception
                self.assertEqual(error.attempts, 2)
                self.assertEqual(error.fallback_mode, "controlled_react")
                self.assertEqual(error.goal_id, "GOAL_LOT_01")
                self.assertEqual(error.completed_steps, len(records))
                self.assertEqual(len(error.validation_errors), 2)
                self.assertEqual(len(client.requests), 2)

    def test_runtime_budget_forces_python_stop_without_an_llm_call(self) -> None:
        eight_records = [
            action_record(
                kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                agent=AgentKind.DEFECT_WAT.value,
                scope={"lot_id": "LOT_01", "step": index},
                action_id=f"PRIOR_{index}",
            )
            for index in range(8)
        ]

        for boundary, records, tool_calls in (
            ("max_steps", eight_records, 8),
            ("max_tool_calls", [], goal().max_tool_calls),
        ):
            with self.subTest(boundary=boundary):
                client = RecordingNextActionClient()
                decision = QwenNextActionPlanner(client).decide(
                    goal=goal(),
                    questions=questions(),
                    findings=[],
                    action_records=records,
                    tool_call_count=tool_calls,
                )

                self.assertEqual(
                    decision.decision_type,
                    DecisionType.STOP.value,
                )
                self.assertEqual(
                    decision.goal_status,
                    GoalStatus.BUDGET_EXHAUSTED.value,
                )
                self.assertEqual(
                    decision.stop_reason,
                    StopReason.BUDGET_EXHAUSTED.value,
                )
                self.assertEqual(len(client.requests), 0)
                self.assertTrue(
                    all(
                        update.status == EvidenceGapStatus.UNAVAILABLE.value
                        for update in decision.question_updates
                    )
                )

    def test_prompt_and_runtime_keep_tool_dispatch_outside_planner(self) -> None:
        import yield_rca_core.next_action_planner as next_action_planner

        prompt = load_prompt("next_action_planner", "v1").lower()
        self.assertIn("choose exactly one entry from allowed_actions", prompt)
        self.assertIn("impact lot is a result", prompt)
        self.assertIn("does not answer this specific question", prompt)
        source = inspect.getsource(next_action_planner).lower()
        self.assertNotIn("yield_rca_core.repositories", source)
        self.assertNotIn("yield_rca_core.tool_layer", source)


if __name__ == "__main__":
    unittest.main()
