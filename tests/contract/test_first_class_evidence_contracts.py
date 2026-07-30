from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    AgentFinding,
    AgentKind,
    EntityType,
    Evidence,
    EvidenceBuilder,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    ModelValidationError,
    ToolInput,
    ToolOutput,
)


def make_evidence(
    *,
    evidence_id: str = "EV_MES_COMMON_CHAMBER",
    observation: str = "Affected lots share CMP_CU03_CH02.",
) -> Evidence:
    tool_input = ToolInput(
        tool_name="analyze_lot_genealogy",
        request_id=f"REQ_{evidence_id}",
        parameters={"lot_ids": ["LOT_A_015"]},
        requested_by=AgentKind.MES.value,
    )
    return EvidenceBuilder.from_tool(
        tool_input=tool_input,
        evidence_id=evidence_id,
        evidence_type=EvidenceType.EQUIPMENT_EXPOSURE,
        source_type=EvidenceSourceType.MES,
        observation=observation,
        entities=[
            EvidenceEntity(
                entity_type=EntityType.CHAMBER.value,
                entity_id="CMP_CU03_CH02",
            )
        ],
        confidence=0.99,
        source_id="process_history:CMP_CU03_CH02",
        source_table="process_history",
        source_field="chamber_id",
    )


def assert_envelope_consistent(
    test_case: unittest.TestCase,
    *,
    evidence_ids: list[str],
    evidence: list[Evidence],
    legacy_payload: list[dict[str, Any]],
) -> None:
    mirrored = [Evidence.from_dict(item) for item in legacy_payload]
    test_case.assertEqual(evidence_ids, [item.evidence_id for item in evidence])
    test_case.assertEqual(evidence, mirrored)


class FirstClassEvidenceContractTest(unittest.TestCase):
    def test_tool_output_first_class_evidence_creates_legacy_mirror(self) -> None:
        evidence = make_evidence()
        output = ToolOutput(
            tool_name="analyze_lot_genealogy",
            request_id="REQ_FIRST_CLASS_TOOL",
            success=True,
            data={"common_chamber": "CMP_CU03_CH02"},
            evidence=[evidence],
        )

        assert_envelope_consistent(
            self,
            evidence_ids=output.evidence_ids,
            evidence=output.evidence,
            legacy_payload=output.data["evidence"],
        )
        self.assertEqual(ToolOutput.from_dict(output.to_dict()), output)

    def test_tool_output_legacy_mirror_hydrates_first_class_evidence(self) -> None:
        evidence = make_evidence()
        output = ToolOutput(
            tool_name="analyze_lot_genealogy",
            request_id="REQ_LEGACY_TOOL",
            success=True,
            data={"evidence": [evidence.to_dict()]},
            evidence_ids=[evidence.evidence_id],
        )

        self.assertEqual(output.evidence, [evidence])
        self.assertEqual(output.to_dict()["evidence"], [evidence.to_dict()])

    def test_agent_finding_first_class_evidence_creates_legacy_mirror(self) -> None:
        evidence = make_evidence()
        finding = AgentFinding(
            finding_id="FINDING_FIRST_CLASS_MES",
            agent=AgentKind.MES.value,
            summary="MES commonality identified.",
            confidence=0.95,
            evidence_ids=[],
            evidence=[evidence],
            details={"target_operation_no": "6400"},
        )

        assert_envelope_consistent(
            self,
            evidence_ids=finding.evidence_ids,
            evidence=finding.evidence,
            legacy_payload=finding.details["evidence"],
        )
        self.assertEqual(AgentFinding.from_dict(finding.to_dict()), finding)

    def test_agent_finding_legacy_mirror_hydrates_first_class_evidence(self) -> None:
        evidence = make_evidence()
        finding = AgentFinding(
            finding_id="FINDING_LEGACY_MES",
            agent=AgentKind.MES.value,
            summary="MES commonality identified.",
            confidence=0.95,
            evidence_ids=[evidence.evidence_id],
            details={"evidence": [evidence.to_dict()]},
        )

        self.assertEqual(finding.evidence, [evidence])
        self.assertEqual(finding.to_dict()["evidence"], [evidence.to_dict()])

    def test_conflicting_first_class_and_legacy_payload_is_rejected(self) -> None:
        evidence = make_evidence()
        conflicting = make_evidence(observation="Affected lots do not share a chamber.")

        with self.assertRaisesRegex(ModelValidationError, "identical payloads"):
            ToolOutput(
                tool_name="analyze_lot_genealogy",
                request_id="REQ_CONFLICT",
                success=True,
                data={"evidence": [conflicting.to_dict()]},
                evidence_ids=[evidence.evidence_id],
                evidence=[evidence],
            )

    def test_mismatched_evidence_ids_are_rejected(self) -> None:
        evidence = make_evidence()

        with self.assertRaisesRegex(ModelValidationError, "same order"):
            AgentFinding(
                finding_id="FINDING_BAD_IDS",
                agent=AgentKind.MES.value,
                summary="Invalid evidence references.",
                confidence=0.5,
                evidence_ids=["EV_DIFFERENT"],
                evidence=[evidence],
            )

    def test_ids_only_legacy_finding_remains_supported(self) -> None:
        finding = AgentFinding(
            finding_id="FINDING_IDS_ONLY",
            agent=AgentKind.MES.value,
            summary="Legacy external payload.",
            confidence=0.5,
            evidence_ids=["EV_EXTERNAL"],
        )

        self.assertEqual(finding.evidence, [])
        self.assertNotIn("evidence", finding.details)
        self.assertEqual(AgentFinding.from_dict(finding.to_dict()), finding)


if __name__ == "__main__":
    unittest.main()
