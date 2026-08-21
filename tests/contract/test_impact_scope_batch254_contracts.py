from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_confirmation import evaluate_impact_lot_gate  # noqa: E402
from yield_rca_core.causal_hypothesis import CausalHypothesis  # noqa: E402
from yield_rca_core.evidence_models import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
)


def _evidence(
    evidence_id: str,
    evidence_type: str,
    lot_id: str,
    entities: list[EvidenceEntity],
    *,
    source_type: str = EvidenceSourceType.FDC.value,
    timestamp: str = "2026-01-01T00:30:00+00:00",
    metadata: dict[str, object] | None = None,
    source_field: str | None = None,
    observation: str = "typed observation",
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
        entities=[EvidenceEntity(EntityType.LOT.value, lot_id), *entities],
        metadata=metadata or {},
        timestamp=timestamp,
        confidence=0.95,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


def _candidate() -> CausalHypothesis:
    return CausalHypothesis(
        root_cause="EQ_01 CH_01 OP_4000 temperature high control drift",
        causal_explanation="High temperature causes the observed edge_void outcome.",
        supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_OUTCOME"),
    )


def _lot_evidence(
    lot_id: str,
    *,
    chamber: str = "CH_01",
    operation: str = "OP_4000",
    process_timestamp: str = "2026-01-01T00:30:00+00:00",
    outcome: str = "edge_void",
) -> list[Evidence]:
    lane = [
        EvidenceEntity(EntityType.EQUIPMENT.value, "EQ_01"),
        EvidenceEntity(EntityType.CHAMBER.value, chamber),
        EvidenceEntity(EntityType.OPERATION.value, operation),
        EvidenceEntity(EntityType.RECIPE.value, "RCP_01"),
    ]
    return [
        _evidence(
            f"EV_EXPOSURE_{lot_id}",
            EvidenceType.IMPACT_SCOPE.value,
            lot_id,
            lane,
            source_type=EvidenceSourceType.ANALYTICS.value,
            observation=f"{lot_id} exposed to EQ_01 {chamber} {operation}",
        ),
        _evidence(
            f"EV_PROCESS_{lot_id}",
            EvidenceType.PARAMETER_DEVIATION.value,
            lot_id,
            [*lane, EvidenceEntity(EntityType.PARAMETER.value, "temperature")],
            timestamp=process_timestamp,
            source_field="temperature",
            metadata={
                "direction": "high",
                "excursion_start": "2026-01-01T00:00:00+00:00",
                "excursion_end": "2026-01-01T01:00:00+00:00",
            },
            observation="temperature high during OP_4000",
        ),
        _evidence(
            f"EV_OUTCOME_{lot_id}",
            EvidenceType.DEFECT_SIGNAL.value,
            lot_id,
            [EvidenceEntity(EntityType.DEFECT.value, outcome)],
            source_type=EvidenceSourceType.DEFECT.value,
            observation=f"{outcome} observed on {lot_id}",
        ),
    ]


def test_impact_scope_status_is_confirmed_for_a_fully_supported_lot() -> None:
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=_lot_evidence("LOT_IMPACT"),
        observed_impact_lots=["LOT_IMPACT"],
    )

    assert result["scope_status"] == "confirmed"
    assert result["publication_status"] == "confirmed"
    assert result["confirmed_impact_lots"] == ["LOT_IMPACT"]
    assert "candidate exposure" in result["scope_basis"]


