from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    AgentKind,
    EntityType,
    Evidence,
    EvidenceBuilder,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    InvestigationAction,
    InvestigationQuestion,
    QuestionEvidenceLink,
    QuestionEvidenceRelation,
    QuestionKind,
    QuestionUpdateDisposition,
    ToolInput,
    review_question_updates,
)
from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ActionRecord,
    ConclusionLevel,
    DecisionType,
    GoalStatus,
    InvestigationValidationError,
    PlannerDecision,
    StopReason,
)
from yield_rca_core.next_action_planner import QwenNextActionPlanner  # noqa: E402
from yield_rca_core.question_evidence import QuestionEvidenceResolver  # noqa: E402


def question(kind: QuestionKind, *, question_id: str = "Q_QUESTION") -> InvestigationQuestion:
    return InvestigationQuestion(
        question_id=question_id,
        goal_id="GOAL_LOT_01",
        question="Which typed observation answers this gap?",
        rationale="The question is required by the investigation goal.",
        question_kind=kind.value,
        scope={"lot_id": "LOT_01", "module": "CU_CMP"},
    )


def action(
    kind: ActionKind,
    *,
    action_id: str = "ACT_1",
    lot_id: str = "LOT_01",
) -> InvestigationAction:
    agent = {
        ActionKind.INSPECT_DEFECT_PATTERN: AgentKind.DEFECT_WAT.value,
        ActionKind.FIND_SHARED_EXPOSURE: AgentKind.MES.value,
        ActionKind.INSPECT_FDC_SPC: AgentKind.FDC.value,
    }.get(kind, AgentKind.RCA_REASONING.value)
    return InvestigationAction(
        action_id=action_id,
        kind=kind.value,
        agent=agent,
        reason="Collect a typed observation for the open Question.",
        inputs={"lot_id": lot_id},
        scope={"lot_id": lot_id, "module": "CU_CMP"},
    )


def evidence(
    evidence_id: str,
    evidence_type: EvidenceType,
    *,
    source_type: EvidenceSourceType = EvidenceSourceType.FDC,
    entity_type: EntityType = EntityType.LOT,
) -> Evidence:
    return EvidenceBuilder.from_tool(
        tool_input=ToolInput(
            tool_name="test_tool",
            request_id=f"request:{evidence_id}",
            parameters={"lot_id": "LOT_01"},
            requested_by=AgentKind.FDC.value,
        ),
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        source_type=source_type,
        source_id=f"source:{evidence_id}",
        observation="A typed test observation is available.",
        entities=[EvidenceEntity(entity_type=entity_type.value, entity_id="LOT_01")],
        confidence=0.9,
    )


def record(item: InvestigationAction, evidence_ids: list[str]) -> ActionRecord:
    return ActionRecord(
        action=item,
        status="completed",
        produced_finding_ids=[f"FINDING:{item.action_id}"],
        produced_evidence_ids=evidence_ids,
        decision_summary="Typed test observation completed.",
    )


class QuestionEvidenceContractTest(unittest.TestCase):
    def test_link_round_trip_and_relation_validation(self) -> None:
        link = QuestionEvidenceLink(
            question_id="Q_QUESTION",
            evidence_id="EV_FDC",
            action_id="ACT_1",
            relation=QuestionEvidenceRelation.SUPPORTS.value,
            matched_evidence_group="process_anomaly",
            reason="The typed FDC signal matches the mechanism Question.",
        )
        self.assertEqual(QuestionEvidenceLink.from_dict(link.to_dict()), link)
        with self.assertRaises(InvestigationValidationError):
            QuestionEvidenceLink(
                question_id="Q_QUESTION",
                evidence_id="EV_FDC",
                action_id="ACT_1",
                relation="irrelevant",
                matched_evidence_group="process_anomaly",
                reason="invalid",
            )

    def test_fdc_evidence_does_not_answer_unsupported_material_trace(self) -> None:
        item = evidence("EV_FDC", EvidenceType.PARAMETER_DEVIATION)
        item_action = action(ActionKind.INSPECT_FDC_SPC)
        links = QuestionEvidenceResolver().resolve(
            questions=[question(QuestionKind.MATERIAL_TRACE)],
            action_record=record(item_action, [item.evidence_id]),
            evidence=[item],
        )
        self.assertEqual(links, [])

    def test_process_mechanism_requires_all_closure_groups(self) -> None:
        q = question(QuestionKind.PROCESS_MECHANISM)
        decision = PlannerDecision(
            decision_id="STOP_1",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.STOP.value,
            reason="Stop with a partial mechanism observation.",
            goal_status=GoalStatus.BLOCKED.value,
            proposed_conclusion_level=ConclusionLevel.INCONCLUSIVE.value,
            stop_reason=StopReason.NO_ALLOWED_ACTION.value,
        )
        item = evidence("EV_FDC", EvidenceType.PARAMETER_DEVIATION)
        link = QuestionEvidenceLink(
            question_id=q.question_id,
            evidence_id=item.evidence_id,
            action_id="ACT_1",
            relation=QuestionEvidenceRelation.SUPPORTS.value,
            matched_evidence_group="process_anomaly",
            reason="Only the process anomaly group is present.",
        )
        outcome = review_question_updates(
            decision,
            [
                {
                    "question_id": q.question_id,
                    "status": "closed",
                    "answer": "The FDC signal is abnormal.",
                    "evidence_ids": [item.evidence_id],
                    "unavailable_reason": None,
                }
            ],
            questions=[q],
            available_evidence_ids={item.evidence_id},
            question_evidence_links=[link],
        )
        self.assertEqual(outcome.decision.question_updates, [])
        self.assertEqual(
            outcome.question_update_reviews[0].disposition,
            QuestionUpdateDisposition.REJECTED.value,
        )
        self.assertEqual(
            outcome.question_update_reviews[0].reason_code,
            "insufficient_evidence_coverage",
        )

    def test_second_same_direction_no_gain_is_rejected(self) -> None:
        q = question(QuestionKind.SPC_SIGNAL)
        first = action(ActionKind.INSPECT_FDC_SPC, action_id="ACT_1")
        second = action(ActionKind.INSPECT_FDC_SPC, action_id="ACT_2", lot_id="LOT_02")
        prior = PlannerDecision(
            decision_id="DECISION_1",
            goal_id="GOAL_LOT_01",
            decision_type=DecisionType.ACT.value,
            reason="Try the first SPC scope.",
            goal_status=GoalStatus.IN_PROGRESS.value,
            proposed_conclusion_level=ConclusionLevel.SIGNAL.value,
            next_action=first,
            target_question_ids=[q.question_id],
        )
        with self.assertRaisesRegex(InvestigationValidationError, "no_expected_evidence_gain"):
            QwenNextActionPlanner._validate_no_gain_boundary(
                action=second,
                target_questions=[q],
                action_records=[record(first, ["EV_NONE"])],
                prior_decisions=[prior],
                links=[],
            )

    def test_first_no_gain_has_no_previous_attempt_boundary(self) -> None:
        q = question(QuestionKind.SPC_SIGNAL)
        current = action(ActionKind.INSPECT_FDC_SPC)
        QwenNextActionPlanner._validate_no_gain_boundary(
            action=current,
            target_questions=[q],
            action_records=[],
            prior_decisions=[],
            links=[],
        )
