from __future__ import annotations

import inspect
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import Evidence, ToolInput, ToolOutput  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.specialist_agents import MESAgent  # noqa: E402
from yield_rca_core.tool_layer import (  # noqa: E402
    AnalyzeLotGenealogyTool,
    FindAffectedLotsTool,
    FindImpactLotsTool,
    GetLotContextTool,
)

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


@dataclass
class RecordingTool:
    delegate: Any
    outputs: list[ToolOutput] = field(default_factory=list)

    def run(self, tool_input: ToolInput) -> ToolOutput:
        output = cast(ToolOutput, self.delegate.run(tool_input))
        # A first-class consumer must not depend on the generated compatibility mirror.
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


class MESAgentFirstClassEvidenceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        repository = CsvFabRepository(SEED_DIR)
        self.find_affected = RecordingTool(FindAffectedLotsTool(repository))
        self.genealogy = RecordingTool(AnalyzeLotGenealogyTool(repository))
        self.context = RecordingTool(GetLotContextTool(repository))
        self.impact = RecordingTool(FindImpactLotsTool(repository))
        self.agent = MESAgent(
            find_affected_lots_tool=cast(Any, self.find_affected),
            analyze_lot_genealogy_tool=cast(Any, self.genealogy),
            get_lot_context_tool=cast(Any, self.context),
            find_impact_lots_tool=cast(Any, self.impact),
        )

    def assert_finding_carries_original_tool_evidence(
        self,
        finding_evidence: list[Evidence],
        expected: list[Evidence],
    ) -> None:
        self.assertEqual(finding_evidence, expected)
        self.assertTrue(
            all(actual is source for actual, source in zip(finding_evidence, expected, strict=True))
        )

    def test_product_driven_path_consumes_first_class_tool_evidence(self) -> None:
        finding = self.agent.analyze(
            request_id="REQ_MES_FIRST_CLASS_PRODUCT",
            product_id="40N_SOC",
            start_date="2026-07-01",
            end_date="2026-07-31",
        )

        expected = merged_tool_evidence(self.find_affected, self.genealogy)
        self.assert_finding_carries_original_tool_evidence(finding.evidence, expected)
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in expected])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in expected],
        )

    def test_no_affected_lots_path_consumes_first_class_tool_evidence(self) -> None:
        finding = self.agent.analyze(
            request_id="REQ_MES_FIRST_CLASS_EMPTY",
            product_id="UNKNOWN_PRODUCT",
        )

        expected = merged_tool_evidence(self.find_affected)
        self.assert_finding_carries_original_tool_evidence(finding.evidence, expected)
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in expected])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in expected],
        )
        self.assertEqual(self.genealogy.outputs, [])

    def test_lot_driven_path_consumes_first_class_tool_evidence(self) -> None:
        finding = self.agent.analyze_lot(
            request_id="REQ_MES_FIRST_CLASS_LOT",
            lot_id="LOT_A_001",
        )

        expected = merged_tool_evidence(self.context, self.impact, self.genealogy)
        self.assert_finding_carries_original_tool_evidence(finding.evidence, expected)
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in expected])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in expected],
        )

    def test_mes_agent_does_not_build_or_deserialize_evidence(self) -> None:
        source = inspect.getsource(MESAgent)
        self.assertNotIn("EvidenceBuilder", source)
        self.assertNotIn('data.get("evidence"', source)
        self.assertNotIn('data["evidence"]', source)


if __name__ == "__main__":
    unittest.main()
