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
            LLM_REACT_EXECUTABLE_ACTION_KINDS,
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

    def test_valid_question_update_requires_existing_evidence(self) -> None:
        def close_defect_question(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            original = request.payload["questions"][0]
            payload["question_updates"] = [
                {
                    **original,
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

        def unsupported_question_answer(
            payload: dict[str, Any],
            request: LLMRequest,
        ) -> None:
            payload.clear()
            payload.update(model_act_payload(request))
            original = request.payload["questions"][0]
            payload["question_updates"] = [
                {
                    **original,
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
        source = inspect.getsource(next_action_planner).lower()
        self.assertNotIn("yield_rca_core.repositories", source)
        self.assertNotIn("yield_rca_core.tool_layer", source)


if __name__ == "__main__":
    unittest.main()
