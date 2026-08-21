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
    EvidenceBuilder,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    LegacyEvidenceAdapter,
    ModelValidationError,
    ToolInput,
)


class EvidenceBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tool_input = ToolInput(
            tool_name="analyze_lot_genealogy",
            request_id="req_evidence_001",
            parameters={"lots": ["LOT_A_015"]},
            requested_by=AgentKind.MES.value,
        )
        self.entity = EvidenceEntity(
            entity_type=EntityType.CHAMBER.value,
            entity_id="CMP_CU03_CH02",
        )

    def test_builder_derives_agent_and_tool_source(self) -> None:
        evidence = EvidenceBuilder.from_tool(
            tool_input=self.tool_input,
            evidence_id="EV_MES_COMMON_CHAMBER",
            evidence_type=EvidenceType.EQUIPMENT_EXPOSURE,
            source_type=EvidenceSourceType.MES,
            observation="Affected lots share CMP_CU03_CH02.",
            entities=[self.entity],
            confidence=0.99,
            source_id="process_history:CMP_CU03_CH02",
            source_table="process_history",
            source_field="chamber_id",
        )

        self.assertTrue(evidence.is_typed)
        self.assertEqual(evidence.source_agent, AgentKind.MES.value)
        self.assertEqual(evidence.source_tool, self.tool_input.tool_name)
        self.assertEqual(evidence.summary, evidence.observation)
        self.assertEqual(evidence.evidence_type, EvidenceType.EQUIPMENT_EXPOSURE.value)

    def test_builder_scopes_evidence_identity_by_causal_lane(self) -> None:
        lane_a_input = ToolInput(
            tool_name="perform_basic_spc_analysis",
            request_id="req_lane_a",
            parameters={"lane_id": "lane:4000:EQ_A:CH01:R1"},
            requested_by=AgentKind.FDC.value,
        )
        lane_b_input = ToolInput(
            tool_name="perform_basic_spc_analysis",
            request_id="req_lane_b",
            parameters={"lane_id": "lane:4000:EQ_B:CH01:R1"},
            requested_by=AgentKind.FDC.value,
        )

        lane_a_id = EvidenceBuilder.scoped_evidence_id(
            lane_a_input,
            "EV_SPC_BASELINE_STATUS",
        )
        lane_b_id = EvidenceBuilder.scoped_evidence_id(
            lane_b_input,
            "EV_SPC_BASELINE_STATUS",
        )

        self.assertRegex(lane_a_id, r"^EV_SPC_BASELINE_STATUS_LANE_[A-F0-9]{16}$")
        self.assertNotEqual(lane_a_id, lane_b_id)
        self.assertEqual(
            EvidenceBuilder.scoped_evidence_id(lane_a_input, lane_a_id),
            lane_a_id,
        )
        self.assertEqual(
            EvidenceBuilder.scoped_evidence_id(
                self.tool_input,
                "EV_SPC_BASELINE_STATUS",
            ),
            "EV_SPC_BASELINE_STATUS",
        )

    def test_builder_rejects_missing_observation_and_entities(self) -> None:
        with self.assertRaises(ModelValidationError):
            EvidenceBuilder.from_tool(
                tool_input=self.tool_input,
                evidence_id="EV_INVALID",
                evidence_type=EvidenceType.EQUIPMENT_EXPOSURE,
                source_type=EvidenceSourceType.MES,
                observation="",
                entities=[],
                confidence=0.8,
                source_id="process_history:invalid",
            )

    def test_legacy_adapter_preserves_traceability_fields(self) -> None:
        legacy = Evidence(
            evidence_id="EV_HOLD_COMMENT",
            source_type=EvidenceSourceType.MES.value,
            source_id="hold_history:HOLD_001",
            summary="Lot held after post-CMP metrology failure.",
            source_table="hold_history",
            source_field="hold_comment",
            timestamp="2026-07-15T11:00:00+00:00",
            metadata={"hold_id": "HOLD_001"},
        )

        typed = LegacyEvidenceAdapter.to_typed(
            legacy,
            evidence_type=EvidenceType.HOLD_EVENT,
            source_agent=AgentKind.MES.value,
            source_tool="analyze_lot_genealogy",
            entities=[
                EvidenceEntity(
                    entity_type=EntityType.LOT.value,
                    entity_id="LOT_A_015",
                )
            ],
            confidence=1.0,
        )

        self.assertEqual(typed.evidence_id, legacy.evidence_id)
        self.assertEqual(typed.source_id, legacy.source_id)
        self.assertEqual(typed.metadata, legacy.metadata)
        self.assertEqual(typed.observation, legacy.summary)

    def test_legacy_adapter_rejects_already_typed_evidence(self) -> None:
        typed = EvidenceBuilder.from_tool(
            tool_input=self.tool_input,
            evidence_id="EV_TYPED",
            evidence_type=EvidenceType.EQUIPMENT_EXPOSURE,
            source_type=EvidenceSourceType.MES,
            observation="Affected lots share CMP_CU03_CH02.",
            entities=[self.entity],
            confidence=0.99,
            source_id="process_history:CMP_CU03_CH02",
        )

        with self.assertRaisesRegex(ModelValidationError, "requires legacy"):
            LegacyEvidenceAdapter.to_typed(
                typed,
                evidence_type=EvidenceType.EQUIPMENT_EXPOSURE,
                source_agent=AgentKind.MES.value,
                source_tool="analyze_lot_genealogy",
                entities=[self.entity],
                confidence=0.99,
            )


if __name__ == "__main__":
    unittest.main()
