"""Synthetic counterexamples; no Formal GT or model invocation."""

from dataclasses import replace

import pytest
from yield_rca_core.evidence_models import Evidence, ModelValidationError
from yield_rca_core.evidence_synthesis import (
    build_lane_first_evidence_synthesis,
    compact_evidence_prompt_card,
    compact_evidence_record,
)
from yield_rca_core.models import ToolInput
from yield_rca_core.tool_layer import SummarizeDefectWatTool


class Repository:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def rows(self, table):
        return self.tables.get(table, [])


def defect(kind, pattern, lot="LOT_A", i=1):
    return dict(
        lot_id=lot,
        wafer_id=f"{lot}_W{i:02}",
        defect_type=kind,
        pattern_type=pattern,
        location_region="STRIPE" if kind == "X" else "EDGE",
        inspection_operation_no="INSPECT",
        defect_count="4",
        inspected_at="2026-01-01T01:00:00Z",
    )


def run(tables, lots=("LOT_A",), lane=None):
    params = {"lot_ids": list(lots)}
    if lane:
        params["lane_id"] = lane
    return SummarizeDefectWatTool(Repository(tables)).run(
        ToolInput(
            tool_name="summarize_defect_wat",
            request_id="REQ",
            requested_by="defect_wat",
            parameters=params,
        )
    )


def lanes():
    return [
        dict(
            lane_id=k,
            recipe=r,
            operation="PROCESS",
            equipment="EQ",
            chamber="CH",
            exposed_lot_ids=lots,
            lifecycle_status="active",
            priority_score=1,
        )
        for k, r, lots in [
            ("LANE_A", "RECIPE_A", ["LOT_A"]),
            ("LANE_B", "RECIPE_B", ["LOT_B", "LOT_C"]),
        ]
    ]


def test_unqueried_other_recipe_is_not_acquired_even_when_raw_rows_exist():
    tables = {"defect_summary": [defect("X", "P", lot) for lot in ("LOT_A", "LOT_B")]}
    first = run(tables, lane="LANE_A")
    synthesis = build_lane_first_evidence_synthesis(first.evidence, lanes())
    other = next(x for x in synthesis["active_causal_lanes"] if x["lane_id"] == "LANE_B")
    assert other["facts"]["outcomes"] == []
    assert other["outcome_observation_coverage"]["status"] == "not_acquired"
    second = run(tables, ("LOT_B", "LOT_C"), "LANE_B")
    synthesis = build_lane_first_evidence_synthesis([*first.evidence, *second.evidence], lanes())
    other = next(x for x in synthesis["active_causal_lanes"] if x["lane_id"] == "LANE_B")
    assert other["outcome_observation_coverage"]["status"] == "acquired"
    entries = other["outcome_observation_coverage"]["observations"]
    assert any(x["lot_id"] == "LOT_C" and x["status"] == "not_observed" for x in entries)
    assert any(x["lot_id"] == "LOT_B" and x["status"] == "observed_abnormal" for x in entries)


def test_conditional_spatial_pattern_and_atomic_source_association():
    # X is the dominant defect but Q is the GLOBAL dominant pattern from Y + Z.
    rows = [defect("X", "P", i=i) for i in range(1, 4)]
    rows += [defect(k, "Q", i=i) for k in ("Y", "Z") for i in range(4, 6)]
    output = run({"defect_summary": rows})
    item = next(x for x in output.evidence if x.evidence_type == "defect_signal")
    assert item.evidence_id == "EV_DEFECT_X"
    assert (
        next(e for e in item.entities if e.entity_type == "defect").attributes["dominant_pattern"]
        == "P"
    )
    for projected in (
        item.to_dict()["metadata"],
        compact_evidence_record(item)["metadata"],
        compact_evidence_prompt_card(item)["typed_details"],
    ):
        associations = projected["defect_spatial_observations"]
        assert len(associations) == 3
        assert all(
            a["defect_type"] == "X"
            and a["pattern_type"] == "P"
            and a["location_region"] == "STRIPE"
            for a in associations
        )
        assert all(
            a["source_table"] == "defect_summary"
            and a["source_record_hash"]
            and a["lot_id"] == "LOT_A"
            and a["inspection_operation_no"] == "INSPECT"
            and a["inspected_at"]
            for a in associations
        )
    assert Evidence.from_dict(item.to_dict()) == item


def test_no_match_is_not_observed_not_unavailable_or_negative():
    output = run({})
    receipt = output.evidence[0].metadata["outcome_observation_receipt"]
    assert receipt["source_available"] is True
    assert {x["status"] for x in receipt["observations"]} == {"not_observed"}
    assert output.evidence[0].evidence_type == "data_missing"  # legacy Gate semantics retained
    changed_prose = replace(output.evidence[0], observation="No abnormality exists.")
    assert changed_prose.metadata == output.evidence[0].metadata
    with pytest.raises(ModelValidationError):
        bad = output.evidence[0].to_dict()
        bad["metadata"]["outcome_observation_receipt"]["observations"][0]["status"] = (
            "observed_normal"
        )
        Evidence.from_dict(bad)


