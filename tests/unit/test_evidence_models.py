from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    AgentKind,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
    ModelValidationError,
)


def make_typed_evidence() -> Evidence:
    return Evidence(
        evidence_id="EV_FDC_SLURRY_FLOW",
        source_type=EvidenceSourceType.FDC.value,
        source_id="fdc_feature:6400:slurry_flow",
        summary="Slurry flow mean decreased from 150 to 132 ml/min.",
        source_table="fdc_feature",
        source_field="feature_mean",
        timestamp="2026-07-15T10:30:00+00:00",
        metadata={"baseline": 150.0, "observed": 132.0, "unit": "ml/min"},
        evidence_type=EvidenceType.PARAMETER_DEVIATION.value,
        source_agent=AgentKind.FDC.value,
        source_tool="analyze_parameter_shift",
        observation="Slurry flow mean decreased from 150 to 132 ml/min.",
        entities=[
            EvidenceEntity(
                entity_type=EntityType.CHAMBER.value,
                entity_id="CMP_CU03_CH02",
                attributes={"module": "CU_CMP"},
            ),
            EvidenceEntity(
                entity_type=EntityType.PARAMETER.value,
                entity_id="slurry_flow",
            ),
        ],
        confidence=0.98,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


class EvidenceModelTest(unittest.TestCase):
    def test_typed_evidence_round_trip(self) -> None:
        evidence = make_typed_evidence()

        restored = Evidence.from_dict(evidence.to_dict())

        self.assertTrue(restored.is_typed)
        self.assertEqual(restored.to_dict(), evidence.to_dict())
        self.assertEqual(restored.entities[0].entity_id, "CMP_CU03_CH02")
        json.dumps(restored.to_dict())

    def test_typed_evidence_is_deeply_immutable(self) -> None:
        metadata: dict[str, Any] = {
            "baseline": 150.0,
            "windows": [{"start": "2026-07-01", "lots": ["LOT_A_001"]}],
        }
        entities = [
            EvidenceEntity(
                entity_type=EntityType.LOT.value,
                entity_id="LOT_A_001",
                attributes={"roles": ["source"]},
            )
        ]
        evidence = Evidence(
            evidence_id="EV_IMMUTABLE",
            source_type=EvidenceSourceType.MES.value,
            source_id="lot_master:LOT_A_001",
            summary="Immutable Lot context.",
            metadata=metadata,
            evidence_type=EvidenceType.LOT_CONTEXT.value,
            source_agent=AgentKind.MES.value,
            source_tool="get_lot_context",
            observation="Immutable Lot context.",
            entities=entities,
            confidence=1.0,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        )

        metadata["windows"][0]["lots"].append("LOT_MUTATED")
        entities.append(
            EvidenceEntity(
                entity_type=EntityType.LOT.value,
                entity_id="LOT_MUTATED",
            )
        )
        serialized_metadata = evidence.to_dict()["metadata"]
        self.assertIsInstance(serialized_metadata, dict)
        if not isinstance(serialized_metadata, dict):
            self.fail("serialized Evidence metadata must be a JSON object")
        self.assertEqual(
            serialized_metadata["windows"][0]["lots"],
            ["LOT_A_001"],
        )
        self.assertEqual(len(evidence.entities), 1)

        immutable_entities: Any = evidence.entities
        immutable_metadata: Any = evidence.metadata
        immutable_attributes: Any = evidence.entities[0].attributes
        with self.assertRaises(AttributeError):
            immutable_entities.append(entities[-1])
        with self.assertRaises(TypeError):
            immutable_metadata["baseline"] = 132.0
        with self.assertRaises(TypeError):
            immutable_attributes["roles"] = ["impact"]

    def test_legacy_evidence_json_contract_is_unchanged(self) -> None:
        evidence = Evidence(
            evidence_id="EV_MES_COMMON_CHAMBER",
            source_type=EvidenceSourceType.MES.value,
            source_id="process_history:CMP_CU03_CH02",
            summary="Affected lots share CMP_CU03_CH02.",
            metadata={"lot_count": 4},
        )

        payload = evidence.to_dict()

        self.assertFalse(evidence.is_typed)
        self.assertNotIn("evidence_type", payload)
        self.assertNotIn("entities", payload)
        self.assertEqual(Evidence.from_dict(payload).to_dict(), payload)

    def test_partial_typed_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "all V1 fields"):
            Evidence(
                evidence_id="EV_PARTIAL",
                source_type=EvidenceSourceType.FDC.value,
                source_id="fdc_feature:1",
                summary="Partial evidence.",
                evidence_type=EvidenceType.PARAMETER_DEVIATION.value,
            )

    def test_typed_evidence_requires_entity(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "at least one entity"):
            Evidence(
                evidence_id="EV_NO_ENTITY",
                source_type=EvidenceSourceType.FDC.value,
                source_id="fdc_feature:1",
                summary="No entity.",
                evidence_type=EvidenceType.PARAMETER_DEVIATION.value,
                source_agent=AgentKind.FDC.value,
                source_tool="analyze_parameter_shift",
                observation="No entity.",
                confidence=0.8,
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            )

    def test_unknown_type_and_agent_are_rejected(self) -> None:
        base = make_typed_evidence().to_dict()
        base["evidence_type"] = "unsupported_type"
        with self.assertRaisesRegex(ModelValidationError, "evidence_type"):
            Evidence.from_dict(base)

        base = make_typed_evidence().to_dict()
        base["source_agent"] = "unregistered_agent"
        with self.assertRaisesRegex(ModelValidationError, "source_agent"):
            Evidence.from_dict(base)

    def test_entity_validation_rejects_unknown_type(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "entity_type"):
            EvidenceEntity(entity_type="factory_object", entity_id="OBJECT_001")


if __name__ == "__main__":
    unittest.main()
