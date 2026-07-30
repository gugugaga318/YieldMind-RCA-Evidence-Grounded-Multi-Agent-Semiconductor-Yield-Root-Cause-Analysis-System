from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentKind, Evidence, ToolInput, ToolOutput  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.tool_layer import (  # noqa: E402
    AnalyzeLotGenealogyTool,
    AnalyzeParameterShiftTool,
    FindAffectedLotsTool,
    FindImpactLotsTool,
    FindOocEventsTool,
    GetLotContextTool,
    PerformBasicSpcAnalysisTool,
    RetrieveSimilarCaseTool,
    SummarizeDefectWatTool,
)

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
MULTI_CASE_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def tool_input(
    tool_name: str,
    request_id: str,
    parameters: dict[str, object],
    requested_by: str,
) -> ToolInput:
    return ToolInput(
        tool_name=tool_name,
        request_id=request_id,
        parameters=parameters,
        requested_by=requested_by,
    )


def evidence_from_output(output: ToolOutput) -> list[Evidence]:
    return [Evidence.from_dict(item) for item in output.data["evidence"]]


class ToolLayerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = CsvFabRepository(SEED_DIR)
        self.multi_case_repository = CsvFabRepository(MULTI_CASE_SEED_DIR)

    def test_find_affected_lots_returns_wat_backed_evidence(self) -> None:
        output = FindAffectedLotsTool(self.repository).run(
            tool_input(
                "find_affected_lots",
                "REQ_FIND_AFFECTED",
                {
                    "product_id": "40N_SOC",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                },
                AgentKind.MES.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_ANALYTICS_AFFECTED_LOTS", output.evidence_ids)
        self.assertEqual(output.data["affected_count"], 20)
        self.assertEqual(output.data["normal_count"], 30)
        self.assertEqual(output.data["affected_lots"][0], "LOT_A_001")
        self.assertEqual(len(output.data["yield_trend"]), 7)
        self.assertEqual(
            output.data["yield_trend"][0],
            {
                "date": "2026-07-05",
                "lot_count": 7,
                "pass_count": 7,
                "fail_count": 0,
                "pass_rate": 100.0,
            },
        )
        self.assertEqual(output.data["yield_trend"][-1]["pass_rate"], 0.0)
        self.assertEqual(evidence_from_output(output)[0].source_table, "wat_result")

    def test_mes_genealogy_tool_returns_common_chamber_and_hold_evidence(self) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output = AnalyzeLotGenealogyTool(self.repository).run(
            tool_input(
                "analyze_lot_genealogy",
                "REQ_MES_COMMONALITY",
                {"lot_ids": affected_lots, "target_operation_no": "6400"},
                AgentKind.MES.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_MES_COMMON_CHAMBER", output.evidence_ids)
        self.assertIn("EV_HOLD_COMMENT", output.evidence_ids)
        self.assertEqual(output.data["target_commonality"]["equipment_id"], "CMP_CU03")
        self.assertEqual(output.data["target_commonality"]["chamber_id"], "CMP_CU03_CH02")
        self.assertEqual(output.data["target_commonality"]["coverage"], 1.0)

    def test_lot_context_and_impact_tools_return_traceable_scope(self) -> None:
        context = GetLotContextTool(self.repository).run(
            tool_input(
                "get_lot_context",
                "REQ_LOT_CONTEXT",
                {"lot_id": "LOT_A_001"},
                AgentKind.MES.value,
            )
        )
        impact = FindImpactLotsTool(self.repository).run(
            tool_input(
                "find_impact_lots",
                "REQ_IMPACT_LOTS",
                {"lot_id": "LOT_A_001"},
                AgentKind.MES.value,
            )
        )

        self.assertTrue(context.data["wat_failed"])
        self.assertEqual(context.data["product_id"], "40N_SOC")
        self.assertIn("EV_MES_SOURCE_LOT_CONTEXT", context.evidence_ids)
        self.assertIn("EV_WAT_SOURCE_LOT_ANOMALY", context.evidence_ids)
        self.assertEqual(len(impact.data["impact_lots"]), 19)
        self.assertNotIn("LOT_A_001", impact.data["impact_lots"])
        self.assertEqual(impact.data["impact_criteria"]["operation_no"], "6400")
        self.assertEqual(impact.data["impact_criteria"]["chamber_id"], "CMP_CU03_CH02")
        self.assertIn("EV_FDC_EXCURSION_WINDOW", impact.evidence_ids)
        self.assertIn("EV_MES_IMPACT_LOTS", impact.evidence_ids)

    def test_fdc_parameter_shift_tool_returns_slurry_and_endpoint_evidence(self) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output = AnalyzeParameterShiftTool(self.repository).run(
            tool_input(
                "analyze_parameter_shift",
                "REQ_FDC_SHIFT",
                {
                    "lot_ids": affected_lots,
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
                AgentKind.FDC.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_FDC_SLURRY_FLOW", output.evidence_ids)
        self.assertIn("EV_FDC_ENDPOINT_TIME", output.evidence_ids)
        summary = {item["parameter_name"]: item for item in output.data["parameter_summary"]}
        self.assertEqual(summary["slurry_flow"]["avg_observed"], 132.0)
        self.assertEqual(summary["slurry_flow"]["avg_delta_percent"], -12.0)
        self.assertEqual(summary["endpoint_time"]["avg_observed"], 105.0)

    def test_ooc_tool_returns_high_severity_events(self) -> None:
        output = FindOocEventsTool(self.repository).run(
            tool_input(
                "find_ooc_events",
                "REQ_OOC",
                {
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
                AgentKind.FDC.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_OOC_EVENTS", output.evidence_ids)
        self.assertEqual(output.data["event_count"], 20)
        self.assertEqual(output.data["severity_counts"]["HIGH"], 20)

    def test_basic_spc_detects_cu_excursion_from_normal_peer_baseline(self) -> None:
        output = PerformBasicSpcAnalysisTool(self.multi_case_repository).run(
            tool_input(
                "perform_basic_spc_analysis",
                "REQ_BASIC_SPC_CU",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(11, 16)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
                AgentKind.FDC.value,
            )
        )

        self.assertTrue(output.success)
        self.assertEqual(output.data["analyzed_parameter_count"], 3)
        self.assertEqual(output.data["ooc_parameter_count"], 3)
        self.assertGreater(output.data["calculated_point_violation_count"], 0)
        results = {item["parameter_name"]: item for item in output.data["spc_results"]}
        slurry = results["slurry_flow"]
        self.assertEqual(slurry["status"], "OOC")
        self.assertEqual(slurry["baseline_scope"], "same_equipment")
        self.assertLess(
            datetime.fromisoformat(slurry["baseline_window_end"]),
            datetime.fromisoformat(slurry["target_window_start"]),
        )
        self.assertIn("POINT_BEYOND_3_SIGMA", slurry["violated_rules"])
        self.assertLess(slurry["target_mean"], slurry["lower_control_limit"])
        self.assertIn("EV_SPC_SLURRY_FLOW", output.evidence_ids)
        self.assertEqual(output.warnings, [])

    def test_basic_spc_reports_insufficient_baseline_without_fabricating_limits(self) -> None:
        output = PerformBasicSpcAnalysisTool(self.repository).run(
            tool_input(
                "perform_basic_spc_analysis",
                "REQ_BASIC_SPC_NO_BASELINE",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(1, 21)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
                AgentKind.FDC.value,
            )
        )

        self.assertEqual(output.data["spc_results"], [])
        self.assertEqual(
            output.data["baseline_insufficient_parameters"],
            ["endpoint_time", "slurry_flow"],
        )
        self.assertEqual(output.evidence_ids, ["EV_SPC_BASELINE_STATUS"])
        self.assertEqual(output.warnings[0].warning_id, "WARN_SPC_BASELINE_INSUFFICIENT")

    def test_defect_wat_tool_returns_physical_and_electrical_evidence(self) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output = SummarizeDefectWatTool(self.repository).run(
            tool_input(
                "summarize_defect_wat",
                "REQ_DEFECT_WAT",
                {"lot_ids": affected_lots},
                AgentKind.DEFECT_WAT.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_DEFECT_SCRATCH", output.evidence_ids)
        self.assertIn("EV_WAT_LEAKAGE", output.evidence_ids)
        self.assertEqual(output.data["defect_counts"]["scratch"], 20)
        self.assertEqual(output.data["defect_patterns"]["edge_dominant"], 20)
        self.assertEqual(output.data["wat_fail_modes"]["leakage"], 20)

    def test_knowledge_tool_returns_historical_rca_evidence(self) -> None:
        output = RetrieveSimilarCaseTool(self.repository).run(
            tool_input(
                "retrieve_similar_case",
                "REQ_KNOWLEDGE",
                {
                    "query": "Cu CMP slurry flow scratch leakage",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                },
                AgentKind.KNOWLEDGE.value,
            )
        )

        self.assertTrue(output.success)
        self.assertIn("EV_KNOWLEDGE_MATCH", output.evidence_ids)
        self.assertEqual(output.data["top_case"]["case_id"], "RCA_CMP_2025_032")
        self.assertGreaterEqual(output.data["top_case"]["similarity"], 0.9)
        self.assertEqual(output.data["documents"][0]["document_id"], "DOC_RCA_CMP_2025_032")

    def test_knowledge_tool_excludes_unconfirmed_cases(self) -> None:
        class UnconfirmedKnowledgeRepository:
            def rows(self, table_name: str) -> list[dict[str, str]]:
                if table_name == "rca_case":
                    return [
                        {
                            "case_id": "RCA_DRAFT_001",
                            "title": "Unapproved CMP finding",
                            "module": "Cu CMP",
                            "equipment_type": "CMP",
                            "symptom": "slurry flow",
                            "root_cause": "draft root cause",
                            "solution": "draft action",
                            "confidence": "0.99",
                            "created_at": "2026-07-01T00:00:00+00:00",
                            "validation_status": "DRAFT",
                        }
                    ]
                if table_name == "knowledge_document":
                    return []
                raise AssertionError(f"unexpected table: {table_name}")

        output = RetrieveSimilarCaseTool(UnconfirmedKnowledgeRepository()).run(
            tool_input(
                "retrieve_similar_case",
                "REQ_UNCONFIRMED_KNOWLEDGE",
                {"query": "CMP slurry flow", "module": "Cu CMP"},
                AgentKind.KNOWLEDGE.value,
            )
        )

        self.assertIsNone(output.data["top_case"])
        self.assertEqual(output.data["cases"], [])
        self.assertIn("WARN_KNOWLEDGE_NO_CONFIRMED_CASE", {
            item.warning_id for item in output.warnings
        })

    def test_tools_return_serializable_tool_outputs_with_traceable_evidence(self) -> None:
        output = FindAffectedLotsTool(self.repository).run(
            tool_input(
                "find_affected_lots",
                "REQ_SERIALIZE",
                {"product_id": "40N_SOC"},
                AgentKind.MES.value,
            )
        )

        round_tripped = ToolOutput.from_dict(output.to_dict())
        self.assertEqual(round_tripped.evidence_ids, output.evidence_ids)
        self.assertEqual(
            round_tripped.evidence_ids,
            [evidence.evidence_id for evidence in round_tripped.evidence],
        )
        self.assertTrue(round_tripped.data["evidence"])
        self.assertEqual(
            [evidence.to_dict() for evidence in round_tripped.evidence],
            round_tripped.data["evidence"],
        )
        for evidence in evidence_from_output(round_tripped):
            self.assertIn(evidence.evidence_id, round_tripped.evidence_ids)
            self.assertTrue(evidence.source_table)


if __name__ == "__main__":
    unittest.main()