def test_explicit_passing_wat_remains_observed_normal():
    output = run(
        {
            "wat_result": [
                dict(
                    lot_id="LOT_A",
                    pass_fail="true",
                    fail_mode="",
                    parameter_name="metric",
                    tested_at="2026-01-01T01:00:00Z",
                )
            ]
        }
    )
    receipt = output.evidence[0].metadata["outcome_observation_receipt"]
    wat = next(x for x in receipt["observations"] if x["source_table"] == "wat_result")
    assert wat["status"] == "observed_normal"
    assert wat["normal_record_count"] == 1
    assert any(x.evidence_type == "negative_signal" for x in output.evidence)


def test_existing_parameter_summary_is_not_completed_discrimination():
    from test_patch4_mechanism_identity_contracts import _process_evidence
    from yield_rca_core.investigation_decision import assess_action_values

    item = _process_evidence()
    gap = dict(
        gap_id="G",
        gap_type="hypothesis_discrimination",
        competition_axis="mechanism",
        discriminator_kind="parameter_anomaly",
        can_change_ranking=True,
        expected_evidence_types=["parameter_deviation"],
        supports_candidate_ids=["A", "B"],
        weakens_candidate_ids=["A", "B"],
        target_scope=dict(operation="2500", equipment="EQ_A"),
    )
    value = assess_action_values(
        options=[dict(action_kind="inspect_fdc_spc", gap=gap)],
        action_records=[],
        gain_history=[],
        remaining_tool_budget=10,
        evidence=[item],
        competition_requirement="mechanism_required",
    )[0]
    assert not value.eligible  # rerunning a summary is still not useful
    assert value.discriminative_analysis_status == "not_established"
    assert value.discriminative_analysis_executability == "engineer_evidence_required"
    assert value.rejection_reason == "discriminative_analysis_not_executable"
    assert value.already_available_evidence_ids == (item.evidence_id,)


@pytest.mark.parametrize("status", ["not_acquired", "not_observed", "unavailable"])
def test_unknown_state_cannot_have_negative_observation_counts(status):
    from yield_rca_core.outcome_observation import OutcomeObservation

    with pytest.raises(ModelValidationError):
        OutcomeObservation("wat_result", "LOT", status, 1, 1)


def test_missing_quality_cannot_be_retyped_as_negative():
    evidence = run({}).evidence[0]
    with pytest.raises(ModelValidationError, match="requires normal rows"):
        replace(evidence, evidence_type="negative_signal", observation="All normal.")


