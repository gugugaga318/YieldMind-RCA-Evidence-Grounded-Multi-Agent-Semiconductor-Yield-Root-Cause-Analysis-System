from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_candidate_comparison import compare_candidate_matrices  # noqa: E402
from yield_rca_core.causal_confirmation import (  # noqa: E402
    confirm_candidate,
    evaluate_impact_lot_gate,
)
from yield_rca_core.causal_evidence_gap import build_causal_evidence_gaps  # noqa: E402
from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix  # noqa: E402
from yield_rca_core.causal_hypothesis import CausalHypothesis  # noqa: E402
from yield_rca_core.evidence_models import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
)
from yield_rca_core.evidence_synthesis import build_evidence_synthesis  # noqa: E402


def evidence(
    evidence_id: str,
    evidence_type: str,
    entities: list[EvidenceEntity],
    *,
    source_agent: str = "fdc",
    source_type: str = EvidenceSourceType.FDC.value,
    observation: str = "typed observation",
    metadata: dict[str, object] | None = None,
    timestamp: str | None = "2026-01-01T00:30:00",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type=source_type,
        source_id=f"SRC_{evidence_id}",
        summary=observation,
        evidence_type=evidence_type,
        source_agent=source_agent,
        source_tool="test_tool",
        observation=observation,
        entities=entities,
        metadata=metadata or {},
        timestamp=timestamp,
        confidence=0.95,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


def causal_evidence() -> list[Evidence]:
    lot = EvidenceEntity(EntityType.LOT.value, "LOT_01")
    return [
        evidence(
            "EV_EXPOSURE",
            EvidenceType.IMPACT_SCOPE.value,
            [
                lot,
                EvidenceEntity(EntityType.EQUIPMENT.value, "EQ_01"),
                EvidenceEntity(EntityType.CHAMBER.value, "CH_01"),
                EvidenceEntity(EntityType.OPERATION.value, "OP_4000"),
            ],
            source_agent="mes",
            source_type=EvidenceSourceType.ANALYTICS.value,
            observation="LOT_01 exposed to EQ_01 CH_01 OP_4000",
        ),
        evidence(
            "EV_PROCESS",
            EvidenceType.PARAMETER_DEVIATION.value,
            [lot, EvidenceEntity(EntityType.PARAMETER.value, "temperature")],
            metadata={"direction": "high", "magnitude": 8.0},
            observation="temperature high during OP_4000",
        ),
        evidence(
            "EV_OUTCOME",
            EvidenceType.DEFECT_SIGNAL.value,
            [lot, EvidenceEntity(EntityType.DEFECT.value, "edge_void")],
            source_agent="defect_wat",
            source_type=EvidenceSourceType.DEFECT.value,
            observation="edge void observed on LOT_01",
        ),
    ]


def candidate() -> CausalHypothesis:
    return CausalHypothesis(
        root_cause="EQ_01 CH_01 OP_4000 temperature control drift",
        causal_explanation="High temperature produces the observed edge_void outcome.",
        supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_OUTCOME"),
    )


class Batch242CausalReasoningContractTest(unittest.TestCase):
    def test_synthesis_is_compact_traceable_and_json_safe(self) -> None:
        payload = build_evidence_synthesis(causal_evidence())
        self.assertEqual(payload["evidence_ids"], ["EV_EXPOSURE", "EV_OUTCOME", "EV_PROCESS"])
        self.assertEqual(payload["evidence_count"], 3)
        self.assertEqual(
            payload["process_excursions"][0]["evidence_id"],
            "EV_PROCESS",
        )
        json.dumps(payload)

    def test_confirmation_requires_typed_claims_but_not_controls(self) -> None:
        matrix = build_causal_evidence_matrix(candidate(), causal_evidence())
        result = confirm_candidate(matrix, strict=True)
        self.assertEqual(result.status, "supported")
        self.assertTrue(result.checks["mechanism"])
        self.assertTrue(result.checks["temporal"])
        self.assertTrue(result.checks["control_informational"])

    def test_matrix_gaps_map_to_registered_actions_and_skip_control(self) -> None:
        matrix = build_causal_evidence_matrix(
            candidate(),
            [causal_evidence()[0], causal_evidence()[2]],
        )
        gaps = build_causal_evidence_gaps([matrix])
        mechanism = next(item for item in gaps if item["claim"] == "mechanism")
        self.assertEqual(mechanism["question_kind"], "process_mechanism")
        self.assertIn("inspect_fdc_spc", mechanism["allowed_actions"])
        self.assertFalse(any(item["claim"] == "control" for item in gaps))

    def test_impact_lot_gate_requires_exposure_process_and_outcome(self) -> None:
        result = evaluate_impact_lot_gate(
            source_lot_id="LOT_SOURCE",
            candidate=candidate(),
            evidence=causal_evidence(),
            observed_impact_lots=["LOT_01", "LOT_MISSING"],
        )
        self.assertEqual(result["confirmed_impact_lots"], ["LOT_01"])
        missing = next(item for item in result["rows"] if item["lot_id"] == "LOT_MISSING")
        self.assertIn("exposure", missing["excluded_reason"])

    def test_python_comparison_returns_null_for_an_equal_tie(self) -> None:
        matrix = build_causal_evidence_matrix(candidate(), causal_evidence())
        result = compare_candidate_matrices([matrix, matrix])
        self.assertIsNone(result["preferred_candidate_index"])
        self.assertTrue(result["unresolved"])


if __name__ == "__main__":
    unittest.main()
