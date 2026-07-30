from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentFinding, AgentKind  # noqa: E402
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


class SpecialistAgentsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        repository = CsvFabRepository(SEED_DIR)
        self.mes_agent = MESAgent(
            find_affected_lots_tool=FindAffectedLotsTool(repository),
            analyze_lot_genealogy_tool=AnalyzeLotGenealogyTool(repository),
        )
        self.fdc_agent = FDCAgent(
            analyze_parameter_shift_tool=AnalyzeParameterShiftTool(repository),
            find_ooc_events_tool=FindOocEventsTool(repository),
            perform_basic_spc_analysis_tool=PerformBasicSpcAnalysisTool(repository),
        )
        self.defect_wat_agent = DefectWATAgent(
            summarize_defect_wat_tool=SummarizeDefectWatTool(repository)
        )
        self.knowledge_agent = KnowledgeAgent(
            retrieve_similar_case_tool=RetrieveSimilarCaseTool(repository)
        )

    def test_mes_agent_returns_affected_lot_and_commonality_finding(self) -> None:
        finding = self.mes_agent.analyze(
            request_id="REQ_GOLDEN_MES",
            product_id="40N_SOC",
            start_date="2026-07-01",
            end_date="2026-07-31",
        )

        self.assertIsInstance(finding, AgentFinding)
        self.assertEqual(finding.agent, AgentKind.MES.value)
        self.assertEqual(len(finding.details["affected_lots"]), 20)
        self.assertEqual(len(finding.details["normal_lots"]), 30)
        self.assertEqual(len(finding.details["yield_trend"]), 7)
        self.assertEqual(finding.details["yield_trend"][0]["pass_rate"], 100.0)
        self.assertEqual(finding.details["yield_trend"][-1]["pass_rate"], 0.0)
        self.assertEqual(
            finding.details["target_commonality"]["chamber_id"],
            "CMP_CU03_CH02",
        )
        self.assertEqual(finding.details["target_commonality"]["coverage"], 1.0)
        self.assertGreaterEqual(finding.confidence, 0.9)
        self.assertEqual(finding.warnings, [])
        self.assertTrue(
            {
                "EV_ANALYTICS_AFFECTED_LOTS",
                "EV_MES_COMMON_CHAMBER",
                "EV_HOLD_COMMENT",
            }.issubset(finding.evidence_ids)
        )

    def test_fdc_agent_returns_parameter_and_ooc_finding(self) -> None:
        finding = self.fdc_agent.analyze(
            request_id="REQ_GOLDEN_FDC",
            lot_ids=AFFECTED_LOTS,
            operation_no="6400",
            equipment_id="CMP_CU03",
            chamber_id="CMP_CU03_CH02",
        )

        self.assertIsInstance(finding, AgentFinding)
        self.assertEqual(finding.agent, AgentKind.FDC.value)
        summary = {item["parameter_name"]: item for item in finding.details["parameter_summary"]}
        self.assertEqual(summary["slurry_flow"]["avg_delta_percent"], -12.0)
        self.assertEqual(summary["endpoint_time"]["avg_observed"], 105.0)
        self.assertEqual(finding.details["event_count"], 20)
        self.assertEqual(finding.details["severity_counts"]["HIGH"], 20)
        self.assertEqual(
            [warning.warning_id for warning in finding.warnings],
            ["WARN_SPC_BASELINE_INSUFFICIENT"],
        )
        self.assertTrue(
            {
                "EV_FDC_SLURRY_FLOW",
                "EV_FDC_ENDPOINT_TIME",
                "EV_OOC_EVENTS",
                "EV_SPC_BASELINE_STATUS",
            }.issubset(finding.evidence_ids)
        )
        self.assertEqual(finding.details["spc_results"], [])
        self.assertEqual(
            finding.details["spc_baseline_insufficient_parameters"],
            ["endpoint_time", "slurry_flow"],
        )

    def test_defect_wat_agent_returns_consistent_physical_electrical_finding(self) -> None:
        finding = self.defect_wat_agent.analyze(
            request_id="REQ_GOLDEN_DEFECT_WAT",
            lot_ids=AFFECTED_LOTS,
        )

        self.assertIsInstance(finding, AgentFinding)
        self.assertEqual(finding.agent, AgentKind.DEFECT_WAT.value)
        self.assertEqual(finding.details["defect_counts"]["scratch"], 20)
        self.assertEqual(finding.details["defect_patterns"]["edge_dominant"], 20)
        self.assertEqual(finding.details["wat_fail_modes"]["leakage"], 20)
        self.assertTrue(finding.details["physical_electrical_consistent"])
        self.assertEqual(finding.warnings, [])
        self.assertTrue({"EV_DEFECT_SCRATCH", "EV_WAT_LEAKAGE"}.issubset(finding.evidence_ids))

    def test_knowledge_agent_returns_historical_case_finding(self) -> None:
        finding = self.knowledge_agent.analyze(
            request_id="REQ_GOLDEN_KNOWLEDGE",
            query="Cu CMP slurry flow scratch leakage",
            module="Cu CMP",
            equipment_type="CMP",
        )

        self.assertIsInstance(finding, AgentFinding)
        self.assertEqual(finding.agent, AgentKind.KNOWLEDGE.value)
        self.assertEqual(finding.details["top_case"]["case_id"], "RCA_CMP_2025_032")
        self.assertGreaterEqual(finding.confidence, 0.9)
        self.assertEqual(finding.evidence_ids, ["EV_KNOWLEDGE_MATCH"])
        self.assertEqual(finding.warnings, [])

    def test_agent_findings_are_serializable_and_evidence_is_traceable(self) -> None:
        findings = [
            self.mes_agent.analyze(
                request_id="REQ_SERIALIZE_MES",
                product_id="40N_SOC",
                start_date="2026-07-01",
                end_date="2026-07-31",
            ),
            self.fdc_agent.analyze(
                request_id="REQ_SERIALIZE_FDC",
                lot_ids=AFFECTED_LOTS,
                equipment_id="CMP_CU03",
                chamber_id="CMP_CU03_CH02",
            ),
            self.defect_wat_agent.analyze(
                request_id="REQ_SERIALIZE_DEFECT_WAT",
                lot_ids=AFFECTED_LOTS,
            ),
            self.knowledge_agent.analyze(
                request_id="REQ_SERIALIZE_KNOWLEDGE",
                query="Cu CMP slurry flow scratch leakage",
                module="Cu CMP",
                equipment_type="CMP",
            ),
        ]

        for finding in findings:
            round_tripped = AgentFinding.from_dict(finding.to_dict())
            self.assertEqual(round_tripped, finding)
            evidence_ids_in_details = {item["evidence_id"] for item in finding.details["evidence"]}
            self.assertEqual(set(finding.evidence_ids), evidence_ids_in_details)
            self.assertEqual(
                finding.evidence_ids,
                [evidence.evidence_id for evidence in finding.evidence],
            )
            self.assertEqual(
                [evidence.to_dict() for evidence in finding.evidence],
                finding.details["evidence"],
            )
            self.assertGreaterEqual(finding.confidence, 0.0)
            self.assertLessEqual(finding.confidence, 1.0)

    def test_specialist_agent_module_has_no_data_access_dependency(self) -> None:
        import yield_rca_core.specialist_agents as specialist_agents

        source = inspect.getsource(specialist_agents).lower()
        forbidden_dependencies = (
            "yield_rca_core.repositories",
            "csvfabrepository",
            "postgresfabrepository",
            "psycopg",
            "sqlalchemy",
            "select ",
            "insert ",
            "update ",
            "delete ",
        )
        for dependency in forbidden_dependencies:
            self.assertNotIn(dependency, source)


if __name__ == "__main__":
    unittest.main()
