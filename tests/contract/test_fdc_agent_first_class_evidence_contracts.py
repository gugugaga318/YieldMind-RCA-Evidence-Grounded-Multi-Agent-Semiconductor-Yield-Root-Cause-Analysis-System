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
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.specialist_agents import FDCAgent  # noqa: E402
from yield_rca_core.tool_layer import (  # noqa: E402
    AnalyzeParameterShiftTool,
    AnalyzeSpcEvidenceTool,
    FindOocEventsTool,
    PerformBasicSpcAnalysisTool,
)

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
SPC_SEED_DIR = ROOT / "data" / "seeds" / "spc_case"


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


def merged_tool_evidence(*tools: RecordingTool) -> list[Evidence]:
    evidence_by_id: dict[str, Evidence] = {}
    for tool in tools:
        for output in tool.outputs:
            for evidence in output.evidence:
                evidence_by_id.setdefault(evidence.evidence_id, evidence)
    return list(evidence_by_id.values())


class FDCAgentFirstClassEvidenceContractTest(unittest.TestCase):
    def build_agent(
        self,
        seed_dir: Path,
    ) -> tuple[
        FDCAgent,
        RecordingTool,
        RecordingTool,
        RecordingTool,
        RecordingTool,
    ]:
        repository = CsvFabRepository(seed_dir)
        parameter = RecordingTool(AnalyzeParameterShiftTool(repository))
        ooc = RecordingTool(FindOocEventsTool(repository))
        basic = RecordingTool(PerformBasicSpcAnalysisTool(repository))
        advanced = RecordingTool(AnalyzeSpcEvidenceTool(repository))
        agent = FDCAgent(
            analyze_parameter_shift_tool=cast(Any, parameter),
            find_ooc_events_tool=cast(Any, ooc),
            perform_basic_spc_analysis_tool=cast(Any, basic),
            analyze_spc_evidence_tool=cast(Any, advanced),
        )
        return agent, parameter, ooc, basic, advanced

    def assert_original_tool_evidence_is_transported(
        self,
        finding: AgentFinding,
        expected: list[Evidence],
    ) -> None:
        self.assertEqual(finding.evidence, expected)
        self.assertTrue(
            all(
                actual is source
                for actual, source in zip(finding.evidence, expected, strict=True)
            )
        )
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in expected])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in expected],
        )

    def test_advanced_spc_path_consumes_selected_first_class_evidence(self) -> None:
        agent, parameter, ooc, basic, advanced = self.build_agent(SPC_SEED_DIR)
        finding = agent.analyze(
            request_id="REQ_FDC_FIRST_CLASS_ADVANCED",
            lot_ids=[f"LOT_A_{number:03d}" for number in range(11, 16)],
            operation_no="6400",
            equipment_id="CMP_CU03",
            chamber_id="CMP_CU03_CH02",
        )

        self.assertEqual(basic.outputs, [])
        self.assertEqual(advanced.outputs[0].data["analyzed_parameter_count"], 5)
        expected = merged_tool_evidence(parameter, ooc, advanced)
        self.assert_original_tool_evidence_is_transported(finding, expected)
        self.assertEqual(
            finding.details["spc_method"]["engine"],
            "deterministic_advanced_spc",
        )

    def test_advanced_spc_fallback_transports_only_basic_spc_evidence(self) -> None:
        agent, parameter, ooc, basic, advanced = self.build_agent(GOLDEN_SEED_DIR)
        finding = agent.analyze(
            request_id="REQ_FDC_FIRST_CLASS_FALLBACK",
            lot_ids=[f"LOT_A_{number:03d}" for number in range(1, 21)],
            operation_no="6400",
            equipment_id="CMP_CU03",
            chamber_id="CMP_CU03_CH02",
        )

        self.assertEqual(advanced.outputs[0].data["analyzed_parameter_count"], 0)
        self.assertEqual(len(basic.outputs), 1)
        expected = merged_tool_evidence(parameter, ooc, basic)
        self.assert_original_tool_evidence_is_transported(finding, expected)
        advanced_evidence_ids = {
            item.evidence_id for item in advanced.outputs[0].evidence
        }
        self.assertTrue(advanced_evidence_ids)
        self.assertTrue(advanced_evidence_ids.isdisjoint(finding.evidence_ids))
        self.assertNotIn(
            "WARN_SPC_PROFILE_NOT_FOUND",
            {warning.warning_id for warning in finding.warnings},
        )
        self.assertEqual(
            finding.details["spc_method"]["control_limits"],
            "mean +/- 3 sigma",
        )

    def test_fdc_agent_does_not_build_or_deserialize_evidence(self) -> None:
        source = inspect.getsource(FDCAgent)
        self.assertNotIn("EvidenceBuilder", source)
        self.assertNotIn('data.get("evidence"', source)
        self.assertNotIn('data["evidence"]', source)


if __name__ == "__main__":
    unittest.main()
