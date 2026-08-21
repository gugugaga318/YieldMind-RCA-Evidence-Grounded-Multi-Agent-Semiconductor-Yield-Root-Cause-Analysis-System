from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_adversarial import (  # noqa: E402
    QwenAdversarialChallenger,
    derive_alternative_search_status,
)
from yield_rca_core.causal_evidence_gap import (  # noqa: E402
    build_hypothesis_discrimination_gaps,
)
from yield_rca_core.causal_evidence_matrix import (  # noqa: E402
    CausalClaimResult,
    CausalEvidenceMatrix,
)
from yield_rca_core.causal_hypothesis import (  # noqa: E402
    CausalClaimStatus,
    CausalHypothesis,
)
from yield_rca_core.causal_investigation_models import (  # noqa: E402
    AlternativeSearchStatus,
    CandidateChallenge,
    CausalLaneRecord,
    ChallengeStatus,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMRequest,
    LLMResponse,
)


def matrix() -> CausalEvidenceMatrix:
    candidate = CausalHypothesis(
        root_cause="EQ_01 pressure excursion",
        causal_explanation="Pressure excursion explains the outcome.",
        supporting_evidence_ids=("EV_1",),
    )
    return CausalEvidenceMatrix(
        candidate=candidate,
        claims={
            "mechanism": CausalClaimResult(
                claim="mechanism",
                status=CausalClaimStatus.INCOMPLETE.value,
            )
        },
    )


class ChallengeClient(FakeLLMClient):
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        base = super().complete_json(
            LLMRequest(
                agent=request.agent,
                prompt_name="planner",
                prompt_version=request.prompt_version,
                payload={"fallback_plan": {}},
            )
        )
        return LLMResponse(data=self.response, usage=base.usage)


class SequentialChallengeClient(ChallengeClient):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        super().__init__(responses[0])
        self.responses = responses

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.response = self.responses[min(len(self.requests), len(self.responses) - 1)]
        return super().complete_json(request)


def test_single_candidate_does_not_imply_alternatives_eliminated() -> None:
    challenge = CandidateChallenge(
        candidate_id="C1",
        challenge_explanation="The strongest alternative remains unresolved.",
        status=ChallengeStatus.UNRESOLVED.value,
    )
    assert (
        derive_alternative_search_status(
            challenges=[challenge],
            matrices=[matrix()],
            active_lane_ids=["L1", "L2"],
        )
        == AlternativeSearchStatus.UNRESOLVED.value
    )


def test_discrimination_gap_is_created_even_for_one_candidate() -> None:
    gaps = build_hypothesis_discrimination_gaps([matrix()])
    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "hypothesis_discrimination"
    assert gaps[0]["priority"] == 1
    assert "inspect_fdc_spc" in gaps[0]["allowed_actions"]


def test_parameterized_alternative_lane_narrows_discriminator_to_fdc_then_refresh() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_OP_6000",
        operation="6000",
        equipment="EQ_PLATING",
        chamber="EQ_PLATING_CH02",
        recipe="RCP_01",
        parameter_scope=("filter_pressure", "organic_replenishment"),
    )
    offered_gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C1"],
    )
    parameter_gap = next(
        gap
        for gap in offered_gaps
        if gap["discriminator_kind"] == "parameter_anomaly"
    )
    challenge = CandidateChallenge(
        candidate_id="C1",
        strongest_alternative_lane_id="LANE_OP_6000",
        distinguishing_gap_ids=(parameter_gap["gap_id"],),
        challenge_explanation="Operation 6000 is the strongest alternative.",
        status=ChallengeStatus.ALTERNATIVE_IDENTIFIED.value,
    )

    gap = build_hypothesis_discrimination_gaps(
        [matrix()],
        candidate_challenges=[challenge],
        causal_lanes=[lane],
        candidate_ids=["C1"],
    )[0]

    assert gap["discriminator_kind"] == "parameter_anomaly"
    assert gap["preferred_action"] == "inspect_fdc_spc"
    assert gap["refresh_action"] == "run_rca_reasoning"
    assert gap["allowed_actions"] == ["inspect_fdc_spc", "run_rca_reasoning"]
    assert gap["required_evidence_groups"] == ["process_anomaly"]
    assert gap["target_scope"] == {
        "lane_id": "LANE_OP_6000",
        "operation": "6000",
        "equipment": "EQ_PLATING",
        "chamber": "EQ_PLATING_CH02",
        "recipe": "RCP_01",
        "discriminator_kind": "parameter_anomaly",
        "parameters": "filter_pressure,organic_replenishment",
    }


