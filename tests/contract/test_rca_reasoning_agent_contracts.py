from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import (  # noqa: E402
    AgentFinding,
    AgentKind,
    Hypothesis,
    HypothesisStatus,
    ModelValidationError,
)
from yield_rca_core.rca_reasoning_agent import RCAReasoningAgent  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.specialist_agents import (  # noqa: E402
    DefectWATAgent,
    FDCAgent,
    KnowledgeAgent,
    MESAgent,
)
from yield_rca_core.tool_layer import (  # noqa: E402
    AnalyzeLotGenealogyTool,
    AnalyzeParameterShiftTool,
    FindAffectedLotsTool,
    FindOocEventsTool,
    PerformBasicSpcAnalysisTool,
    RetrieveSimilarCaseTool,
    SummarizeDefectWatTool,
)

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
AFFECTED_LOTS = [f"LOT_A_{index:03d}" for index in range(1, 21)]
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


def golden_specialist_findings() -> list[AgentFinding]:
    repository = CsvFabRepository(SEED_DIR)
    mes_finding = MESAgent(
        FindAffectedLotsTool(repository),
        AnalyzeLotGenealogyTool(repository),
    ).analyze(
        request_id="REQ_RCA_MES",
        product_id="40N_SOC",
        start_date="2026-07-01",
        end_date="2026-07-31",
    )
    fdc_finding = FDCAgent(
        AnalyzeParameterShiftTool(repository),
        FindOocEventsTool(repository),
        PerformBasicSpcAnalysisTool(repository),
    ).analyze(
        request_id="REQ_RCA_FDC",
        lot_ids=AFFECTED_LOTS,
        operation_no="6400",
        equipment_id="CMP_CU03",
        chamber_id="CMP_CU03_CH02",
    )
    defect_wat_finding = DefectWATAgent(
        SummarizeDefectWatTool(repository)
    ).analyze(
        request_id="REQ_RCA_DEFECT_WAT",
        lot_ids=AFFECTED_LOTS,
    )
    knowledge_finding = KnowledgeAgent(
        RetrieveSimilarCaseTool(repository)
    ).analyze(
        request_id="REQ_RCA_KNOWLEDGE",
        query="Cu CMP slurry flow scratch leakage",
        module="Cu CMP",
        equipment_type="CMP",
    )
    return [mes_finding, fdc_finding, defect_wat_finding, knowledge_finding]


class RCAReasoningAgentContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.specialist_findings = golden_specialist_findings()
        cls.result = RCAReasoningAgent().analyze(
            request_id="REQ_GOLDEN_RCA",
            findings=cls.specialist_findings,
        )

    def test_golden_case_returns_expected_supported_root_cause(self) -> None:
        self.assertEqual(self.result.agent, AgentKind.RCA_REASONING.value)
        self.assertEqual(self.result.details["root_cause"], EXPECTED_ROOT_CAUSE)
        self.assertEqual(self.result.details["status"], HypothesisStatus.SUPPORTED.value)
        self.assertGreaterEqual(self.result.confidence, 0.85)
        self.assertLessEqual(self.result.confidence, 0.95)
        self.assertEqual(
            [warning.warning_id for warning in self.result.warnings],
            ["WARN_SPC_BASELINE_INSUFFICIENT"],
        )

        hypothesis = Hypothesis.from_dict(self.result.details["hypothesis"])
        self.assertEqual(hypothesis.root_cause, EXPECTED_ROOT_CAUSE)
        self.assertEqual(hypothesis.confidence, self.result.confidence)
        self.assertLessEqual(set(hypothesis.evidence_ids), set(self.result.evidence_ids))
        self.assertEqual(hypothesis.rank, 1)
        self.assertTrue(hypothesis.supporting_evidence_ids)

    def test_result_exposes_active_hypothesis_engine_decision(self) -> None:
        engine = self.result.details["hypothesis_engine_result"]
        self.assertEqual(engine["engine"], "hypothesis_v1")
        self.assertEqual(engine["mode"], "active")
        self.assertEqual(self.result.details["reasoning_engine"], "hypothesis_v1")

    def test_ranked_candidates_are_traceable_and_keep_supported_root_cause_first(self) -> None:
        candidates = self.result.details["ranked_candidates"]

        self.assertGreaterEqual(len(candidates), 1)
        self.assertLessEqual(len(candidates), 3)
        self.assertEqual(candidates[0]["root_cause"], EXPECTED_ROOT_CAUSE)
        for candidate in candidates:
            self.assertTrue(candidate["evidence_ids"])
            self.assertLessEqual(set(candidate["evidence_ids"]), set(self.result.evidence_ids))

    def test_every_conclusion_and_action_references_known_evidence(self) -> None:
        known_evidence = set(self.result.evidence_ids)
        self.assertLessEqual(set(self.result.details["root_cause_evidence_ids"]), known_evidence)

        evidence_chain = self.result.details["evidence_chain"]
        self.assertEqual(len(evidence_chain), 4)
        for item in evidence_chain:
            self.assertTrue(item["claim"])
            self.assertTrue(item["evidence_ids"])
            self.assertTrue(set(item["evidence_ids"]) <= known_evidence)

        actions = self.result.details["recommended_actions"]
        self.assertEqual(len(actions), 4)
        self.assertEqual(actions[0]["action"], "Inspect slurry pump")
        for action in actions:
            self.assertTrue(action["action"])
            self.assertTrue(action["evidence_ids"])
            self.assertTrue(set(action["evidence_ids"]) <= known_evidence)

    def test_reasoning_result_is_serializable_and_preserves_evidence(self) -> None:
        restored = AgentFinding.from_dict(self.result.to_dict())

        self.assertEqual(restored, self.result)
        evidence_payload_ids = {
            item["evidence_id"] for item in restored.details["evidence"]
        }
        self.assertEqual(evidence_payload_ids, set(restored.evidence_ids))

    def test_insufficient_evidence_returns_inconclusive(self) -> None:
        result = RCAReasoningAgent().analyze(
            request_id="REQ_INCONCLUSIVE_RCA",
            findings=[self.specialist_findings[0]],
        )

        self.assertEqual(result.details["root_cause"], HypothesisStatus.INCONCLUSIVE.value)
        self.assertEqual(result.details["status"], HypothesisStatus.INCONCLUSIVE.value)
        self.assertLessEqual(result.confidence, 0.60)
        self.assertEqual(result.details["recommended_actions"], [])
        warning_ids = {warning.warning_id for warning in result.warnings}
        self.assertIn("WARN_RCA_MISSING_FINDINGS", warning_ids)
        self.assertIn("WARN_RCA_INCONCLUSIVE", warning_ids)
        self.assertTrue(result.evidence_ids)

    def test_empty_or_non_specialist_input_is_rejected(self) -> None:
        agent = RCAReasoningAgent()
        with self.assertRaises(ModelValidationError):
            agent.analyze(request_id="REQ_EMPTY_RCA", findings=[])

        invalid_finding = AgentFinding(
            finding_id="FINDING_PLANNER",
            agent=AgentKind.PLANNER.value,
            summary="Planner output is not Specialist evidence.",
            confidence=1.0,
            evidence_ids=["EV_INVALID"],
        )
        with self.assertRaises(ModelValidationError):
            agent.analyze(request_id="REQ_INVALID_RCA", findings=[invalid_finding])

    def test_reasoning_agent_has_no_data_tool_or_report_dependency(self) -> None:
        import yield_rca_core.rca_reasoning_agent as reasoning_agent

        source = inspect.getsource(reasoning_agent).lower()
        forbidden_dependencies = (
            "yield_rca_core.repositories",
            "yield_rca_core.tool_layer",
            "csvfabrepository",
            "postgresfabrepository",
            "psycopg",
            "sqlalchemy",
            "toolinput",
            "tooloutput",
            "markdown",
            "report(",
        )
        for dependency in forbidden_dependencies:
            self.assertNotIn(dependency, source)


if __name__ == "__main__":
    unittest.main()
