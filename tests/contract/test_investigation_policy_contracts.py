from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import (  # noqa: E402
    ActionKind,
    ActionRecord,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
)
from yield_rca_core.investigation_policy import InvestigationPolicy  # noqa: E402
from yield_rca_core.models import AgentFinding, AgentKind  # noqa: E402


def finding(agent: str) -> AgentFinding:
    evidence_id = f"EV_{agent.upper()}"
    return AgentFinding(
        finding_id=f"FINDING_{agent.upper()}",
        agent=agent,
        summary=f"{agent} observation",
        confidence=0.8,
        evidence_ids=[evidence_id],
        details={},
    )


class InvestigationPolicyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = InvestigationPolicy()
        self.goal = InvestigationGoal(
            goal_id="GOAL_SCRATCH",
            intent=InvestigationIntent.ROOT_CAUSE.value,
            summary="Investigate known Cu CMP scratch.",
            known_facts={"lot_id": "LOT_01", "module": "CU", "defect": "scratch"},
        )

    def test_root_cause_path_validates_shared_defect_before_fdc_and_rca(self) -> None:
        first = self.policy.next_action(
            goal=self.goal, findings=[], action_records=[], tool_call_count=0
        )
        self.assertEqual(first.next_action.kind, ActionKind.INSPECT_DEFECT_PATTERN.value)

        second = self.policy.next_action(
            goal=self.goal,
            findings=[finding(AgentKind.DEFECT_WAT.value)],
            action_records=[],
            tool_call_count=1,
        )
        self.assertEqual(second.next_action.kind, ActionKind.FIND_SHARED_EXPOSURE.value)

        third = self.policy.next_action(
            goal=self.goal,
            findings=[finding(AgentKind.DEFECT_WAT.value), finding(AgentKind.MES.value)],
            action_records=[],
            tool_call_count=2,
        )
        self.assertEqual(third.next_action.kind, ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value)

        fourth = self.policy.next_action(
            goal=self.goal,
            findings=[
                finding(AgentKind.DEFECT_WAT.value),
                finding(AgentKind.MES.value),
                finding(AgentKind.FDC.value),
            ],
            action_records=[
                ActionRecord(
                    action=InvestigationAction(
                        action_id="GOAL_SCRATCH:validate_shared_defect_pattern",
                        kind=ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value,
                        agent=AgentKind.DEFECT_WAT.value,
                        reason="Shared exposure comparison.",
                    ),
                    status="completed",
                    decision_summary="Comparison completed.",
                )
            ],
            tool_call_count=3,
        )
        self.assertEqual(fourth.next_action.kind, ActionKind.RUN_RCA_REASONING.value)

    def test_scope_goal_stops_after_mes_scope_observation(self) -> None:
        goal = InvestigationGoal(
            goal_id="GOAL_SCOPE",
            intent=InvestigationIntent.IMPACT_SCOPE.value,
            summary="Identify impact Lots.",
            known_facts={"lot_id": "LOT_01"},
        )
        decision = self.policy.next_action(
            goal=goal,
            findings=[finding(AgentKind.MES.value)],
            action_records=[],
            tool_call_count=1,
        )
        self.assertEqual(decision.goal_status, "satisfied")
        self.assertEqual(decision.stop_reason, "goal_satisfied")

    def test_budget_and_critical_contradiction_stop_the_loop(self) -> None:
        budget = self.policy.next_action(
            goal=self.goal,
            findings=[],
            action_records=[],
            tool_call_count=self.goal.max_tool_calls,
        )
        self.assertEqual(budget.stop_reason, "budget_exhausted")

        conflict = self.policy.next_action(
            goal=self.goal,
            findings=[],
            action_records=[],
            tool_call_count=0,
            critical_contradictions=[
                "FDC behavior conflicts with the proposed physical mechanism."
            ],
        )
        self.assertEqual(conflict.conclusion_level, "conflicted")

    def test_repeated_action_is_not_reissued(self) -> None:
        record = ActionRecord(
            action=InvestigationAction(
                action_id="GOAL_SCRATCH:inspect_defect_pattern",
                kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
                agent=AgentKind.DEFECT_WAT.value,
                reason="Initial inspection.",
            ),
            status="completed",
            decision_summary="No usable defect observation was returned.",
        )
        decision = self.policy.next_action(
            goal=self.goal,
            findings=[],
            action_records=[record],
            tool_call_count=1,
        )
        self.assertEqual(decision.stop_reason, "no_allowed_action")


if __name__ == "__main__":
    unittest.main()
