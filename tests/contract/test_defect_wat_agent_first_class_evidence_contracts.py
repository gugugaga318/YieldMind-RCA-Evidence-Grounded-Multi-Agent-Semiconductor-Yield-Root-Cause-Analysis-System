from __future__ import annotations

import inspect
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentFinding, Evidence, ToolInput, ToolOutput  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository, Row  # noqa: E402
from yield_rca_core.specialist_agents import DefectWATAgent  # noqa: E402
from yield_rca_core.tool_layer import SummarizeDefectWatTool  # noqa: E402

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
MULTI_CASE_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


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


@dataclass
class RecordingTool:
    delegate: Any
    outputs: list[ToolOutput] = field(default_factory=list)

    def run(self, tool_input: ToolInput) -> ToolOutput:
        output = cast(ToolOutput, self.delegate.run(tool_input))
        # Agent evidence transport must not depend on the generated legacy mirror.
        output.data.pop("evidence", None)
        self.outputs.append(output)
        return output


class DefectWATAgentFirstClassEvidenceContractTest(unittest.TestCase):
    def run_agent(
        self,
        *,
        repository: Any,
        request_id: str,
        lot_ids: list[str],
    ) -> tuple[ToolOutput, AgentFinding]:
        tool = RecordingTool(SummarizeDefectWatTool(repository))
        agent = DefectWATAgent(summarize_defect_wat_tool=cast(Any, tool))
        finding = agent.analyze(request_id=request_id, lot_ids=lot_ids)
        return tool.outputs[0], finding

    def assert_original_tool_evidence_is_transported(
        self,
        output: ToolOutput,
        finding_evidence: list[Evidence],
    ) -> None:
        self.assertEqual(finding_evidence, output.evidence)
        self.assertTrue(
            all(
                actual is source
                for actual, source in zip(finding_evidence, output.evidence, strict=True)
            )
        )

    def test_golden_path_consumes_first_class_tool_evidence(self) -> None:
        affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
        output, finding = self.run_agent(
            repository=CsvFabRepository(GOLDEN_SEED_DIR),
            request_id="REQ_DEFECT_WAT_FIRST_CLASS_GOLDEN",
            lot_ids=affected_lots,
        )

        self.assert_original_tool_evidence_is_transported(output, finding.evidence)
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in output.evidence])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in output.evidence],
        )
        self.assertTrue(finding.details["physical_electrical_consistent"])
        self.assertEqual(finding.confidence, 0.9)

    def test_negative_signal_path_preserves_warnings_and_evidence(self) -> None:
        output, finding = self.run_agent(
            repository=CsvFabRepository(MULTI_CASE_SEED_DIR),
            request_id="REQ_DEFECT_WAT_FIRST_CLASS_NEGATIVE",
            lot_ids=["LOT_A_026"],
        )

        self.assert_original_tool_evidence_is_transported(output, finding.evidence)
        self.assertIn("EV_QUALITY_NO_IMPACT", finding.evidence_ids)
        self.assertEqual(
            {warning.warning_id for warning in finding.warnings},
            {"WARN_DEFECT_NO_SIGNAL", "WARN_WAT_NO_FAILURE"},
        )
        self.assertEqual(finding.confidence, 0.2)

    def test_missing_wat_path_preserves_typed_missing_data_evidence(self) -> None:
        output, finding = self.run_agent(
            repository=PartialQualityRepository(),
            request_id="REQ_DEFECT_WAT_FIRST_CLASS_MISSING",
            lot_ids=["LOT_PARTIAL"],
        )

        self.assert_original_tool_evidence_is_transported(output, finding.evidence)
        self.assertIn("EV_QUALITY_WAT_DATA_MISSING", finding.evidence_ids)
        self.assertEqual(finding.details["missing_wat_lot_ids"], ["LOT_PARTIAL"])
        self.assertIn(
            "WARN_WAT_DATA_MISSING",
            {warning.warning_id for warning in finding.warnings},
        )
        self.assertNotIn(
            "WARN_WAT_NO_FAILURE",
            {warning.warning_id for warning in finding.warnings},
        )

    def test_defect_wat_agent_does_not_build_or_deserialize_evidence(self) -> None:
        source = inspect.getsource(DefectWATAgent)
        self.assertNotIn("EvidenceBuilder", source)
        self.assertNotIn('data.get("evidence"', source)
        self.assertNotIn('data["evidence"]', source)


if __name__ == "__main__":
    unittest.main()