def test_partial_cohort_coverage_does_not_make_unqueried_lots_negative():
    evidence = run({}, ("LOT_B",), "LANE_A").evidence
    synthesis = build_lane_first_evidence_synthesis(evidence, lanes())
    other = next(x for x in synthesis["active_causal_lanes"] if x["lane_id"] == "LANE_B")
    coverage = other["outcome_observation_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["not_acquired_lot_ids"] == ["LOT_C"]
    assert not other["facts"]["outcomes"]  # no cross-lane reassignment
    assert (
        synthesis["candidate_competition"]["outcome_observation_coverage_by_lane"]["LANE_B"][
            "status"
        ]
        == coverage["status"]
    )


def test_legacy_evidence_has_unknown_acquisition_not_fabricated_receipt():
    original = run({}).evidence[0]
    legacy = replace(original, metadata={})
    restored = Evidence.from_dict(legacy.to_dict())
    assert "outcome_observation_receipt" not in restored.metadata
    result = build_lane_first_evidence_synthesis([restored], lanes())
    assert all(
        x["outcome_observation_coverage"]["status"] == "unknown"
        for x in result["active_causal_lanes"]
    )


def test_bounded_projection_preserves_whole_spatial_records_and_reports_omissions():
    evidence = run({"defect_summary": [defect("X", "P", i=i) for i in range(1, 26)]}).evidence[0]
    projection = compact_evidence_prompt_card(evidence)["typed_details"]
    assert len(projection["defect_spatial_observations"]) == 3
    assert projection["defect_spatial_observations_count"] == 25
    assert projection["defect_spatial_observations_omitted"] == 22
    assert len(evidence.metadata["defect_spatial_observations"]) == 25


def test_full_cohort_coverage_not_inferred_from_truncated_lot_prefix():
    broad = lanes()[0]
    broad["exposed_lot_ids"] = [f"LOT_{i}" for i in range(20)]
    output = run({}, broad["exposed_lot_ids"][:16])
    lane = build_lane_first_evidence_synthesis(output.evidence, [broad])["active_causal_lanes"][0]
    coverage = lane["outcome_observation_coverage"]
    assert coverage["status"] == "partial"
    assert coverage["comparison_lot_count"] == 20
    assert coverage["missing_lot_count"] == 4
    assert coverage["observations_omitted"] > 0


def test_specialist_prose_cannot_overwrite_tool_observation_state():
    from test_specialist_v2 import ScriptedClient, _action
    from yield_rca_core.specialist_v2 import SpecialistV2Executor

    def misleading(request, _index):
        result = dict(request.payload["deterministic_specialist_analysis"])
        result["summary"] = "All outcomes are normal."
        result["engineering_interpretation"] = "No abnormality exists."
        return result

    executor = SpecialistV2Executor(
        llm_client=ScriptedClient(analysis=misleading),
        summarize_defect_wat_tool=SummarizeDefectWatTool(Repository()),
        llm_analysis_enabled=True,
    )
    finding = executor.execute(
        _action("inspect_defect_pattern", "defect_wat"),
        request_id="REQ",
        context={"source_lot_id": "LOT_A", "lot_ids": ["LOT_A"]},
    )
    assert finding.summary == "All outcomes are normal."  # interpretation remains untrusted
    assert all(item.evidence_type == "data_missing" for item in finding.evidence)
    assert {
        row["status"]
        for row in finding.evidence[0].metadata["outcome_observation_receipt"]["observations"]
    } == {"not_observed"}


def test_unavailable_source_receipt_cannot_claim_observed_negative():
    from yield_rca_core.outcome_observation import lane_outcome_coverage

    evidence = run({}, ("LOT_A",)).evidence[0].to_dict()
    receipt = evidence["metadata"]["outcome_observation_receipt"]
    receipt["source_available"] = False
    for row in receipt["observations"]:
        row["status"] = "unavailable"
    restored = Evidence.from_dict(evidence)
    assert lane_outcome_coverage([restored], ["LOT_A"])["status"] == "unavailable"
    receipt["observations"][0]["status"] = "not_observed"
    with pytest.raises(ModelValidationError):
        Evidence.from_dict(evidence)


def test_acquisition_receipt_cannot_drop_a_queried_domain():
    evidence = run({}).evidence[0].to_dict()
    evidence["metadata"]["outcome_observation_receipt"]["observations"].pop()
    with pytest.raises(ModelValidationError):
        Evidence.from_dict(evidence)


def test_action_analysis_status_round_trip_cannot_claim_completion():
    from test_patch4_mechanism_identity_contracts import _process_evidence
    from yield_rca_core.causal_investigation_models import ActionValueAssessment
    from yield_rca_core.investigation_decision import assess_action_values

    value = assess_action_values(
        options=[
            dict(
                action_kind="inspect_fdc_spc",
                gap=dict(
                    gap_type="hypothesis_discrimination",
                    discriminator_kind="parameter_anomaly",
                    competition_axis="mechanism",
                    target_scope=dict(equipment="EQ_A"),
                    expected_evidence_types=["parameter_deviation"],
                ),
            )
        ],
        action_records=[],
        gain_history=[],
        remaining_tool_budget=10,
        evidence=[_process_evidence()],
        competition_requirement="mechanism_required",
    )[0]
    assert ActionValueAssessment.from_dict(value.to_dict()) == value
    payload = value.to_dict()
    payload["discriminative_analysis_status"] = "performed"
    with pytest.raises(ModelValidationError):
        ActionValueAssessment.from_dict(payload)
    # Old assessments without new diagnostic fields still round-trip without backfill.
    payload.pop("discriminative_analysis_status")
    payload.pop("discriminative_analysis_executability")
    assert ActionValueAssessment.from_dict(payload).to_dict() == payload


def test_repeated_lot_query_keeps_original_evidence_identity():
    once = run({})
    repeated = run({}, ("LOT_A", "LOT_A"))
    assert repeated.evidence_ids == once.evidence_ids
    receipt = repeated.evidence[0].metadata["outcome_observation_receipt"]
    assert receipt["queried_lot_ids"] == ("LOT_A",)
    assert len(receipt["observations"]) == 3


def test_aggregate_normal_quality_keeps_separate_domain_coverage():
    output = run(
        {
            "wat_result": [
                dict(
                    lot_id="LOT_A",
                    pass_fail="true",
                    fail_mode="",
                    parameter_name="electrical",
                    tested_at="2026-01-01T01:00:00Z",
                )
            ],
            "metrology_result": [
                dict(
                    lot_id="LOT_B",
                    wafer_id="LOT_B_W01",
                    pass_fail="true",
                    measurement_stage="POST",
                    metric_name="thickness",
                    measured_value="10",
                    unit="nm",
                    measured_at="2026-01-01T01:00:00Z",
                )
            ],
        },
        ("LOT_A", "LOT_B"),
    )
    normal = next(e for e in output.evidence if e.evidence_id == "EV_QUALITY_NO_IMPACT")
    assert {e.entity_id for e in normal.entities} == {"LOT_A", "LOT_B"}
    observations = normal.metadata["outcome_observation_receipt"]["observations"]
    assert (
        next(
            o for o in observations if o["lot_id"] == "LOT_B" and o["source_table"] == "wat_result"
        )["status"]
        == "not_observed"
    )
    assert (
        next(
            o
            for o in observations
            if o["lot_id"] == "LOT_B" and o["source_table"] == "metrology_result"
        )["status"]
        == "observed_normal"
    )