def test_typed_discriminators_derive_actions_from_runtime_lane_facts() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_RUNTIME_X91",
        operation="OP_X91",
        equipment="TOOL_X91",
        chamber="CH_X91",
        recipe="RCP_X91",
        parameter_scope=("gas_ratio",),
        exposed_lot_ids=("LOT_X91", "LOT_X92"),
        time_window=("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00"),
    )

    gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_RUNTIME"],
    )
    by_kind = {gap["discriminator_kind"]: gap for gap in gaps}

    assert set(by_kind) == {
        "parameter_anomaly",
        "exposure_commonality",
        "recipe_commonality",
        "product_outcome",
        "mechanism_context",
        "temporal_alignment",
    }
    assert by_kind["parameter_anomaly"]["preferred_action"] == "inspect_fdc_spc"
    assert by_kind["exposure_commonality"]["preferred_action"] == "find_shared_exposure"
    assert by_kind["recipe_commonality"]["preferred_action"] == "find_shared_exposure"
    assert (
        by_kind["product_outcome"]["preferred_action"]
        == "validate_shared_defect_pattern"
    )
    assert by_kind["mechanism_context"]["preferred_action"] == "validate_historical_case"
    assert by_kind["temporal_alignment"]["preferred_action"] == "inspect_fdc_spc"
    assert all(gap["lane_binding"] == "challenge_selected" for gap in gaps)
    assert all(gap["target_scope"] == {} for gap in gaps)


def test_source_only_lane_excludes_self_comparison_product_outcome() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_SOURCE_ONLY",
        operation="OP_SOURCE",
        equipment="TOOL_SOURCE",
        chamber="CH_SOURCE",
        recipe="RCP_SOURCE",
        parameter_scope=("pressure", "temperature", "flow"),
        exposed_lot_ids=("LOT_SOURCE",),
        time_window=(
            "2026-03-01T00:00:00+00:00",
            "2026-03-01T01:00:00+00:00",
        ),
    )

    gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_SOURCE"],
        source_lot_id="LOT_SOURCE",
    )
    by_kind = {gap["discriminator_kind"]: gap for gap in gaps}

    assert "product_outcome" not in by_kind
    assert gaps[0]["discriminator_kind"] == "parameter_anomaly"
    assert gaps[0]["information_gain"] > by_kind["temporal_alignment"][
        "information_gain"
    ]
    assert gaps[0]["applicable_lane_ids"] == [lane.lane_id]
    assert gaps[0]["information_gain_by_lane"][lane.lane_id] == gaps[0][
        "information_gain"
    ]


def test_product_outcome_requires_an_independent_comparison_lot() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_WITH_CONTROL",
        operation="OP_CONTROL",
        equipment="TOOL_CONTROL",
        chamber="CH_CONTROL",
        recipe="RCP_CONTROL",
        exposed_lot_ids=("LOT_SOURCE", "LOT_COMPARISON"),
    )

    gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_CONTROL"],
        source_lot_id="LOT_SOURCE",
    )
    product = next(
        gap for gap in gaps if gap["discriminator_kind"] == "product_outcome"
    )

    assert product["information_gain"] > 0
    assert product["applicable_lane_ids"] == [lane.lane_id]
    assert any(
        "1 independent comparison Lots" in item
        for item in product["information_gain_basis"]
    )


def test_temporal_discriminator_without_parameter_signal_uses_mes_exposure() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_TIME_ONLY",
        operation="OP_T7",
        equipment="TOOL_T7",
        chamber="CH_T7",
        recipe="RCP_T7",
        exposed_lot_ids=("LOT_T7",),
        time_window=("2026-02-01T00:00:00+00:00", "2026-02-01T01:00:00+00:00"),
    )

    gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_TIME"],
    )
    temporal = next(
        gap for gap in gaps if gap["discriminator_kind"] == "temporal_alignment"
    )

    assert temporal["required_evidence_groups"] == ["shared_exposure"]
    assert temporal["preferred_action"] == "find_shared_exposure"


def test_selected_typed_gap_isolated_from_other_discriminator_kinds() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_DYNAMIC_SELECTION",
        operation="OP_D1",
        equipment="TOOL_D1",
        chamber="CH_D1",
        recipe="RCP_D1",
        parameter_scope=("pressure_delta",),
        exposed_lot_ids=("LOT_D1",),
    )
    offered = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_DYNAMIC"],
    )
    product_gap = next(
        gap for gap in offered if gap["discriminator_kind"] == "product_outcome"
    )
    challenge = CandidateChallenge(
        candidate_id="C_DYNAMIC",
        strongest_alternative_lane_id=lane.lane_id,
        distinguishing_gap_ids=(product_gap["gap_id"],),
        challenge_explanation="The product outcome best separates the two explanations.",
        status=ChallengeStatus.ALTERNATIVE_IDENTIFIED.value,
    )

    selected = build_hypothesis_discrimination_gaps(
        [matrix()],
        candidate_challenges=[challenge],
        causal_lanes=[lane],
        candidate_ids=["C_DYNAMIC"],
    )

    assert len(selected) == 1
    assert selected[0]["gap_id"] == product_gap["gap_id"]
    assert selected[0]["preferred_action"] == "validate_shared_defect_pattern"