def test_data_missing_blocks_only_the_affected_lot_and_keeps_trace_ids() -> None:
    missing = _evidence(
        "EV_FDC_DATA_MISSING_LOT_02",
        EvidenceType.DATA_MISSING.value,
        "LOT_02",
        [],
        source_field="temperature",
        metadata={"required_for_impact_scope": True},
        observation="FDC history is unavailable for LOT_02.",
    )
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=[*_lot_evidence("LOT_01"), *_lot_evidence("LOT_02"), missing],
        observed_impact_lots=["LOT_01", "LOT_02"],
    )

    row_01 = next(row for row in result["rows"] if row["lot_id"] == "LOT_01")
    row_02 = next(row for row in result["rows"] if row["lot_id"] == "LOT_02")
    assert result["scope_status"] == "partial"
    assert result["confirmed_impact_lots"] == ["LOT_01"]
    assert row_01["included"] is True
    assert row_02["included"] is False
    assert row_02["data_missing_evidence_ids"] == ["EV_FDC_DATA_MISSING_LOT_02"]
    assert "EV_FDC_DATA_MISSING_LOT_02" in row_02["excluded_reason"]
    assert result["data_missing_evidence_ids"] == ["EV_FDC_DATA_MISSING_LOT_02"]


def test_redundant_spc_data_missing_is_audited_without_blocking_scope() -> None:
    missing = _evidence(
        "EV_SPC_BASELINE_UNAVAILABLE",
        EvidenceType.DATA_MISSING.value,
        "LOT_01",
        [EvidenceEntity(EntityType.PARAMETER.value, "temperature")],
        source_type=EvidenceSourceType.ANALYTICS.value,
        observation="SPC baseline sample count is insufficient.",
    )
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=[*_lot_evidence("LOT_01"), missing, missing],
        observed_impact_lots=["LOT_01"],
    )

    row = result["rows"][0]
    assert result["scope_status"] == "confirmed"
    assert result["publication_status"] == "confirmed"
    assert result["candidate_impact_lots"] == ["LOT_01"]
    assert result["confirmed_impact_lots"] == ["LOT_01"]
    assert result["data_missing_evidence_ids"] == []
    assert result["non_blocking_data_missing_evidence_ids"] == [
        "EV_SPC_BASELINE_UNAVAILABLE"
    ]
    assert row["included"] is True
    assert row["data_missing_evidence_ids"] == []
    assert row["non_blocking_data_missing_evidence_ids"] == [
        "EV_SPC_BASELINE_UNAVAILABLE"
    ]


def test_inconclusive_rca_preserves_candidate_scope_but_publishes_no_lots() -> None:
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=_lot_evidence("LOT_01"),
        observed_impact_lots=["LOT_01"],
        authoritative_conclusion_status="insufficient_evidence",
    )

    row = result["rows"][0]
    assert result["scope_status"] == "confirmed"
    assert result["publication_status"] == "withheld"
    assert result["candidate_impact_lots"] == ["LOT_01"]
    assert result["confirmed_impact_lots"] == []
    assert result["confirmation_blocked_reason"] == (
        "authoritative RCA conclusion is not supported"
    )
    assert row["candidate_included"] is True
    assert row["confirmed"] is False


def test_metrology_outcome_phrase_is_available_to_candidate_scope() -> None:
    evidence = _lot_evidence("LOT_01")[:-1]
    evidence.append(
        _evidence(
            "EV_CENTER_VOID_OUTCOME",
            EvidenceType.METROLOGY_DEVIATION.value,
            "LOT_01",
            [
                EvidenceEntity(
                    EntityType.PARAMETER.value,
                    "INCIDENT_OBSERVATION:center seam-void density",
                )
            ],
            source_type=EvidenceSourceType.ANALYTICS.value,
            metadata={"metric_name": "center seam-void density"},
            observation="Center seam-void density is out of specification.",
        )
    )
    candidate = CausalHypothesis(
        root_cause="EQ_01 CH_01 OP_4000 temperature high control drift",
        causal_explanation=(
            "High temperature causes center seam voids and insufficient copper fill."
        ),
        supporting_evidence_ids=(
            "EV_EXPOSURE_LOT_01",
            "EV_PROCESS_LOT_01",
            "EV_CENTER_VOID_OUTCOME",
        ),
    )

    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=candidate,
        evidence=evidence,
        observed_impact_lots=["LOT_01"],
        authoritative_conclusion_status="insufficient_evidence",
    )

    assert result["candidate_impact_lots"] == ["LOT_01"]
    assert result["confirmed_impact_lots"] == []
    assert result["rows"][0]["checks"]["outcome"] is True
    assert "EV_CENTER_VOID_OUTCOME" in result["rows"][0][
        "supporting_evidence_ids"
    ]


