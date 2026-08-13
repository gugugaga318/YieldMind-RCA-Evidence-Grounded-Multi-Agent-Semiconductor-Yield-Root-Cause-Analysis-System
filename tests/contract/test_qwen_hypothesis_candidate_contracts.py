from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evidence_models import (  # noqa: E402
    EVIDENCE_SCHEMA_VERSION,
    EntityType,
    Evidence,
    EvidenceEntity,
    EvidenceSourceType,
    EvidenceType,
)
from yield_rca_core.hypothesis_candidate_generator import (  # noqa: E402
    QwenHypothesisCandidateGenerator,
)
from yield_rca_core.hypothesis_engine import HypothesisEngine  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMOutputValidationError,
    LLMRequest,
    LLMResponse,
)
from yield_rca_core.models import AgentFinding, AgentKind  # noqa: E402
from yield_rca_core.rca_reasoning_agent import RCAReasoningAgent  # noqa: E402


def typed_evidence(
    *,
    evidence_id: str,
    evidence_type: str,
    agent: str,
    entity_type: str,
    entity_id: str,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type={
            AgentKind.MES.value: EvidenceSourceType.ANALYTICS.value,
            AgentKind.FDC.value: EvidenceSourceType.FDC.value,
            AgentKind.DEFECT_WAT.value: EvidenceSourceType.DEFECT.value,
        }[agent],
        source_id=f"SOURCE_{evidence_id}",
        summary=f"Observation for {evidence_id}",
        evidence_type=evidence_type,
        source_agent=agent,
        source_tool=f"{agent}_tool",
        observation=f"Measured observation for {evidence_id}",
        entities=[
            EvidenceEntity(entity_type=EntityType.LOT.value, entity_id="LOT_01"),
            EvidenceEntity(entity_type=entity_type, entity_id=entity_id),
        ],
        confidence=0.95,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


def causal_findings() -> list[AgentFinding]:
    evidence = [
        typed_evidence(
            evidence_id="EV_EXPOSURE",
            evidence_type=EvidenceType.IMPACT_SCOPE.value,
            agent=AgentKind.MES.value,
            entity_type=EntityType.EQUIPMENT.value,
            entity_id="EQ_01",
        ),
        typed_evidence(
            evidence_id="EV_PROCESS",
            evidence_type=EvidenceType.PARAMETER_DEVIATION.value,
            agent=AgentKind.FDC.value,
            entity_type=EntityType.PARAMETER.value,
            entity_id="chamber_temperature_range",
        ),
        typed_evidence(
            evidence_id="EV_PRODUCT",
            evidence_type=EvidenceType.DEFECT_SIGNAL.value,
            agent=AgentKind.DEFECT_WAT.value,
            entity_type=EntityType.DEFECT.value,
            entity_id="edge_void",
        ),
    ]
    return [
        AgentFinding(
            finding_id=f"FINDING_{item.source_agent}",
            agent=str(item.source_agent),
            summary=item.summary,
            confidence=0.9,
            evidence_ids=[item.evidence_id],
            evidence=[item],
        )
        for item in evidence
    ]


def proposal(*, supporting: list[str] | None = None) -> dict[str, object]:
    return {
        "root_cause": "EQ_01 chamber temperature control drift",
        "causal_explanation": (
            "A shared EQ_01 exposure with chamber temperature deviation can "
            "produce the observed edge void signature."
        ),
        "supporting_evidence_ids": supporting
        or ["EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"],
        "contradicting_evidence_ids": [],
    }


class CandidateClient(FakeLLMClient):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        base = super().complete_json(request)
        response = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        return LLMResponse(data=dict(response), usage=base.usage)


class QwenHypothesisCandidateContractTest(unittest.TestCase):
    def test_generator_retries_unknown_evidence_and_accepts_repaired_candidate(
        self,
    ) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [proposal(supporting=["EV_UNKNOWN"])],
                    "analysis_summary": "First attempt uses an unknown ID.",
                },
                {
                    "candidates": [proposal()],
                    "analysis_summary": "Repaired evidence-bounded proposal.",
                },
            ]
        )

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_CANDIDATE",
            findings=causal_findings(),
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(result.validation_errors), 1)
        self.assertIn("unknown Evidence", result.validation_errors[0])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            client.requests[1].payload["previous_validation_feedback"]["message"],
            result.validation_errors[0],
        )

    def test_generator_rejects_negative_evidence_as_causal_support(self) -> None:
        findings = causal_findings()
        negative = typed_evidence(
            evidence_id="EV_NORMAL",
            evidence_type=EvidenceType.NEGATIVE_SIGNAL.value,
            agent=AgentKind.FDC.value,
            entity_type=EntityType.PARAMETER.value,
            entity_id="normal_pressure",
        )
        findings[1] = AgentFinding(
            finding_id="FINDING_FDC_WITH_NORMAL",
            agent=AgentKind.FDC.value,
            summary="FDC contains process and normal control Evidence.",
            confidence=0.9,
            evidence_ids=["EV_PROCESS", "EV_NORMAL"],
            evidence=[findings[1].evidence[0], negative],
        )
        client = CandidateClient(
            [
                {
                    "candidates": [proposal(supporting=["EV_NORMAL"])],
                    "analysis_summary": "Invalid use of a normal control.",
                }
            ]
        )

        with self.assertRaisesRegex(
            LLMOutputValidationError,
            "non-supporting Evidence",
        ):
            QwenHypothesisCandidateGenerator(client).generate(
                request_id="REQ_NEGATIVE",
                findings=findings,
            )

        self.assertEqual(len(client.requests), 2)

    def test_generator_retries_a_candidate_missing_shared_exposure_lane(self) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [
                        proposal(supporting=["EV_PROCESS", "EV_PRODUCT"])
                    ],
                    "analysis_summary": "The first proposal omitted MES exposure.",
                },
                {
                    "candidates": [proposal()],
                    "analysis_summary": "The repaired proposal joins all lanes.",
                },
            ]
        )

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_MISSING_EXPOSURE",
            findings=causal_findings(),
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertIn("shared_exposure", result.validation_errors[0])
        retry_feedback = client.requests[1].payload[
            "previous_validation_feedback"
        ]
        self.assertEqual(
            retry_feedback["missing_causal_lanes"],
            ["shared_exposure"],
        )
        self.assertEqual(
            retry_feedback["eligible_supporting_evidence_ids_by_lane"][
                "shared_exposure"
            ],
            ["EV_EXPOSURE"],
        )
        self.assertEqual(
            retry_feedback["source_agent_by_evidence_id"]["EV_EXPOSURE"],
            AgentKind.MES.value,
        )
        self.assertEqual(
            result.candidates[0].supporting_evidence_ids,
            ("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

    def test_generator_accepts_bounded_empty_answer_after_incomplete_candidate(
        self,
    ) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [
                        proposal(supporting=["EV_PROCESS", "EV_PRODUCT"])
                    ],
                    "analysis_summary": "The first proposal omitted MES exposure.",
                },
                {
                    "candidates": [],
                    "analysis_summary": (
                        "No complete three-lane causal mechanism is justified."
                    ),
                },
            ]
        )

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_BOUNDED_EMPTY",
            findings=causal_findings(),
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.candidates, ())
        self.assertEqual(len(result.validation_errors), 1)
        self.assertEqual(
            client.requests[1].payload["previous_validation_feedback"][
                "valid_empty_output"
            ]["candidates"],
            [],
        )

    def test_python_gate_can_support_a_three_lane_llm_candidate(self) -> None:
        result = HypothesisEngine().analyze(
            request_id="REQ_ENGINE",
            findings=causal_findings(),
            mode="active",
            external_candidates=[proposal()],
        )

        selected = result["candidates"][0]
        self.assertEqual(selected["basis"], "llm_evidence_composition")
        self.assertTrue(selected["llm_gate_passed"])
        self.assertEqual(
            set(selected["causal_lanes"]),
            {"shared_exposure", "process_anomaly", "product_outcome"},
        )
        self.assertEqual(result["decision_gate"]["status"], "supported")
        self.assertEqual(
            result["decision_gate"]["root_cause"],
            "EQ_01 chamber temperature control drift",
        )

    def test_python_gate_rejects_a_candidate_missing_product_lane(self) -> None:
        result = HypothesisEngine().analyze(
            request_id="REQ_MISSING_LANE",
            findings=causal_findings(),
            mode="active",
            external_candidates=[
                proposal(supporting=["EV_EXPOSURE", "EV_PROCESS"])
            ],
        )

        selected = result["candidates"][0]
        self.assertFalse(selected["llm_gate_passed"])
        self.assertEqual(result["decision_gate"]["status"], "inconclusive")
        self.assertTrue(
            any("three" in reason for reason in selected["rejection_reasons"])
        )

    def test_unrelated_normal_parameter_is_not_a_causal_contradiction(self) -> None:
        findings = causal_findings()
        unrelated_normal = typed_evidence(
            evidence_id="EV_UNRELATED_NORMAL",
            evidence_type=EvidenceType.NEGATIVE_SIGNAL.value,
            agent=AgentKind.FDC.value,
            entity_type=EntityType.PARAMETER.value,
            entity_id="unrelated_gas_flow",
        )
        findings[1] = AgentFinding(
            finding_id="FINDING_FDC_WITH_UNRELATED_CONTROL",
            agent=AgentKind.FDC.value,
            summary="FDC includes the anomaly and an unrelated normal control.",
            confidence=0.9,
            evidence_ids=["EV_PROCESS", "EV_UNRELATED_NORMAL"],
            evidence=[findings[1].evidence[0], unrelated_normal],
        )

        result = HypothesisEngine().analyze(
            request_id="REQ_UNRELATED_NORMAL",
            findings=findings,
            mode="active",
            external_candidates=[proposal()],
        )

        selected = result["candidates"][0]
        self.assertTrue(selected["llm_gate_passed"])
        self.assertNotIn(
            "EV_UNRELATED_NORMAL",
            selected["contradicting_evidence_ids"],
        )
        self.assertEqual(result["decision_gate"]["status"], "supported")

    def test_rca_agent_uses_qwen_proposal_but_python_gate_owns_result(self) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [proposal()],
                    "analysis_summary": "One evidence-bounded mechanism.",
                }
            ]
        )

        result = RCAReasoningAgent(
            llm_client=client,
            agent_mode="llm",
        ).analyze(
            request_id="REQ_RCA_AGENT",
            findings=causal_findings(),
        )

        generation = result.details["hypothesis_candidate_generation"]
        self.assertEqual(generation["source"], "qwen")
        self.assertEqual(generation["candidate_count"], 1)
        self.assertEqual(result.details["status"], "supported")
        self.assertEqual(
            result.details["hypothesis_engine_result"]["candidates"][0]["basis"],
            "llm_evidence_composition",
        )
        self.assertEqual(len(client.requests), 1)

    def test_rca_agent_falls_back_safely_after_invalid_qwen_candidates(self) -> None:
        invalid = {
            "candidates": [proposal(supporting=["EV_UNKNOWN"])],
            "analysis_summary": "Invalid unknown Evidence.",
        }
        client = CandidateClient([invalid])

        result = RCAReasoningAgent(
            llm_client=client,
            agent_mode="llm",
        ).analyze(
            request_id="REQ_RCA_FALLBACK",
            findings=causal_findings(),
        )

        generation = result.details["hypothesis_candidate_generation"]
        self.assertEqual(generation["source"], "deterministic_fallback")
        self.assertEqual(
            generation["fallback_reason"],
            "qwen_hypothesis_candidate_generation_failed",
        )
        self.assertEqual(result.details["status"], "inconclusive")
        self.assertIn(
            "WARN_RCA_LLM_CANDIDATE_FALLBACK",
            {warning.warning_id for warning in result.warnings},
        )


if __name__ == "__main__":
    unittest.main()