def test_challenge_accepts_python_gap_and_explanatory_question() -> None:
    client = ChallengeClient(
        {
            "challenges": [
                {
                    "candidate_id": "C1",
                    "strongest_alternative": "L2",
                    "supporting_evidence_ids": ["EV_1"],
                    "contradicting_evidence_ids": [],
                    "unexplained_precursor_evidence_ids": [],
                    "distinguishing_gap_ids": ["G1"],
                    "distinguishing_questions": ["Does L2 show the same excursion?"],
                    "challenge_explanation": "L2 is the strongest competing Lane.",
                    "status": "alternative_identified",
                }
            ],
            "analysis_summary": "Challenge complete.",
        }
    )
    result = QwenAdversarialChallenger(client).generate(
        request_id="REQ_252",
        candidates=[{"candidate_id": "C1", "root_cause": "EQ_01"}],
        matrices=[matrix()],
        evidence_gaps=[
            {
                "gap_id": "G1",
                "gap_type": "hypothesis_discrimination",
                "candidate_id": "C1",
                "target_scope": {"lane_id": "L2"},
            }
        ],
        evidence_ids=["EV_1"],
        lane_ids=["L1", "L2"],
        active_lane_ids=["L1", "L2"],
    )
    assert not result.output_invalid
    assert result.alternative_search_status == AlternativeSearchStatus.ALTERNATIVE_FOUND.value
    assert result.challenges[0].strongest_alternative_lane_id == "L2"
    assert result.challenges[0].distinguishing_questions == (
        "Does L2 show the same excursion?",
    )


def test_challenge_retries_multiple_typed_gaps_and_accepts_single_repair() -> None:
    challenge = {
        "candidate_id": "C1",
        "strongest_alternative_lane_id": "L2",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "unexplained_precursor_evidence_ids": [],
        "distinguishing_gap_ids": ["G_PARAMETER", "G_OUTCOME"],
        "distinguishing_questions": ["Which observation best separates L2?"],
        "challenge_explanation": "L2 remains the strongest alternative.",
        "status": "alternative_identified",
    }
    repaired = dict(challenge)
    repaired["distinguishing_gap_ids"] = ["G_PARAMETER"]
    client = SequentialChallengeClient(
        [
            {"challenges": [challenge], "analysis_summary": "Two gaps selected."},
            {"challenges": [repaired], "analysis_summary": "One gap selected."},
        ]
    )
    gaps = [
        {
            "gap_id": gap_id,
            "gap_type": "hypothesis_discrimination",
            "candidate_id": "C1",
            "target_scope": {"lane_id": "L2"},
        }
        for gap_id in ("G_PARAMETER", "G_OUTCOME")
    ]

    result = QwenAdversarialChallenger(client).generate(
        request_id="REQ_REPAIR_MULTIPLE_GAPS",
        candidates=[{"candidate_id": "C1", "root_cause": "candidate"}],
        matrices=[matrix()],
        evidence_gaps=gaps,
        evidence_ids=[],
        lane_ids=["L1", "L2"],
        active_lane_ids=["L1", "L2"],
    )

    assert not result.output_invalid
    assert len(client.requests) == 2
    assert result.challenges[0].distinguishing_gap_ids == ("G_PARAMETER",)
    feedback = client.requests[1].payload["previous_validation_feedback"]
    assert feedback["must_repair_before_resubmission"] is True
    assert "exactly one" in feedback["message"]
    assert feedback["allowed_gap_ids"] == ["G_OUTCOME", "G_PARAMETER"]