def test_unrelated_metrology_metric_is_not_a_compatible_outcome() -> None:
    evidence = _lot_evidence("LOT_01")[:-1]
    evidence.append(
        _evidence(
            "EV_UNRELATED_METROLOGY",
            EvidenceType.METROLOGY_DEVIATION.value,
            "LOT_01",
            [EvidenceEntity(EntityType.PARAMETER.value, "surface particle monitor")],
            source_type=EvidenceSourceType.ANALYTICS.value,
            metadata={"metric_name": "surface particle monitor"},
            observation="Surface particle monitor is out of specification.",
        )
    )

    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=evidence,
        observed_impact_lots=["LOT_01"],
    )

    assert result["candidate_impact_lots"] == []
    assert result["rows"][0]["checks"]["outcome"] is False


def test_required_missing_source_from_another_lane_does_not_block_scope() -> None:
    unrelated_missing = _evidence(
        "EV_OTHER_LANE_DATA_MISSING",
        EvidenceType.DATA_MISSING.value,
        "LOT_01",
        [
            EvidenceEntity(EntityType.EQUIPMENT.value, "EQ_99"),
            EvidenceEntity(EntityType.CHAMBER.value, "CH_99"),
            EvidenceEntity(EntityType.OPERATION.value, "OP_9000"),
        ],
        metadata={"required_for_impact_scope": True},
        observation="The unrelated Lane has no process history.",
    )
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=[*_lot_evidence("LOT_01"), unrelated_missing],
        observed_impact_lots=["LOT_01"],
    )

    assert result["candidate_impact_lots"] == ["LOT_01"]
    assert result["data_missing_evidence_ids"] == []
    assert result["non_blocking_data_missing_evidence_ids"] == []


def test_source_lot_is_never_returned_as_an_impact_lot() -> None:
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=_lot_evidence("LOT_SOURCE"),
        observed_impact_lots=["LOT_SOURCE"],
    )

    row = result["rows"][0]
    assert result["confirmed_impact_lots"] == []
    assert row["included"] is False
    assert row["excluded_reason"] == "source_lot_is_not_an_impact_lot"
    assert row["checks"] == {"source_lot": False}


def test_scope_distinguishes_wrong_chamber_operation_and_time_window() -> None:
    wrong_chamber = _lot_evidence("LOT_WRONG_CHAMBER", chamber="CH_02")
    wrong_operation = _lot_evidence("LOT_WRONG_OPERATION", operation="OP_5000")
    out_of_window = _lot_evidence(
        "LOT_OUT_OF_WINDOW",
        process_timestamp="2026-01-01T02:00:00+00:00",
    )
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=[*wrong_chamber, *wrong_operation, *out_of_window],
        observed_impact_lots=[
            "LOT_WRONG_CHAMBER",
            "LOT_WRONG_OPERATION",
            "LOT_OUT_OF_WINDOW",
        ],
    )

    rows = {row["lot_id"]: row for row in result["rows"]}
    assert rows["LOT_WRONG_CHAMBER"]["included"] is False
    assert rows["LOT_WRONG_CHAMBER"]["checks"]["chamber"] is False
    assert rows["LOT_WRONG_OPERATION"]["included"] is False
    assert rows["LOT_WRONG_OPERATION"]["checks"]["operation"] is False
    assert rows["LOT_OUT_OF_WINDOW"]["included"] is False
    assert rows["LOT_OUT_OF_WINDOW"]["checks"]["temporal"] is False
    assert result["scope_status"] == "unconfirmed"


def test_empty_observed_scope_is_not_evaluated() -> None:
    result = evaluate_impact_lot_gate(
        source_lot_id="LOT_SOURCE",
        candidate=_candidate(),
        evidence=[],
        observed_impact_lots=[],
    )

    assert result["scope_status"] == "not_evaluated"
    assert result["confirmed_impact_lots"] == []
