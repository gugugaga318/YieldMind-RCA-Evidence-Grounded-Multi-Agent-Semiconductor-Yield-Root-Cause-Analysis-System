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
    AnalyzeParameterShiftTool,
    AnalyzeSpcEvidenceTool,
    FindOocEventsTool,
    PerformBasicSpcAnalysisTool,
)

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
MULTI_CASE_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"
SPC_CASE_SEED_DIR = ROOT / "data" / "seeds" / "spc_case"


def fdc_input(
    tool_name: str,
    request_id: str,
    parameters: dict[str, object],
) -> ToolInput:
    return ToolInput(
        tool_name=tool_name,
        request_id=request_id,
        parameters=parameters,
        requested_by=AgentKind.FDC.value,
    )


def evidence_by_id(output: ToolOutput) -> dict[str, Evidence]:
    return {evidence.evidence_id: evidence for evidence in output.evidence}


class EmptyFdcRepository:
    def rows(self, table_name: str) -> list[Row]:
        if table_name != "fdc_feature":
            raise KeyError(table_name)
        return []


class FilteredSpcRepository:
    def __init__(
        self,
        repository: CsvFabRepository,
        *,
        remove_hold_ids: set[str] | None = None,
        remove_baseline_data: bool = False,
    ) -> None:
        self.repository = repository
        self.remove_hold_ids = set(remove_hold_ids or set())
        self.remove_baseline_data = remove_baseline_data

    def rows(self, table_name: str) -> list[Row]:
        rows = self.repository.rows(table_name)
        if table_name == "hold_history":
            rows = [row for row in rows if row["hold_id"] not in self.remove_hold_ids]
        if self.remove_baseline_data and table_name == "fdc_feature":
            rows = [row for row in rows if row["measured_at"] >= "2026-07-01"]
        if self.remove_baseline_data and table_name == "wat_result":
            rows = [row for row in rows if row["tested_at"] >= "2026-07-01"]
        return [dict(row) for row in rows]


class FdcSpcTypedEvidenceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.golden_repository = CsvFabRepository(GOLDEN_SEED_DIR)
        self.multi_case_repository = CsvFabRepository(MULTI_CASE_SEED_DIR)
        self.spc_repository = CsvFabRepository(SPC_CASE_SEED_DIR)

    def assert_typed_fdc_output(self, output: ToolOutput, tool_name: str) -> None:
        self.assertTrue(output.evidence)
        self.assertEqual(output.evidence_ids, [item.evidence_id for item in output.evidence])
        self.assertEqual(
            output.data["evidence"],
            [item.to_dict() for item in output.evidence],
        )
        for evidence in output.evidence:
            self.assertTrue(evidence.is_typed)
            self.assertEqual(evidence.source_agent, AgentKind.FDC.value)
            self.assertEqual(evidence.source_tool, tool_name)
            self.assertTrue(evidence.entities)
            self.assertIsNotNone(evidence.confidence)

    def test_parameter_shift_distinguishes_deviation_from_normal_signal(self) -> None:
        abnormal = AnalyzeParameterShiftTool(self.golden_repository).run(
            fdc_input(
                "analyze_parameter_shift",
                "REQ_TYPED_FDC_SHIFT",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(1, 21)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )
        normal = AnalyzeParameterShiftTool(self.spc_repository).run(
            fdc_input(
                "analyze_parameter_shift",
                "REQ_TYPED_FDC_NORMAL",
                {
                    "lot_ids": ["LOT_A_081"],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(abnormal, "analyze_parameter_shift")
        self.assert_typed_fdc_output(normal, "analyze_parameter_shift")
        self.assertEqual(
            evidence_by_id(abnormal)["EV_FDC_SLURRY_FLOW"].evidence_type,
            EvidenceType.PARAMETER_DEVIATION.value,
        )
        self.assertEqual(
            evidence_by_id(normal)["EV_FDC_SLURRY_FLOW"].evidence_type,
            EvidenceType.NEGATIVE_SIGNAL.value,
        )
        self.assertTrue(
            any(
                entity.entity_type == EntityType.PARAMETER.value
                and entity.entity_id == "slurry_flow"
                for entity in evidence_by_id(abnormal)["EV_FDC_SLURRY_FLOW"].entities
            )
        )

    def test_missing_fdc_features_are_data_missing(self) -> None:
        output = AnalyzeParameterShiftTool(EmptyFdcRepository()).run(
            fdc_input(
                "analyze_parameter_shift",
                "REQ_TYPED_FDC_MISSING",
                {
                    "lot_ids": ["LOT_NO_FDC"],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "analyze_parameter_shift")
        evidence = evidence_by_id(output)["EV_FDC_FEATURE_DATA_MISSING"]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertIn(
            "WARN_FDC_FEATURE_DATA_MISSING",
            {warning.warning_id for warning in output.warnings},
        )

    def test_basic_spc_types_ooc_and_in_control_results(self) -> None:
        ooc = PerformBasicSpcAnalysisTool(self.multi_case_repository).run(
            fdc_input(
                "perform_basic_spc_analysis",
                "REQ_TYPED_BASIC_SPC_OOC",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(11, 16)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )
        in_control = PerformBasicSpcAnalysisTool(self.spc_repository).run(
            fdc_input(
                "perform_basic_spc_analysis",
                "REQ_TYPED_BASIC_SPC_NORMAL",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(81, 86)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(ooc, "perform_basic_spc_analysis")
        self.assert_typed_fdc_output(in_control, "perform_basic_spc_analysis")
        self.assertTrue(
            any(
                evidence.evidence_type == EvidenceType.SPC_VIOLATION.value
                for evidence in ooc.evidence
            )
        )
        self.assertTrue(in_control.data["spc_results"])
        self.assertTrue(
            any(item["status"] == "IN_CONTROL" for item in in_control.data["spc_results"])
        )
        self.assertTrue(
            any(
                evidence.evidence_type == EvidenceType.NEGATIVE_SIGNAL.value
                for evidence in in_control.evidence
            )
        )

    def test_basic_spc_insufficient_baseline_is_data_missing(self) -> None:
        output = PerformBasicSpcAnalysisTool(self.golden_repository).run(
            fdc_input(
                "perform_basic_spc_analysis",
                "REQ_TYPED_BASIC_SPC_MISSING",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(1, 21)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "perform_basic_spc_analysis")
        evidence = evidence_by_id(output)["EV_SPC_BASELINE_STATUS"]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertEqual(
            output.warnings[0].evidence_ids,
            [evidence.evidence_id],
        )

    def test_advanced_spc_uses_typed_strict_baseline_evidence(self) -> None:
        output = AnalyzeSpcEvidenceTool(self.spc_repository).run(
            fdc_input(
                "analyze_spc_evidence",
                "REQ_TYPED_ADVANCED_SPC",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(11, 16)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "analyze_spc_evidence")
        self.assertEqual(output.data["analyzed_parameter_count"], 5)
        self.assertGreaterEqual(output.data["ooc_parameter_count"], 1)
        slurry = evidence_by_id(output)["EV_SPC_SLURRY_FLOW"]
        self.assertEqual(slurry.evidence_type, EvidenceType.SPC_VIOLATION.value)
        self.assertTrue(
            any(
                entity.entity_type == EntityType.RECIPE.value
                and entity.entity_id == "CU_CMP_40N"
                for entity in slurry.entities
            )
        )

    def test_advanced_spc_insufficient_baseline_is_data_missing(self) -> None:
        repository = FilteredSpcRepository(
            self.spc_repository,
            remove_baseline_data=True,
        )
        output = AnalyzeSpcEvidenceTool(repository).run(
            fdc_input(
                "analyze_spc_evidence",
                "REQ_TYPED_ADVANCED_SPC_MISSING",
                {
                    "lot_ids": [f"LOT_A_{number:03d}" for number in range(11, 16)],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "analyze_spc_evidence")
        evidence = evidence_by_id(output)["EV_SPC_BASELINE_DATA_MISSING"]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertEqual(output.data["analyzed_parameter_count"], 0)
        self.assertIn(
            "WARN_SPC_BASELINE_INSUFFICIENT",
            {warning.warning_id for warning in output.warnings},
        )

    def test_advanced_spc_missing_profile_is_data_missing(self) -> None:
        output = AnalyzeSpcEvidenceTool(self.spc_repository).run(
            fdc_input(
                "analyze_spc_evidence",
                "REQ_TYPED_ADVANCED_SPC_PROFILE_MISSING",
                {
                    "lot_ids": ["LOT_A_001"],
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU02",
                    "chamber_id": "CMP_CU02_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "analyze_spc_evidence")
        evidence = evidence_by_id(output)["EV_SPC_PROFILE_DATA_MISSING"]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertIn(
            "WARN_SPC_PROFILE_NOT_FOUND",
            {warning.warning_id for warning in output.warnings},
        )

    def test_ooc_context_types_event_excursion_and_hold_evidence(self) -> None:
        output = FindOocEventsTool(self.spc_repository).run(
            fdc_input(
                "find_ooc_events",
                "REQ_TYPED_OOC_CONTEXT",
                {
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "find_ooc_events")
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_OOC_EVENTS"].evidence_type,
            EvidenceType.OOC_EVENT.value,
        )
        self.assertEqual(
            evidence["EV_SPC_OOC_CONTEXT"].evidence_type,
            EvidenceType.EXCURSION_WINDOW.value,
        )
        self.assertEqual(
            evidence["EV_SPC_HOLD_CONTEXT"].evidence_type,
            EvidenceType.HOLD_EVENT.value,
        )
        self.assertEqual(output.warnings, [])
        self.assertTrue(output.data["spc_contexts"][0]["hold_link_complete"])

    def test_missing_hold_retains_ooc_and_adds_missing_data_warning(self) -> None:
        repository = FilteredSpcRepository(
            self.spc_repository,
            remove_hold_ids={"HOLD_CU_OOC_001"},
        )
        output = FindOocEventsTool(repository).run(
            fdc_input(
                "find_ooc_events",
                "REQ_TYPED_OOC_MISSING_HOLD",
                {
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU03",
                    "chamber_id": "CMP_CU03_CH02",
                },
            )
        )

        self.assert_typed_fdc_output(output, "find_ooc_events")
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_OOC_EVENTS"].evidence_type,
            EvidenceType.OOC_EVENT.value,
        )
        self.assertEqual(
            evidence["EV_SPC_OOC_CONTEXT"].evidence_type,
            EvidenceType.EXCURSION_WINDOW.value,
        )
        self.assertEqual(
            evidence["EV_SPC_HOLD_DATA_MISSING"].evidence_type,
            EvidenceType.DATA_MISSING.value,
        )
        self.assertNotIn("EV_SPC_HOLD_CONTEXT", evidence)
        self.assertIn(
            "WARN_SPC_HOLD_MISSING",
            {warning.warning_id for warning in output.warnings},
        )
        self.assertFalse(output.data["spc_contexts"][0]["hold_link_complete"])

    def test_no_ooc_event_is_a_negative_signal(self) -> None:
        output = FindOocEventsTool(self.spc_repository).run(
            fdc_input(
                "find_ooc_events",
                "REQ_TYPED_NO_OOC",
                {
                    "operation_no": "6400",
                    "equipment_id": "CMP_CU01",
                    "chamber_id": "CMP_CU01_CH01",
                },
            )
        )

        self.assert_typed_fdc_output(output, "find_ooc_events")
        self.assertEqual(output.data["event_count"], 0)
        self.assertEqual(
            evidence_by_id(output)["EV_OOC_EVENTS"].evidence_type,
            EvidenceType.NEGATIVE_SIGNAL.value,
        )

    def test_fdc_tool_rejects_non_owner_agent(self) -> None:
        with self.assertRaisesRegex(
            ModelValidationError,
            "belongs to agent fdc",
        ):
            AnalyzeParameterShiftTool(self.golden_repository).run(
                ToolInput(
                    tool_name="analyze_parameter_shift",
                    request_id="REQ_TYPED_FDC_WRONG_OWNER",
                    parameters={
                        "lot_ids": ["LOT_A_001"],
                        "operation_no": "6400",
                    },
                    requested_by=AgentKind.MES.value,
                )
            )


if __name__ == "__main__":
    unittest.main()
