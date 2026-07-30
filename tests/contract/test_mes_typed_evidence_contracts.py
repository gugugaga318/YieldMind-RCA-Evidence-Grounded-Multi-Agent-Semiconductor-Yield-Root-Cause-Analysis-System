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
    EvidenceType,
    ModelValidationError,
    ToolInput,
    ToolOutput,
)
from yield_rca_core.repositories import CsvFabRepository, Row  # noqa: E402
from yield_rca_core.tool_layer import (  # noqa: E402
    AnalyzeLotGenealogyTool,
    FindAffectedLotsTool,
    FindImpactLotsTool,
    GetLotContextTool,
)

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
MULTI_CASE_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def mes_input(
    tool_name: str,
    request_id: str,
    parameters: dict[str, object],
) -> ToolInput:
    return ToolInput(
        tool_name=tool_name,
        request_id=request_id,
        parameters=parameters,
        requested_by=AgentKind.MES.value,
    )


def evidence_by_id(output: ToolOutput) -> dict[str, Evidence]:
    return {evidence.evidence_id: evidence for evidence in output.evidence}


class MissingWatRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "lot_master": [
                {
                    "lot_id": "LOT_MISSING_WAT",
                    "product_id": "40N_SOC",
                    "started_at": "2026-07-01T00:00:00+00:00",
                }
            ],
            "wat_result": [],
            "fdc_feature": [],
        }
        return [dict(row) for row in tables[table_name]]


class PartialWatRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "lot_master": [
                {
                    "lot_id": "LOT_TESTED",
                    "product_id": "40N_SOC",
                    "started_at": "2026-07-01T00:00:00+00:00",
                },
                {
                    "lot_id": "LOT_UNTESTED",
                    "product_id": "40N_SOC",
                    "started_at": "2026-07-01T01:00:00+00:00",
                },
            ],
            "wat_result": [
                {
                    "lot_id": "LOT_TESTED",
                    "pass_fail": "true",
                    "fail_mode": "",
                    "tested_at": "2026-07-02T00:00:00+00:00",
                }
            ],
            "fdc_feature": [],
        }
        return [dict(row) for row in tables[table_name]]


class MissingRecipeHistoryRepository:
    def rows(self, table_name: str) -> list[Row]:
        process_rows = [
            {
                "lot_id": "LOT_PREVIOUS",
                "route_id": "ROUTE_001",
                "operation_no": "6400",
                "wafer_id": "LOT_PREVIOUS_W01",
                "recipe_id": "CU_R17",
                "recipe_version": "1",
                "started_at": "2026-07-01T00:00:00+00:00",
                "ended_at": "2026-07-01T01:00:00+00:00",
            },
            {
                "lot_id": "LOT_RECIPE_CHANGE",
                "route_id": "ROUTE_001",
                "operation_no": "6400",
                "wafer_id": "LOT_RECIPE_CHANGE_W01",
                "recipe_id": "CU_R18",
                "recipe_version": "2",
                "started_at": "2026-07-02T00:00:00+00:00",
                "ended_at": "2026-07-02T01:00:00+00:00",
            },
        ]
        tables: dict[str, list[Row]] = {
            "lot_master": [
                {
                    "lot_id": "LOT_RECIPE_CHANGE",
                    "product_id": "40N_SOC",
                    "route_id": "ROUTE_001",
                    "started_at": "2026-07-02T00:00:00+00:00",
                    "finished_at": "2026-07-03T00:00:00+00:00",
                }
            ],
            "process_history": process_rows,
            "wat_result": [],
            "defect_summary": [],
            "hold_history": [],
            "fdc_feature": [],
            "metrology_result": [],
            "recipe_history": [],
        }
        return [dict(row) for row in tables[table_name]]


class MESTypedEvidenceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.golden_repository = CsvFabRepository(GOLDEN_SEED_DIR)
        self.multi_case_repository = CsvFabRepository(MULTI_CASE_SEED_DIR)

    def assert_typed_mes_output(self, output: ToolOutput, tool_name: str) -> None:
        self.assertTrue(output.evidence)
        self.assertEqual(output.evidence_ids, [item.evidence_id for item in output.evidence])
        self.assertEqual(
            output.data["evidence"],
            [item.to_dict() for item in output.evidence],
        )
        for evidence in output.evidence:
            self.assertTrue(evidence.is_typed)
            self.assertEqual(evidence.source_agent, AgentKind.MES.value)
            self.assertEqual(evidence.source_tool, tool_name)
            self.assertTrue(evidence.entities)
            self.assertIsNotNone(evidence.confidence)

    def test_find_affected_lots_returns_typed_scope_evidence(self) -> None:
        output = FindAffectedLotsTool(self.golden_repository).run(
            mes_input(
                "find_affected_lots",
                "REQ_TYPED_AFFECTED",
                {
                    "product_id": "40N_SOC",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-31",
                },
            )
        )

        self.assert_typed_mes_output(output, "find_affected_lots")
        evidence = evidence_by_id(output)["EV_ANALYTICS_AFFECTED_LOTS"]
        self.assertEqual(evidence.evidence_type, EvidenceType.IMPACT_SCOPE.value)
        self.assertTrue(
            any(
                entity.entity_type == EntityType.PRODUCT.value and entity.entity_id == "40N_SOC"
                for entity in evidence.entities
            )
        )

    def test_find_affected_lots_distinguishes_missing_wat_data(self) -> None:
        output = FindAffectedLotsTool(MissingWatRepository()).run(
            mes_input(
                "find_affected_lots",
                "REQ_MISSING_WAT",
                {"product_id": "40N_SOC"},
            )
        )

        evidence = output.evidence[0]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertIn("No WAT pass/fail records", evidence.observation or "")

    def test_find_affected_lots_does_not_classify_untested_lot_as_normal(self) -> None:
        output = FindAffectedLotsTool(PartialWatRepository()).run(
            mes_input(
                "find_affected_lots",
                "REQ_PARTIAL_WAT",
                {"product_id": "40N_SOC"},
            )
        )

        self.assertEqual(output.data["normal_lots"], ["LOT_TESTED"])
        self.assertEqual(output.data["untested_lots"], ["LOT_UNTESTED"])
        self.assertEqual(output.data["untested_count"], 1)
        missing = evidence_by_id(output)["EV_ANALYTICS_WAT_DATA_MISSING"]
        self.assertEqual(missing.evidence_type, EvidenceType.DATA_MISSING.value)

    def test_lot_context_types_mes_wat_and_negative_fdc_evidence(self) -> None:
        golden = GetLotContextTool(self.golden_repository).run(
            mes_input(
                "get_lot_context",
                "REQ_TYPED_CONTEXT",
                {"lot_id": "LOT_A_001"},
            )
        )
        odd_even = GetLotContextTool(self.multi_case_repository).run(
            mes_input(
                "get_lot_context",
                "REQ_TYPED_CONTEXT_ODD_EVEN",
                {"lot_id": "LOT_A_063"},
            )
        )

        self.assert_typed_mes_output(golden, "get_lot_context")
        golden_evidence = evidence_by_id(golden)
        self.assertEqual(
            golden_evidence["EV_MES_SOURCE_LOT_CONTEXT"].evidence_type,
            EvidenceType.LOT_CONTEXT.value,
        )
        self.assertEqual(
            golden_evidence["EV_WAT_SOURCE_LOT_ANOMALY"].evidence_type,
            EvidenceType.ELECTRICAL_FAILURE.value,
        )
        self.assert_typed_mes_output(odd_even, "get_lot_context")
        self.assertEqual(
            evidence_by_id(odd_even)["EV_FDC_CMP_NORMAL_EXCLUSION"].evidence_type,
            EvidenceType.NEGATIVE_SIGNAL.value,
        )

    def test_impact_scope_types_ooc_window_and_impact_lots(self) -> None:
        output = FindImpactLotsTool(self.multi_case_repository).run(
            mes_input(
                "find_impact_lots",
                "REQ_TYPED_IMPACT",
                {"lot_id": "LOT_A_015"},
            )
        )

        self.assert_typed_mes_output(output, "find_impact_lots")
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_FDC_EXCURSION_WINDOW"].evidence_type,
            EvidenceType.EXCURSION_WINDOW.value,
        )
        self.assertEqual(
            evidence["EV_MES_IMPACT_LOTS"].evidence_type,
            EvidenceType.IMPACT_SCOPE.value,
        )
        self.assertEqual(
            output.data["impact_lots"],
            [
                "LOT_A_011",
                "LOT_A_012",
                "LOT_A_013",
                "LOT_A_014",
            ],
        )

    def test_isolated_wafer_scope_is_a_negative_lot_impact_signal(self) -> None:
        output = FindImpactLotsTool(self.multi_case_repository).run(
            mes_input(
                "find_impact_lots",
                "REQ_TYPED_ISOLATED_IMPACT",
                {"lot_id": "LOT_A_038"},
            )
        )

        evidence = evidence_by_id(output)["EV_MES_IMPACT_LOTS"]
        self.assertEqual(output.data["impact_lots"], [])
        self.assertEqual(evidence.evidence_type, EvidenceType.NEGATIVE_SIGNAL.value)

    def test_genealogy_types_equipment_exposure_and_hold_events(self) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output = AnalyzeLotGenealogyTool(self.golden_repository).run(
            mes_input(
                "analyze_lot_genealogy",
                "REQ_TYPED_GENEALOGY",
                {"lot_ids": affected_lots, "target_operation_no": "6400"},
            )
        )

        self.assert_typed_mes_output(output, "analyze_lot_genealogy")
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_MES_COMMON_CHAMBER"].evidence_type,
            EvidenceType.EQUIPMENT_EXPOSURE.value,
        )
        self.assertEqual(
            evidence["EV_HOLD_COMMENT"].evidence_type,
            EvidenceType.HOLD_EVENT.value,
        )
        self.assertEqual(
            evidence["EV_MES_LOT_HOLD"].evidence_type,
            EvidenceType.HOLD_EVENT.value,
        )

    def test_lot_context_falls_back_when_recipe_history_is_missing(self) -> None:
        output = GetLotContextTool(MissingRecipeHistoryRepository()).run(
            mes_input(
                "get_lot_context",
                "REQ_RECIPE_HISTORY_MISSING",
                {"lot_id": "LOT_RECIPE_CHANGE"},
            )
        )

        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_MES_RECIPE_CHANGE"].source_table,
            "process_history",
        )
        self.assertEqual(
            evidence["EV_MES_RECIPE_HISTORY_MISSING"].evidence_type,
            EvidenceType.DATA_MISSING.value,
        )
        self.assertTrue(output.data["recipe_history_missing"])
        self.assertIn(
            "WARN_RECIPE_HISTORY_MISSING",
            {warning.warning_id for warning in output.warnings},
        )

    def test_mes_tool_rejects_non_owner_agent(self) -> None:
        with self.assertRaisesRegex(
            ModelValidationError,
            "belongs to agent mes",
        ):
            FindAffectedLotsTool(self.golden_repository).run(
                ToolInput(
                    tool_name="find_affected_lots",
                    request_id="REQ_WRONG_TOOL_OWNER",
                    parameters={"product_id": "40N_SOC"},
                    requested_by=AgentKind.FDC.value,
                )
            )


if __name__ == "__main__":
    unittest.main()
