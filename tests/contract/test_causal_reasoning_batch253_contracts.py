from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_chain import (  # noqa: E402
    CausalChainAssessment,
    assess_causal_chain,
    collect_data_missing_sources,
)
from yield_rca_core.causal_confirmation import confirm_candidate  # noqa: E402
from yield_rca_core.causal_evidence_gap import build_causal_evidence_gaps  # noqa: E402
from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix  # noqa: E402
from yield_rca_core.causal_hypothesis import CausalHypothesis  # noqa: E402
from yield_rca_core.causal_investigation_models import CausalChainCompleteness  # noqa: E402
from yield_rca_core.evidence_models import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
)


def evidence(
    evidence_id: str,
    evidence_type: str,
    entities: list[EvidenceEntity],
    *,
    observation: str,
    source_type: str = EvidenceSourceType.FDC.value,
    source_field: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type=source_type,
        source_id=f"SRC_{evidence_id}",
        summary=observation,
        source_field=source_field,
        evidence_type=evidence_type,
        source_agent="fdc",
        source_tool="test_tool",
        observation=observation,
        entities=entities,
        metadata=metadata or {},
        timestamp="2026-01-01T00:30:00+00:00",
        confidence=0.95,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


def candidate() -> CausalHypothesis:
    return CausalHypothesis(
        root_cause="EQ_01 CH_01 OP_4000 pressure control drift",
        causal_explanation="Pressure drift produces the observed center void.",
        supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_OUTCOME"),
    )


def complete_evidence() -> list[Evidence]:
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
            observation="LOT_01 exposed to EQ_01 CH_01 OP_4000",
            source_type=EvidenceSourceType.ANALYTICS.value,
        ),
        evidence(
            "EV_PROCESS",
            EvidenceType.PARAMETER_DEVIATION.value,
            [
                lot,
                EvidenceEntity(EntityType.EQUIPMENT.value, "EQ_01"),
                EvidenceEntity(EntityType.CHAMBER.value, "CH_01"),
                EvidenceEntity(EntityType.OPERATION.value, "OP_4000"),
                EvidenceEntity(EntityType.PARAMETER.value, "pressure"),
            ],
            observation="pressure high during OP_4000",
            source_field="pressure",
            metadata={
                "direction": "high",
                "magnitude": 8.0,
                "excursion_start": "2026-01-01T00:00:00+00:00",
                "excursion_end": "2026-01-01T01:00:00+00:00",
            },
        ),
        evidence(
            "EV_OUTCOME",
            EvidenceType.DEFECT_SIGNAL.value,
            [lot, EvidenceEntity(EntityType.DEFECT.value, "center_void")],
            observation="center void observed on LOT_01",
            source_type=EvidenceSourceType.DEFECT.value,
        ),
    ]


def test_data_missing_source_is_typed_and_traceable() -> None:
    missing = evidence(
        "EV_FDC_MISSING",
        EvidenceType.DATA_MISSING.value,
        [
            EvidenceEntity(EntityType.LOT.value, "LOT_01"),
            EvidenceEntity(EntityType.OPERATION.value, "OP_4000"),
        ],
        observation="FDC history is unavailable for the selected lot.",
        source_field="pressure",
    )
    sources = collect_data_missing_sources([missing])
    assert len(sources) == 1
    assert sources[0].evidence_id == "EV_FDC_MISSING"
    assert sources[0].source_field == "pressure"
    assert sources[0].entity_ids == ("LOT_01", "OP_4000")


def test_missing_required_source_blocks_confirmation_without_fallback() -> None:
    missing = evidence(
        "EV_FDC_MISSING",
        EvidenceType.DATA_MISSING.value,
        [EvidenceEntity(EntityType.LOT.value, "LOT_01")],
        observation="FDC history is unavailable.",
        source_field="pressure",
    )
    items = [item for item in complete_evidence() if item.evidence_id != "EV_PROCESS"]
    matrix = build_causal_evidence_matrix(candidate(), [*items, missing])
    result = confirm_candidate(
        matrix,
        strict=True,
        alternative_search_status="alternatives_eliminated",
    )
    assert result.status == "insufficient_evidence"
    assert result.causal_chain_completeness == CausalChainCompleteness.INCOMPLETE.value
    assert result.checks["causal_chain"] is False
    assert result.data_missing_evidence_ids == ("EV_FDC_MISSING",)
    assert result.blocking_data_missing_evidence_ids == ("EV_FDC_MISSING",)
    assert result.non_blocking_data_missing_evidence_ids == ()
    gaps = build_causal_evidence_gaps([matrix])
    parameter_gap = next(item for item in gaps if item["claim"] == "parameter")
    assert parameter_gap["gap_type"] == "data_missing"
    assert parameter_gap["data_missing_evidence_ids"] == ["EV_FDC_MISSING"]


def test_causal_chain_requires_all_stages_but_controls_are_not_a_stage() -> None:
    assessment = assess_causal_chain(
        {
            "equipment": {"status": "supported", "evidence_ids": ["EV_1"]},
            "scope": {"status": "supported", "evidence_ids": ["EV_1"]},
            "parameter": {"status": "supported", "evidence_ids": ["EV_2"]},
            "mechanism": {"status": "supported", "evidence_ids": ["EV_2"]},
            "outcome": {"status": "supported", "evidence_ids": ["EV_3"]},
            "temporal": {"status": "supported", "evidence_ids": ["EV_2"]},
            "control": {"status": "unavailable", "evidence_ids": []},
        }
    )
    assert isinstance(assessment, CausalChainAssessment)
    assert assessment.status == CausalChainCompleteness.COMPLETE.value
    assert "control" not in assessment.stages


def test_conflicting_chain_is_not_reported_as_insufficient_data() -> None:
    assessment = assess_causal_chain(
        {
            "equipment": {"status": "supported"},
            "scope": {"status": "supported"},
            "parameter": {"status": "conflicted"},
            "mechanism": {"status": "supported"},
            "outcome": {"status": "supported"},
            "temporal": {"status": "supported"},
        }
    )
    assert assessment.status == CausalChainCompleteness.CONFLICTING.value
    assert assessment.conflicting_stages == ("parameter",)