def test_challenge_repairs_lower_information_gain_gap_selection() -> None:
    lane = CausalLaneRecord(
        lane_id="LANE_GAIN",
        operation="OP_GAIN",
        equipment="TOOL_GAIN",
        chamber="CH_GAIN",
        recipe="RCP_GAIN",
        parameter_scope=("pressure", "temperature", "flow"),
        exposed_lot_ids=("LOT_SOURCE", "LOT_CONTROL"),
        time_window=(
            "2026-04-01T00:00:00+00:00",
            "2026-04-01T01:00:00+00:00",
        ),
    )
    gaps = build_hypothesis_discrimination_gaps(
        [matrix()],
        causal_lanes=[lane],
        candidate_ids=["C_GAIN"],
        source_lot_id="LOT_SOURCE",
    )
    by_kind = {gap["discriminator_kind"]: gap for gap in gaps}
    assert (
        by_kind["parameter_anomaly"]["information_gain"]
        > by_kind["product_outcome"]["information_gain"]
    )

    def response(gap_id: str) -> dict[str, object]:
        return {
            "challenges": [
                {
                    "candidate_id": "C_GAIN",
                    "strongest_alternative_lane_id": lane.lane_id,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "unexplained_precursor_evidence_ids": [],
                    "distinguishing_gap_ids": [gap_id],
                    "distinguishing_questions": [
                        "Which observation best distinguishes the Lane?"
                    ],
                    "challenge_explanation": "The Lane remains unresolved.",
                    "status": "alternative_identified",
                }
            ],
            "analysis_summary": "Select one distinguishing observation.",
        }

    client = SequentialChallengeClient(
        [
            response(by_kind["product_outcome"]["gap_id"]),
            response(by_kind["parameter_anomaly"]["gap_id"]),
        ]
    )
    result = QwenAdversarialChallenger(client).generate(
        request_id="REQ_GAIN",
        candidates=[{"candidate_id": "C_GAIN", "root_cause": "candidate"}],
        matrices=[matrix()],
        evidence_gaps=gaps,
        evidence_ids=[],
        lane_ids=[lane.lane_id],
        active_lane_ids=[lane.lane_id],
    )

    assert not result.output_invalid
    assert len(client.requests) == 2
    assert result.challenges[0].distinguishing_gap_ids == (
        by_kind["parameter_anomaly"]["gap_id"],
    )
    assert "highest-information-gain" in client.requests[1].payload[
        "previous_validation_feedback"
    ]["message"]


def test_challenge_rejects_typed_gap_owned_by_another_lane() -> None:
    client = ChallengeClient(
        {
            "challenges": [
                {
                    "candidate_id": "C1",
                    "strongest_alternative_lane_id": "L2",
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "unexplained_precursor_evidence_ids": [],
                    "distinguishing_gap_ids": ["G_L1"],
                    "distinguishing_questions": ["Which Lane has the excursion?"],
                    "challenge_explanation": "L2 remains unresolved.",
                    "status": "alternative_identified",
                }
            ],
            "analysis_summary": "Challenge complete.",
        }
    )

    result = QwenAdversarialChallenger(client).generate(
        request_id="REQ_WRONG_LANE",
        candidates=[{"candidate_id": "C1", "root_cause": "candidate"}],
        matrices=[matrix()],
        evidence_gaps=[
            {
                "gap_id": "G_L1",
                "gap_type": "hypothesis_discrimination",
                "candidate_id": "C1",
                "target_scope": {"lane_id": "L1"},
            }
        ],
        evidence_ids=[],
        lane_ids=["L1", "L2"],
        active_lane_ids=["L1", "L2"],
    )

    assert result.output_invalid
    assert any("different causal Lane" in error for error in result.validation_errors)


def test_unknown_gap_is_rejected_without_orchestration_fallback() -> None:
    client = ChallengeClient(
        {
            "challenges": [
                {
                    "candidate_id": "C1",
                    "strongest_alternative_lane_id": None,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                    "unexplained_precursor_evidence_ids": [],
                    "distinguishing_gap_ids": ["QWEN_INVENTED_GAP"],
                    "challenge_explanation": "Invented gap.",
                    "status": "open",
                }
            ],
            "analysis_summary": "Invalid challenge.",
        }
    )
    result = QwenAdversarialChallenger(client).generate(
        request_id="REQ_252_INVALID",
        candidates=[{"candidate_id": "C1"}],
        matrices=[matrix()],
        evidence_gaps=[{"gap_id": "G1"}],
        evidence_ids=["EV_1"],
        lane_ids=["L1"],
    )
    assert result.output_invalid
    assert result.alternative_search_status == AlternativeSearchStatus.UNRESOLVED.value
    assert len(client.requests) == 2


def test_resolved_challenge_with_all_lanes_eliminated_closes_competition() -> None:
    challenge = CandidateChallenge(
        candidate_id="C1",
        challenge_explanation="The only alternative Lane was eliminated.",
        status=ChallengeStatus.RESOLVED.value,
    )
    assert (
        derive_alternative_search_status(
            challenges=[challenge],
            matrices=[matrix()],
            active_lane_ids=["L1"],
            eliminated_lane_ids=["L1"],
        )
        == AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value
    )
