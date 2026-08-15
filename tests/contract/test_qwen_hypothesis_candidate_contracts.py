from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_evidence_matrix import (  # noqa: E402
    build_causal_evidence_matrix,
)
from yield_rca_core.causal_hypothesis import CausalClaimStatus, CausalHypothesis  # noqa: E402
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
    LLMCallError,
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
    metadata: dict[str, object] | None = None,
    timestamp: str | None = None,
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
        metadata=metadata or {},
        timestamp=timestamp,
        confidence=0.95,
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
    )


def approved_knowledge_evidence() -> Evidence:
    return Evidence(
        evidence_id="EV_KNOWLEDGE_MECHANISM",
        source_type=EvidenceSourceType.KNOWLEDGE.value,
        source_id="CASE_01",
        summary="Confirmed engineering mechanism for temperature drift and edge void.",
        evidence_type=EvidenceType.HISTORICAL_CASE_MATCH.value,
        source_agent=AgentKind.KNOWLEDGE.value,
        source_tool="retrieve_similar_case",
        observation="Confirmed case documents temperature drift causing edge void.",
        entities=[
            EvidenceEntity(
                entity_type=EntityType.KNOWLEDGE_ASSET.value,
                entity_id="CASE_01",
                attributes={"validation_status": "CONFIRMED"},
            )
        ],
        metadata={"validation_status": "CONFIRMED"},
        confidence=0.9,
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
            metadata={
                "processing_window": {
                    "start": "2026-01-01T00:00:00",
                    "end": "2026-01-01T01:00:00",
                }
            },
            timestamp="2026-01-01T00:30:00",
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


class CandidateProviderFailureClient(FakeLLMClient):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        raise LLMCallError(
            "candidate provider unavailable",
            status_code=503,
            failure_category="provider_http_error",
        )


class QwenHypothesisCandidateContractTest(unittest.TestCase):
    def test_approved_knowledge_can_support_mechanism_without_becoming_a_causal_lane(
        self,
    ) -> None:
        findings = causal_findings()
        knowledge = approved_knowledge_evidence()
        findings.append(
            AgentFinding(
                finding_id="FINDING_KNOWLEDGE",
                agent=AgentKind.KNOWLEDGE.value,
                summary=knowledge.summary,
                confidence=0.9,
                evidence_ids=[knowledge.evidence_id],
                evidence=[knowledge],
            )
        )
        candidate = proposal(
            supporting=["EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT", knowledge.evidence_id]
        )
        client = CandidateClient(
            [
                {
                    "candidates": [candidate],
                    "analysis_summary": "Current evidence plus approved mechanism.",
                }
            ]
        )

        generated = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_APPROVED_KNOWLEDGE",
            findings=findings,
        )

        self.assertEqual(len(generated.candidates), 1)
        result = HypothesisEngine().analyze(
            request_id="REQ_APPROVED_KNOWLEDGE",
            findings=findings,
            mode="active",
            external_candidates=[candidate],
        )
        selected = result["candidates"][0]
        self.assertEqual(selected["mechanism_support_source"], "approved_knowledge")
        self.assertEqual(set(selected["causal_lanes"]), {
            "shared_exposure",
            "process_anomaly",
            "product_outcome",
        })

    def test_unapproved_knowledge_is_not_accepted_as_mechanism_support(self) -> None:
        knowledge = approved_knowledge_evidence()
        unapproved = Evidence.from_dict(
            {
                **knowledge.to_dict(),
                "metadata": {"validation_status": "DRAFT"},
                "entities": [
                    {
                        "entity_type": EntityType.KNOWLEDGE_ASSET.value,
                        "entity_id": "CASE_01",
                        "attributes": {"validation_status": "DRAFT"},
                    }
                ],
            }
        )
        findings = causal_findings()
        findings.append(
            AgentFinding(
                finding_id="FINDING_KNOWLEDGE_DRAFT",
                agent=AgentKind.KNOWLEDGE.value,
                summary=unapproved.summary,
                confidence=0.9,
                evidence_ids=[unapproved.evidence_id],
                evidence=[unapproved],
            )
        )
        candidate = proposal(
            supporting=["EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT", unapproved.evidence_id]
        )
        client = CandidateClient(
            [
                {
                    "candidates": [candidate],
                    "analysis_summary": "Draft knowledge is not causal proof.",
                }
            ]
        )

        generated = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_DRAFT_KNOWLEDGE",
            findings=findings,
        )

        self.assertEqual(generated.candidates, ())
        self.assertTrue(generated.candidate_output_invalid)

    def test_parameter_direction_mismatch_is_a_matrix_conflict_and_facts_are_exposed(
        self,
    ) -> None:
        findings = causal_findings()
        process = typed_evidence(
            evidence_id="EV_PROCESS",
            evidence_type=EvidenceType.PARAMETER_DEVIATION.value,
            agent=AgentKind.FDC.value,
            entity_type=EntityType.PARAMETER.value,
            entity_id="chamber_temperature_range",
            metadata={
                "direction": "high",
                "magnitude": 2.4,
                "processing_window": {
                    "start": "2026-01-01T00:00:00",
                    "end": "2026-01-01T01:00:00",
                },
            },
            timestamp="2026-01-01T00:30:00",
        )
        findings[1] = AgentFinding(
            finding_id="FINDING_FDC_DIRECTION",
            agent=AgentKind.FDC.value,
            summary=process.summary,
            confidence=0.9,
            evidence_ids=[process.evidence_id],
            evidence=[process],
        )
        candidate = CausalHypothesis(
            root_cause="EQ_01 chamber temperature low control drift",
            causal_explanation="Low temperature deviation produces the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )
        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in findings for item in finding.evidence],
        )

        self.assertEqual(
            matrix.claims["parameter"].status,
            CausalClaimStatus.CONFLICTED.value,
        )
        self.assertEqual(
            matrix.claims["parameter"].facts["parameter_directions"][0]["directions"],
            ["high"],
        )
        self.assertEqual(
            matrix.claims["parameter"].facts["parameter_magnitudes"][0]["magnitudes"],
            [2.4],
        )
        self.assertEqual(
            matrix.claims["parameter"].facts["processing_windows"][0]["windows"][0]["start"],
            "2026-01-01T00:00:00",
        )
    def test_invalid_candidate_isolated_when_another_candidate_is_valid(self) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [
                        proposal(supporting=["EV_UNKNOWN"]),
                        proposal(),
                    ],
                    "analysis_summary": "One invalid and one valid candidate.",
                }
            ]
        )

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_CANDIDATE_ISOLATION",
            findings=causal_findings(),
        )

        self.assertFalse(result.candidate_output_invalid)
        self.assertEqual(len(result.candidates), 1)
        self.assertTrue(any("unknown Evidence" in item for item in result.validation_errors))
        self.assertEqual(len(client.requests), 1)

    def test_matrix_rejects_mismatched_equipment_claim(self) -> None:
        candidate = CausalHypothesis(
            root_cause="EQ_99 chamber temperature control drift",
            causal_explanation="EQ_99 temperature deviation produces the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in causal_findings() for item in finding.evidence],
        )

        self.assertEqual(
            matrix.claims["equipment"].status,
            CausalClaimStatus.CONFLICTED.value,
        )
        self.assertTrue(matrix.has_critical_conflict)

    def test_entity_prefix_does_not_turn_eq_010_into_eq_01_support(self) -> None:
        candidate = CausalHypothesis(
            root_cause="EQ_010 chamber temperature control drift",
            causal_explanation="EQ_010 temperature deviation produces edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in causal_findings() for item in finding.evidence],
        )

        self.assertEqual(matrix.claims["equipment"].status, "conflicted")

    def test_matrix_marks_mechanism_source_as_empirical_convergence(self) -> None:
        candidate = CausalHypothesis(
            root_cause="EQ_01 chamber temperature control drift",
            causal_explanation="Temperature deviation produces the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in causal_findings() for item in finding.evidence],
        )

        self.assertEqual(matrix.mechanism_status, CausalClaimStatus.SUPPORTED.value)
        self.assertEqual(matrix.mechanism_support_source, "empirical_convergence")

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

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_NEGATIVE",
            findings=findings,
        )

        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result.candidates, ())
        self.assertTrue(result.candidate_output_invalid)
        self.assertTrue(any("non-supporting Evidence" in item for item in result.validation_errors))

    def test_generator_accepts_candidate_missing_shared_exposure_for_matrix_gap(self) -> None:
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

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.validation_errors, ())
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(
            result.candidates[0].supporting_evidence_ids,
            ("EV_PROCESS", "EV_PRODUCT"),
        )

    def test_generator_does_not_replace_an_incomplete_candidate_with_empty_answer(
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

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(client.requests), 1)

    def test_near_duplicate_second_candidate_is_isolated(self) -> None:
        second = proposal()
        second["root_cause"] = "Temperature drift failure at EQ_01 chamber"
        client = CandidateClient(
            [
                {
                    "candidates": [proposal(), second],
                    "analysis_summary": "Two paraphrases of one mechanism.",
                }
            ]
        )

        result = QwenHypothesisCandidateGenerator(client).generate(
            request_id="REQ_NEAR_DUPLICATE",
            findings=causal_findings(),
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertTrue(any("near-duplicate" in item for item in result.validation_errors))

    def test_unstated_operation_is_incomplete_not_conflicted(self) -> None:
        findings = causal_findings()
        exposure = Evidence.from_dict(
            {
                **findings[0].evidence[0].to_dict(),
                "entities": [
                    EvidenceEntity(EntityType.LOT.value, "LOT_01").to_dict(),
                    EvidenceEntity(EntityType.EQUIPMENT.value, "EQ_01").to_dict(),
                    EvidenceEntity(EntityType.OPERATION.value, "4000").to_dict(),
                ],
            }
        )
        candidate = CausalHypothesis(
            root_cause="EQ_01 chamber temperature control drift",
            causal_explanation="Temperature drift produces the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [exposure, findings[1].evidence[0], findings[2].evidence[0]],
        )

        self.assertEqual(matrix.claims["operation"].status, "incomplete")

    def test_equipment_tokens_do_not_create_parameter_support(self) -> None:
        candidate = CausalHypothesis(
            root_cause="EQ_01 chamber malfunction",
            causal_explanation="The chamber can produce the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in causal_findings() for item in finding.evidence],
        )

        self.assertEqual(matrix.claims["parameter"].status, "incomplete")

    def test_explicit_wrong_parameter_is_conflicted(self) -> None:
        candidate = CausalHypothesis(
            root_cause="EQ_01 chamber pressure drift",
            causal_explanation="High pressure produces the observed edge void.",
            supporting_evidence_ids=("EV_EXPOSURE", "EV_PROCESS", "EV_PRODUCT"),
        )

        matrix = build_causal_evidence_matrix(
            candidate,
            [item for finding in causal_findings() for item in finding.evidence],
        )

        self.assertEqual(matrix.claims["parameter"].status, "conflicted")

    def test_timestamps_without_a_window_are_temporally_incomplete(self) -> None:
        findings = causal_findings()
        timed = [
            Evidence.from_dict(
                {
                    **item.to_dict(),
                    "timestamp": "2026-01-01T00:30:00",
                    "metadata": {},
                }
            )
            for finding in findings
            for item in finding.evidence
        ]
        matrix = build_causal_evidence_matrix(
            CausalHypothesis.from_mapping(proposal()),
            timed,
        )

        self.assertEqual(matrix.claims["temporal"].status, "incomplete")

    def test_unrelated_approved_knowledge_does_not_support_mechanism(self) -> None:
        unrelated = Evidence.from_dict(
            {
                **approved_knowledge_evidence().to_dict(),
                "evidence_id": "EV_UNRELATED_KNOWLEDGE",
                "summary": "Confirmed slurry flow mechanism for scratch defects.",
                "observation": "Low slurry flow can cause wafer scratches.",
            }
        )
        candidate = CausalHypothesis(
            root_cause="EQ_01 temperature drift",
            causal_explanation="Temperature drift may produce edge voids.",
            supporting_evidence_ids=("EV_UNRELATED_KNOWLEDGE",),
        )

        matrix = build_causal_evidence_matrix(candidate, [unrelated])

        self.assertEqual(matrix.claims["mechanism"].status, "incomplete")
        self.assertEqual(matrix.claims["mechanism"].support_source, "llm_explanation_only")

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

    def test_python_gate_outranks_qwen_preference_for_a_conflicted_candidate(
        self,
    ) -> None:
        supported = proposal()
        conflicted = {
            **proposal(),
            "root_cause": "EQ_02 chamber temperature control drift",
        }

        result = HypothesisEngine().analyze(
            request_id="REQ_GATE_BEFORE_QWEN_PREFERENCE",
            findings=causal_findings(),
            mode="active",
            external_candidates=[supported, conflicted],
            include_deterministic_candidates=False,
            candidate_comparison={
                "preferred_candidate_index": 1,
                "comparison_summary": "Qwen preferred the conflicted candidate.",
            },
            strict_confirmation=True,
        )

        self.assertEqual(
            result["candidates"][0]["root_cause"],
            supported["root_cause"],
        )
        self.assertTrue(result["candidates"][0]["decision_gate_passed"])
        self.assertFalse(result["candidates"][1]["decision_gate_passed"])
        self.assertEqual(result["decision_gate"]["status"], "supported")
        self.assertEqual(
            result["decision_gate"]["root_cause"],
            supported["root_cause"],
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
        self.assertEqual(generation["source"], "qwen")
        self.assertEqual(
            generation["fallback_reason"],
            "qwen_candidate_output_invalid",
        )
        self.assertTrue(generation["candidate_output_invalid"])
        self.assertEqual(result.details["status"], "inconclusive")
        self.assertIn(
            "WARN_RCA_QWEN_CANDIDATE_INVALID",
            {warning.warning_id for warning in result.warnings},
        )
        self.assertEqual(result.details["hypothesis_engine_result"]["candidates"], [])

    def test_valid_empty_qwen_candidates_do_not_enable_deterministic_root_cause(
        self,
    ) -> None:
        client = CandidateClient(
            [
                {
                    "candidates": [],
                    "analysis_summary": "No evidence-bounded causal candidate exists.",
                }
            ]
        )

        result = RCAReasoningAgent(
            llm_client=client,
            agent_mode="llm",
        ).analyze(
            request_id="REQ_RCA_EMPTY",
            findings=causal_findings(),
        )

        generation = result.details["hypothesis_candidate_generation"]
        self.assertFalse(generation["candidate_output_invalid"])
        self.assertEqual(generation["candidate_count"], 0)
        self.assertEqual(result.details["status"], "inconclusive")
        self.assertEqual(result.details["hypothesis_engine_result"]["candidates"], [])

    def test_candidate_provider_failure_does_not_invent_a_deterministic_candidate(
        self,
    ) -> None:
        result = RCAReasoningAgent(
            llm_client=CandidateProviderFailureClient(),
            agent_mode="llm",
        ).analyze(
            request_id="REQ_RCA_PROVIDER_FAILURE",
            findings=causal_findings(),
        )

        generation = result.details["hypothesis_candidate_generation"]
        self.assertEqual(
            generation["fallback_reason"],
            "qwen_candidate_provider_failed",
        )
        self.assertEqual(generation["candidate_count"], 0)
        self.assertEqual(result.details["status"], "inconclusive")
        self.assertEqual(result.details["hypothesis_engine_result"]["candidates"], [])
        self.assertIn(
            "WARN_RCA_LLM_CANDIDATE_FALLBACK",
            {warning.warning_id for warning in result.warnings},
        )


if __name__ == "__main__":
    unittest.main()
