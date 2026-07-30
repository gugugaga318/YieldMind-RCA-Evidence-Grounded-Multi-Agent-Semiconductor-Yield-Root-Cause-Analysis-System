from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import (  # noqa: E402
    AgentKind,
    FindingKind,
    ModelValidationError,
    TaskPlan,
)
from yield_rca_core.planner_agent import (  # noqa: E402
    DEFAULT_PLANNABLE_AGENTS,
    PlannerAgent,
    PlannerConfigurationError,
)

YIELD_QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


class PlannerAgentContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = PlannerAgent()

    def test_yield_drop_query_produces_required_structured_tasks(self) -> None:
        plan = self.planner.plan(YIELD_QUERY, plan_id="PLAN_GOLDEN")

        self.assertIsInstance(plan, TaskPlan)
        self.assertEqual(plan.plan_id, "PLAN_GOLDEN")
        self.assertEqual(
            [task.agent for task in plan.tasks],
            [
                AgentKind.MES.value,
                AgentKind.FDC.value,
                AgentKind.DEFECT_WAT.value,
                AgentKind.KNOWLEDGE.value,
                AgentKind.KNOWLEDGE.value,
                AgentKind.RCA_REASONING.value,
                AgentKind.IMPROVEMENT.value,
            ],
        )
        self.assertTrue(all(task.status == "pending" for task in plan.tasks))

    def test_plan_is_an_acyclic_dependency_graph(self) -> None:
        plan = self.planner.plan(YIELD_QUERY)
        tasks = {task.task_id: task for task in plan.tasks}

        self.assertEqual(tasks["task_mes"].depends_on, [])
        self.assertEqual(tasks["task_fdc"].depends_on, ["task_mes"])
        self.assertEqual(tasks["task_defect_wat"].depends_on, ["task_mes"])
        self.assertEqual(
            tasks["task_knowledge_discovery"].depends_on,
            ["task_mes", "task_fdc", "task_defect_wat"],
        )
        self.assertEqual(
            tasks["task_knowledge_validation"].depends_on,
            ["task_knowledge_discovery"],
        )
        self.assertEqual(
            tasks["task_knowledge_discovery"].finding_kind,
            FindingKind.KNOWLEDGE_DISCOVERY.value,
        )
        self.assertEqual(
            tasks["task_knowledge_validation"].finding_kind,
            FindingKind.KNOWLEDGE_VALIDATION.value,
        )
        self.assertEqual(
            tasks["task_rca"].depends_on,
            [
                "task_mes",
                "task_fdc",
                "task_defect_wat",
                "task_knowledge_discovery",
                "task_knowledge_validation",
            ],
        )
        self.assertEqual(tasks["task_improvement"].depends_on, ["task_rca"])
        self.assertEqual(TaskPlan.from_dict(plan.to_dict()), plan)

    def test_lot_query_produces_same_registered_agent_graph_with_lot_context(self) -> None:
        plan = self.planner.plan(
            "Analyze the abnormal Lot and identify impact Lots.",
            lot_id="lot_a_001",
        )

        self.assertEqual(plan.tasks[0].inputs["investigation_mode"], "lot")
        self.assertEqual(plan.tasks[0].inputs["lot_id"], "LOT_A_001")
        self.assertNotIn("product_id", plan.tasks[0].inputs)
        self.assertEqual(
            [task.agent for task in plan.tasks],
            [
                AgentKind.MES.value,
                AgentKind.FDC.value,
                AgentKind.DEFECT_WAT.value,
                AgentKind.KNOWLEDGE.value,
                AgentKind.KNOWLEDGE.value,
                AgentKind.RCA_REASONING.value,
                AgentKind.IMPROVEMENT.value,
            ],
        )
        self.assertEqual(TaskPlan.from_dict(plan.to_dict()), plan)

    def test_plan_only_references_registered_agents(self) -> None:
        plan = self.planner.plan(YIELD_QUERY)

        self.assertTrue({task.agent for task in plan.tasks} <= DEFAULT_PLANNABLE_AGENTS)
        self.assertNotIn(AgentKind.PLANNER.value, {task.agent for task in plan.tasks})
        self.assertNotIn(AgentKind.SUPERVISOR.value, {task.agent for task in plan.tasks})
        self.assertNotIn(AgentKind.REPORT.value, {task.agent for task in plan.tasks})

    def test_missing_required_agent_registration_is_rejected(self) -> None:
        planner = PlannerAgent(
            registered_agents=DEFAULT_PLANNABLE_AGENTS - {AgentKind.KNOWLEDGE.value}
        )

        with self.assertRaises(PlannerConfigurationError):
            planner.plan(YIELD_QUERY)

    def test_unknown_agent_registration_is_rejected(self) -> None:
        with self.assertRaises(PlannerConfigurationError):
            PlannerAgent(registered_agents=frozenset({"unregistered_agent"}))

    def test_query_context_is_structured_without_data_lookup(self) -> None:
        plan = self.planner.plan(YIELD_QUERY)
        mes_task = plan.tasks[0]

        self.assertEqual(mes_task.inputs["product_id"], "40N_SOC")
        self.assertEqual(
            mes_task.inputs["time_window"],
            {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        )

        chinese_plan = self.planner.plan("分析40N_SOC在2026年7月的良率下降")
        self.assertEqual(chinese_plan.tasks[0].inputs["product_id"], "40N_SOC")
        chinese_window = chinese_plan.tasks[0].inputs["time_window"]
        self.assertEqual(chinese_window["start_date"], "2026-07-01")
        self.assertEqual(chinese_window["end_date"], "2026-07-31")

    def test_default_plan_id_is_stable_for_the_same_query(self) -> None:
        first = self.planner.plan(YIELD_QUERY)
        second = self.planner.plan(YIELD_QUERY)

        self.assertEqual(first.plan_id, second.plan_id)

    def test_blank_query_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            self.planner.plan("  ")

    def test_planner_has_no_runtime_data_or_tool_dependency(self) -> None:
        import yield_rca_core.planner_agent as planner_agent

        source = inspect.getsource(planner_agent).lower()
        forbidden_dependencies = (
            "yield_rca_core.repositories",
            "yield_rca_core.tool_layer",
            "csvfabrepository",
            "postgresfabrepository",
            "psycopg",
            "sqlalchemy",
            "toolinput",
            "tooloutput",
            "agentfinding",
            "hypothesis",
            "report(",
        )
        for dependency in forbidden_dependencies:
            self.assertNotIn(dependency, source)

    def test_plan_does_not_contain_a_golden_case_conclusion_or_report(self) -> None:
        serialized = str(self.planner.plan(YIELD_QUERY).to_dict()).lower()

        self.assertNotIn("cmp_cu03_ch02", serialized)
        self.assertNotIn("slurry delivery degradation", serialized)
        self.assertNotIn("recommended_actions", serialized)
        self.assertNotIn("markdown", serialized)


if __name__ == "__main__":
    unittest.main()
