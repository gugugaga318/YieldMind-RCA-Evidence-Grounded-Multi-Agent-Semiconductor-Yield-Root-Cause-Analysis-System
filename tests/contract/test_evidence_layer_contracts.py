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
    EvidenceCollection,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    ModelValidationError,
    ToolInput,
)


def make_evidence(
    evidence_id: str,
    observation: str = "Slurry flow is below baseline.",
) -> Evidence:
    tool_input = ToolInput(
        tool_name="analyze_parameter_shift",
        request_id=f"req_{evidence_id}",
        parameters={"chamber_id": "CMP_CU03_CH02"},
        requested_by=AgentKind.FDC.value,
    )
    return EvidenceBuilder.from_tool(
        tool_input=tool_input,
        evidence_id=evidence_id,
        evidence_type=EvidenceType.PARAMETER_DEVIATION,
        source_type=EvidenceSourceType.FDC,
        observation=observation,
        entities=[
            EvidenceEntity(
                entity_type=EntityType.CHAMBER.value,
                entity_id="CMP_CU03_CH02",
            )
        ],
        confidence=0.98,
        source_id=f"fdc_feature:{evidence_id}",
    )


class EvidenceLayerContractTest(unittest.TestCase):
    def test_same_id_same_payload_is_idempotent(self) -> None:
        evidence = make_evidence("EV_FDC_SLURRY_FLOW")
        collection = EvidenceCollection([evidence])

        collection.add(evidence)

        self.assertEqual(len(collection), 1)
        self.assertIs(collection.get(evidence.evidence_id), evidence)

    def test_same_id_different_payload_is_rejected_atomically(self) -> None:
        original = make_evidence("EV_FDC_SLURRY_FLOW")
        conflicting = make_evidence(
            "EV_FDC_SLURRY_FLOW",
            observation="Slurry flow is above baseline.",
        )
        collection = EvidenceCollection([original])

        with self.assertRaisesRegex(ModelValidationError, "conflicting payload"):
            collection.merge([make_evidence("EV_OTHER"), conflicting])

        self.assertEqual(collection.to_list(), [original])

    def test_require_rejects_unknown_references(self) -> None:
        collection = EvidenceCollection([make_evidence("EV_FDC_SLURRY_FLOW")])

        with self.assertRaisesRegex(ModelValidationError, "EV_UNKNOWN"):
            collection.require(["EV_FDC_SLURRY_FLOW", "EV_UNKNOWN"])

    def test_collection_filters_by_type_and_entity(self) -> None:
        evidence = make_evidence("EV_FDC_SLURRY_FLOW")
        collection = EvidenceCollection([evidence])

        self.assertEqual(
            collection.by_type(EvidenceType.PARAMETER_DEVIATION),
            [evidence],
        )
        self.assertEqual(
            collection.by_entity(EntityType.CHAMBER, "CMP_CU03_CH02"),
            [evidence],
        )
        self.assertEqual(collection.by_entity(EntityType.LOT), [])


if __name__ == "__main__":
    unittest.main()
