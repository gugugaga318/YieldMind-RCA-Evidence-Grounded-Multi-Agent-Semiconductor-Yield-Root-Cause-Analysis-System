from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evaluation import ScenarioFabRepository  # noqa: E402
from yield_rca_core.improvement_agent import ImprovementAgent  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMOutputValidationError,
    LLMRequest,
    LLMResponse,
)
from yield_rca_core.models import AgentFinding, AgentKind, RCAState  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow, build_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def improvement_inputs(state: RCAState) -> list[AgentFinding]:
    return [
        finding
        for finding in state.findings
        if finding.agent
        in {
            AgentKind.MES.value,
            AgentKind.FDC.value,
            AgentKind.DEFECT_WAT.value,
            AgentKind.KNOWLEDGE.value,
            AgentKind.RCA_REASONING.value,
        }
    ]


class InventedEvidenceClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        response = super().complete_json(request)
        if request.prompt_name != "improvement":
            return response
        return LLMResponse(
            data={**response.data, "evidence_ids": [*response.data["evidence_ids"], "EV_FAKE"]},
            usage=response.usage,
        )


class ImprovementAgentContractTest(unittest.TestCase):
    supported_state: ClassVar[RCAState]
    inconclusive_state: ClassVar[RCAState]

    @classmethod
    def setUpClass(cls) -> None:
        workflow = build_csv_workflow(SEED_DIR)
        cls.supported_state = workflow.run(
            "Analyze abnormal Lot LOT_A_015 and identify impact Lots.",
            job_id="JOB_IMPROVEMENT_SUPPORTED",
            lot_id="LOT_A_015",
        )
        cls.inconclusive_state = workflow.run(
            "Analyze abnormal Lot LOT_A_038 with an isolated scratch.",
            job_id="JOB_IMPROVEMENT_INCONCLUSIVE",
            lot_id="LOT_A_038",
        )

    def test_supported_rca_produces_all_improvement_layers_with_evidence(self) -> None:
        finding = ImprovementAgent().analyze(
            request_id="REQ_IMPROVEMENT_SUPPORTED",
            findings=improvement_inputs(self.supported_state),
        )

        self.assertEqual(finding.agent, AgentKind.IMPROVEMENT.value)
        self.assertEqual(finding.details["scope_assessment"]["level"], "fab")
        self.assertIn("cross_lot", finding.details["scope_assessment"]["criteria"])
        self.assertIn(
            "historical_confirmed_case",
            finding.details["scope_assessment"]["criteria"],
        )
        recommendations = finding.details["recommendations"]
        for category in (
            "containment_actions",
            "corrective_actions",
            "recipe_optimization",
            "preventive_actions",
            "fab_system_optimization",
        ):
            self.assertTrue(recommendations[category], category)
        known_evidence = {item.evidence_id for item in self.supported_state.evidence}
        self.assertTrue(set(finding.evidence_ids) <= known_evidence)
        for items in recommendations.values():
            for item in items:
                self.assertTrue(set(item["evidence_ids"]) <= known_evidence)
        self.assertEqual(
            finding.details["memory_status"],
            "candidate_ready_for_step_19_persistence",
        )
        self.assertTrue(finding.details["requires_two_engineer_approval"])

    def test_inconclusive_rca_withholds_specific_and_fab_level_actions(self) -> None:
        finding = ImprovementAgent().analyze(
            request_id="REQ_IMPROVEMENT_INCONCLUSIVE",
            findings=improvement_inputs(self.inconclusive_state),
        )

        recommendations = finding.details["recommendations"]
        self.assertTrue(recommendations["containment_actions"])
        self.assertEqual(recommendations["corrective_actions"], [])
        self.assertEqual(recommendations["recipe_optimization"], [])
        self.assertEqual(recommendations["preventive_actions"], [])
        self.assertEqual(recommendations["fab_system_optimization"], [])
        self.assertFalse(finding.details["scope_assessment"]["fab_level_supported"])
        self.assertEqual(
            [warning.warning_id for warning in finding.warnings],
            ["WARN_IMPROVEMENT_RCA_INCONCLUSIVE"],
        )

    def test_recipe_change_recommendation_requires_engineering_approval(self) -> None:
        repository = ScenarioFabRepository(
            CsvFabRepository(SEED_DIR),
            "EVAL_RECIPE_VERSION_CHANGE",
        )
        state = build_workflow(repository).run(
            "Analyze abnormal Lot LOT_A_038.",
            job_id="JOB_IMPROVEMENT_RECIPE",
            lot_id="LOT_A_038",
        )
        finding = next(
            item for item in state.findings if item.agent == AgentKind.IMPROVEMENT.value
        )
        recipe_action = finding.details["recommendations"]["recipe_optimization"][0]

        self.assertIn("Process Engineer approval", recipe_action["action"])
        self.assertEqual(recipe_action["evidence_ids"], ["EV_MES_RECIPE_CHANGE"])

    def test_llm_cannot_add_evidence_to_improvement_summary(self) -> None:
        with self.assertRaisesRegex(LLMOutputValidationError, "preserve exactly"):
            ImprovementAgent(
                llm_client=InventedEvidenceClient(),
                agent_mode="fake",
            ).analyze(
                request_id="REQ_IMPROVEMENT_INVALID_LLM",
                findings=improvement_inputs(self.supported_state),
            )

    def test_improvement_agent_has_no_repository_or_tool_dependency(self) -> None:
        import yield_rca_core.improvement_agent as improvement_agent

        source = inspect.getsource(improvement_agent).lower()
        for dependency in (
            "yield_rca_core.repositories",
            "yield_rca_core.tool_layer",
            "csvfabrepository",
            "postgresfabrepository",
            "psycopg",
            ".rows(",
        ):
            self.assertNotIn(dependency, source)


if __name__ == "__main__":
    unittest.main()
