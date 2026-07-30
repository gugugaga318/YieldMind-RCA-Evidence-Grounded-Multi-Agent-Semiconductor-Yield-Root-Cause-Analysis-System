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
from yield_rca_core.tool_layer import SummarizeDefectWatTool  # noqa: E402

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
MULTI_CASE_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


def defect_wat_input(request_id: str, lot_ids: list[str]) -> ToolInput:
    return ToolInput(
        tool_name="summarize_defect_wat",
        request_id=request_id,
        parameters={"lot_ids": lot_ids},
        requested_by=AgentKind.DEFECT_WAT.value,
    )


def evidence_by_id(output: ToolOutput) -> dict[str, Evidence]:
    return {evidence.evidence_id: evidence for evidence in output.evidence}


class EmptyQualityRepository:
    def rows(self, table_name: str) -> list[Row]:
        if table_name not in {"defect_summary", "wat_result", "metrology_result"}:
            raise KeyError(table_name)
        return []


class PartialQualityRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "defect_summary": [
                {
                    "lot_id": "LOT_PARTIAL",
                    "wafer_id": "LOT_PARTIAL_W01",
                    "defect_type": "scratch",
                    "pattern_type": "isolated",
                    "inspected_at": "2026-07-01T00:00:00+00:00",
                }
            ],
            "wat_result": [],
            "metrology_result": [],
        }
        return [dict(row) for row in tables[table_name]]


class DefectWatTypedEvidenceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.golden_repository = CsvFabRepository(GOLDEN_SEED_DIR)
        self.multi_case_repository = CsvFabRepository(MULTI_CASE_SEED_DIR)

    def assert_typed_defect_wat_output(self, output: ToolOutput) -> None:
        self.assertTrue(output.evidence)
        self.assertEqual(output.evidence_ids, [item.evidence_id for item in output.evidence])
        self.assertEqual(
            output.data["evidence"],
            [item.to_dict() for item in output.evidence],
        )
        for evidence in output.evidence:
            self.assertTrue(evidence.is_typed)
            self.assertEqual(evidence.source_agent, AgentKind.DEFECT_WAT.value)
            self.assertEqual(evidence.source_tool, "summarize_defect_wat")
            self.assertTrue(evidence.entities)
            self.assertIsNotNone(evidence.confidence)

    def test_golden_quality_signals_are_typed_by_physical_and_electrical_domain(
        self,
    ) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output = SummarizeDefectWatTool(self.golden_repository).run(
            defect_wat_input("REQ_TYPED_GOLDEN_QUALITY", affected_lots)
        )

        self.assert_typed_defect_wat_output(output)
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_DEFECT_SCRATCH"].evidence_type,
            EvidenceType.DEFECT_SIGNAL.value,
        )
        self.assertEqual(
            evidence["EV_WAT_LEAKAGE"].evidence_type,
            EvidenceType.ELECTRICAL_FAILURE.value,
        )
        self.assertTrue(
            any(
                entity.entity_type == EntityType.DEFECT.value and entity.entity_id == "scratch"
                for entity in evidence["EV_DEFECT_SCRATCH"].entities
            )
        )
        self.assertTrue(
            any(
                entity.entity_type == EntityType.WAT_ITEM.value and entity.entity_id == "leakage"
                for entity in evidence["EV_WAT_LEAKAGE"].entities
            )
        )

    def test_isolated_scratch_is_not_promoted_to_wat_failure(self) -> None:
        output = SummarizeDefectWatTool(self.multi_case_repository).run(
            defect_wat_input("REQ_TYPED_ISOLATED_SCRATCH", ["LOT_A_038"])
        )

        self.assert_typed_defect_wat_output(output)
        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_DEFECT_SCRATCH"].evidence_type,
            EvidenceType.DEFECT_SIGNAL.value,
        )
        self.assertNotIn("EV_WAT_LEAKAGE", evidence)
        self.assertTrue(
            any(
                entity.entity_type == EntityType.WAFER.value and entity.entity_id == "LOT_A_038_W07"
                for entity in evidence["EV_DEFECT_SCRATCH"].entities
            )
        )

    def test_odd_even_thickness_is_typed_as_metrology_deviation(self) -> None:
        output = SummarizeDefectWatTool(self.multi_case_repository).run(
            defect_wat_input("REQ_TYPED_ODD_EVEN_THICKNESS", ["LOT_A_063"])
        )

        self.assert_typed_defect_wat_output(output)
        metrology_evidence = [
            evidence
            for evidence in output.evidence
            if evidence.evidence_type == EvidenceType.METROLOGY_DEVIATION.value
        ]
        self.assertTrue(metrology_evidence)
        post_cmp = next(
            evidence
            for evidence in metrology_evidence
            if evidence.evidence_id == "EV_METROLOGY_POST_CMP_MEAN_THICKNESS"
        )
        self.assertTrue(
            any(
                entity.entity_type == EntityType.PARAMETER.value
                and entity.entity_id == "POST_CMP:mean_thickness"
                for entity in post_cmp.entities
            )
        )
        self.assertTrue(
            any(
                entity.entity_type == EntityType.WAFER.value
                and entity.attributes["status"] == "out_of_spec"
                for entity in post_cmp.entities
            )
        )

    def test_passing_wat_without_quality_impact_is_a_negative_signal(self) -> None:
        output = SummarizeDefectWatTool(self.multi_case_repository).run(
            defect_wat_input("REQ_TYPED_NO_QUALITY_IMPACT", ["LOT_A_026"])
        )

        self.assert_typed_defect_wat_output(output)
        evidence = evidence_by_id(output)["EV_QUALITY_NO_IMPACT"]
        self.assertEqual(evidence.evidence_type, EvidenceType.NEGATIVE_SIGNAL.value)
        self.assertIn("Available quality records", evidence.observation or "")
        self.assertEqual(
            [(entity.entity_type, entity.entity_id) for entity in evidence.entities],
            [(EntityType.LOT.value, "LOT_A_026")],
        )

    def test_absent_quality_records_are_data_missing_not_negative_signal(self) -> None:
        output = SummarizeDefectWatTool(EmptyQualityRepository()).run(
            defect_wat_input("REQ_TYPED_QUALITY_MISSING", ["LOT_NO_QUALITY_DATA"])
        )

        self.assert_typed_defect_wat_output(output)
        evidence = evidence_by_id(output)["EV_QUALITY_NO_IMPACT"]
        self.assertEqual(evidence.evidence_type, EvidenceType.DATA_MISSING.value)
        self.assertEqual(
            evidence.observation,
            "No Defect, WAT, or Metrology records are available for the selected Lots.",
        )

    def test_wat_failure_count_uses_lot_granularity(self) -> None:
        output = SummarizeDefectWatTool(self.multi_case_repository).run(
            defect_wat_input(
                "REQ_TYPED_WAT_GRANULARITY",
                ["LOT_A_012", "LOT_A_013", "LOT_A_014", "LOT_A_015"],
            )
        )

        wat_evidence = next(
            evidence for evidence in output.evidence if evidence.source_type == "wat"
        )
        self.assertEqual(output.data["wat_fail_count"], 4)
        self.assertEqual(output.data["wat_fail_lot_count"], 4)
        self.assertEqual(output.data["wat_fail_record_count"], 100)
        self.assertIn("4 selected Lots fail WAT", wat_evidence.observation or "")

    def test_partial_quality_data_reports_missing_wat(self) -> None:
        output = SummarizeDefectWatTool(PartialQualityRepository()).run(
            defect_wat_input("REQ_TYPED_PARTIAL_QUALITY", ["LOT_PARTIAL"])
        )

        evidence = evidence_by_id(output)
        self.assertEqual(
            evidence["EV_DEFECT_SCRATCH"].evidence_type,
            EvidenceType.DEFECT_SIGNAL.value,
        )
        self.assertEqual(
            evidence["EV_QUALITY_WAT_DATA_MISSING"].evidence_type,
            EvidenceType.DATA_MISSING.value,
        )
        self.assertEqual(output.data["missing_wat_lot_ids"], ["LOT_PARTIAL"])
        self.assertIn(
            "WARN_WAT_DATA_MISSING",
            {warning.warning_id for warning in output.warnings},
        )

    def test_empty_lot_scope_is_rejected_before_evidence_is_built(self) -> None:
        with self.assertRaisesRegex(
            ModelValidationError,
            "lot_ids must contain at least one Lot",
        ):
            SummarizeDefectWatTool(self.multi_case_repository).run(
                defect_wat_input("REQ_EMPTY_QUALITY_SCOPE", [])
            )


if __name__ == "__main__":
    unittest.main()
