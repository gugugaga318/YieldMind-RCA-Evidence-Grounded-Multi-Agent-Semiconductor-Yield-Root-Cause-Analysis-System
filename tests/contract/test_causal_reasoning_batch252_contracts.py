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
        evidence_gaps=[{"gap_id": "G1"}],
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
