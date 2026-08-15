"""Hypothesis Engine RCA reasoning.

Batch 19 removes the retired Legacy scoring engine.  Historical RCA snapshots
remain readable through the domain DTO compatibility contracts; new work always
uses the deterministic, evidence-bounded Hypothesis Engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yield_rca_core.causal_adversarial import (
    QwenAdversarialChallenger,
)
from yield_rca_core.causal_candidate_comparison import (
    QwenHypothesisCandidateComparator,
)
from yield_rca_core.causal_confirmation import evaluate_impact_lot_gate
from yield_rca_core.causal_evidence_gap import (
    build_causal_evidence_gaps,
    build_hypothesis_discrimination_gaps,
)
from yield_rca_core.causal_evidence_matrix import build_causal_evidence_matrix
from yield_rca_core.causal_hypothesis import CausalHypothesis
from yield_rca_core.causal_investigation_models import (
    AlternativeSearchStatus,
    CandidateChallenge,
)
from yield_rca_core.evidence_synthesis import build_evidence_synthesis
from yield_rca_core.hypothesis_candidate_generator import (
    QwenHypothesisCandidateGenerator,
)
from yield_rca_core.hypothesis_engine import HypothesisEngine
from yield_rca_core.llm_gateway import (
    LLMCallError,
    LLMClient,
    LLMOutputValidationError,
)
from yield_rca_core.models import (
    AgentFinding,
    AgentKind,
    AgentMode,
    FindingKind,
    Hypothesis,
    HypothesisStatus,
    ModelValidationError,
    Warning,
)

SPECIALIST_AGENTS = frozenset(
    {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
        AgentKind.KNOWLEDGE.value,
    }
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _lane_context(findings: list[AgentFinding]) -> tuple[list[str], list[str], list[str]]:
    """Return all, active, and Python-eliminated concrete Lane IDs."""

    raw_lanes: list[dict[str, Any]] = []
    for finding in findings:
        if finding.agent != AgentKind.MES.value:
            continue
        raw = finding.details.get("lane_candidates", [])
        if isinstance(raw, list):
            raw_lanes.extend(item for item in raw if isinstance(item, dict))
    ordered = sorted(
        {
            str(item.get("lane_id", "")).strip(): item
            for item in raw_lanes
            if str(item.get("lane_id", "")).strip()
        }.values(),
        key=lambda item: (
            -float(item.get("priority_score", 0.0)),
            str(item.get("lane_id", "")),
        ),
    )
    all_ids = [str(item["lane_id"]) for item in ordered]
    active_ids = [str(item["lane_id"]) for item in ordered[:3]]
    eliminated_ids = [
        str(item["lane_id"])
        for item in ordered
        if str(item.get("investigation_status", "")) in {"eliminated", "blocked"}
    ]
    return all_ids, active_ids, eliminated_ids


def _merge_evidence_payload(findings: list[AgentFinding]) -> list[dict[str, Any]]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for finding in findings:
        for item in finding.details.get("evidence", []):
            evidence_by_id[str(item["evidence_id"])] = dict(item)
    return list(evidence_by_id.values())


def _merge_warnings(findings: list[AgentFinding]) -> list[Warning]:
    return list(
        {
            warning.warning_id: warning
            for finding in findings
            for warning in finding.warnings
        }.values()
    )


def _recommended_actions(findings: list[AgentFinding]) -> list[dict[str, Any]]:
    discovery = next(
        (
            finding
            for finding in findings
            if finding.finding_kind == FindingKind.KNOWLEDGE_DISCOVERY.value
        ),
        None,
    )
    if discovery is None:
        return []
    top_case = discovery.details.get("top_case", {})
    solution = str(top_case.get("solution", "")).strip()
    if not solution:
        return []
    evidence_ids = list(discovery.evidence_ids)
    return [
        {"action": action.strip(), "evidence_ids": evidence_ids}
        for action in solution.split(",")
        if action.strip()
    ]


def _unsupported_source_warning(
    findings: list[AgentFinding],
    *,
    supported: bool,
    ranked_candidates: list[dict[str, Any]],
) -> Warning | None:
    """Make an evidence gap explicit without letting Knowledge fill it.

    A typed missing-FDC observation is already a hard signal that a required
    source is unavailable.  A second, narrower boundary covers an unresolved
    slurry/material hypothesis when every current-Lot slurry trace is normal:
    the deployment has no material-batch genealogy capability, so historical
    similarity cannot silently stand in for that missing source.
    """

    missing_ids = _unique(
        [
            evidence.evidence_id
            for finding in findings
            for evidence in finding.evidence
            if evidence.evidence_type == "data_missing"
            and evidence.source_type in {"fdc", "wat", "mes", "analytics"}
            and (
                not supported
                or evidence.evidence_id == "EV_FDC_FEATURE_DATA_MISSING"
            )
        ]
    )
    if missing_ids:
        return Warning(
            warning_id="WARN_UNSUPPORTED_DATA_SOURCE",
            message=(
                "A required operational data source is unavailable; the RCA remains "
                "bounded by typed DATA_MISSING Evidence."
            ),
            evidence_ids=missing_ids,
        )
    if supported:
        return None

    fdc = next((item for item in findings if item.agent == AgentKind.FDC.value), None)
    if fdc is None:
        return None
    parameters = {
        str(item.get("parameter_name", "")).casefold(): abs(
            float(item.get("avg_delta_percent", 0.0))
        )
        for item in fdc.details.get("parameter_summary", [])
        if isinstance(item, dict)
    }
    material_terms = ("slurry", "chemical", "material", "consumable")
    normal_material_parameters = {
        name
        for name, delta in parameters.items()
        if any(term in name for term in material_terms) and delta < 5.0
    }
    abnormal_material_parameters = {
        name
        for name, delta in parameters.items()
        if any(term in name for term in material_terms) and delta >= 5.0
    }
    candidate_needs_material_trace = any(
        any(term in str(item.get("root_cause", "")).casefold() for term in material_terms)
        for item in ranked_candidates
    )
    if (
        candidate_needs_material_trace
        and normal_material_parameters
        and not abnormal_material_parameters
    ):
        evidence_ids = _unique(
            [
                evidence.evidence_id
                for evidence in fdc.evidence
                if evidence.source_field
                and str(evidence.source_field).casefold() in normal_material_parameters
            ]
        )
        return Warning(
            warning_id="WARN_UNSUPPORTED_DATA_SOURCE",
            message=(
                "Material or chemical batch genealogy is not configured. Current "
                "equipment traces are insufficient to confirm the remaining "
                "material-related hypothesis."
            ),
            evidence_ids=evidence_ids,
        )
    return None


@dataclass(frozen=True)
class RCAReasoningAgent:
    """Generate optional Qwen candidates, then use the Python Evidence Gate."""

    hypothesis_engine: HypothesisEngine = HypothesisEngine()
    llm_client: LLMClient | None = None
    agent_mode: str = AgentMode.DETERMINISTIC.value
    prompt_version: str = "v1"

    def analyze(self, *, request_id: str, findings: list[AgentFinding]) -> AgentFinding:
        if not findings:
            raise ModelValidationError("RCA reasoning requires Specialist findings")
        unsupported = {finding.agent for finding in findings} - SPECIALIST_AGENTS
        if unsupported:
            raise ModelValidationError(
                f"RCA reasoning only accepts Specialist findings, got {sorted(unsupported)}"
            )
        evidence_ids = _unique(
            [evidence_id for finding in findings for evidence_id in finding.evidence_ids]
        )
        if not evidence_ids:
            raise ModelValidationError("Specialist findings must reference evidence_ids")

        warnings = _merge_warnings(findings)
        present_agents = {finding.agent for finding in findings}
        missing_agents = sorted(SPECIALIST_AGENTS - present_agents)
        if missing_agents:
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_MISSING_FINDINGS",
                    message=f"Missing Specialist findings: {', '.join(missing_agents)}.",
                )
            )

        candidate_generation: dict[str, Any] = {
            "source": "deterministic_only",
            "candidate_count": 0,
            "attempt_count": 0,
            "validation_errors": [],
            "fallback_reason": None,
            "candidate_output_invalid": False,
        }
        external_candidates: list[dict[str, Any]] = []
        deterministic_candidates_enabled = self.agent_mode != AgentMode.LLM.value
        candidate_comparison: dict[str, Any] | None = None
        candidate_challenges: list[CandidateChallenge] = []
        alternative_search_status = AlternativeSearchStatus.NOT_SEARCHED.value
        challenge_generation: dict[str, Any] = {
            "source": "not_requested",
            "attempt_count": 0,
            "validation_errors": [],
            "output_invalid": False,
        }
        if self.agent_mode == AgentMode.LLM.value and self.llm_client is not None:
            try:
                generated = QwenHypothesisCandidateGenerator(
                    self.llm_client,
                    prompt_version=self.prompt_version,
                ).generate(
                    request_id=request_id,
                    findings=findings,
                )
                external_candidates = [
                    candidate.to_dict() for candidate in generated.candidates
                ]
                candidate_generation = {
                    "source": "qwen",
                    "candidate_count": len(external_candidates),
                    "attempt_count": generated.attempt_count,
                    "validation_errors": list(generated.validation_errors),
                    "fallback_reason": None,
                    "candidate_output_invalid": generated.candidate_output_invalid,
                }
                if generated.candidate_output_invalid:
                    candidate_generation["fallback_reason"] = "qwen_candidate_output_invalid"
                    warnings.append(
                        Warning(
                            warning_id="WARN_RCA_QWEN_CANDIDATE_INVALID",
                            message=(
                                "Qwen returned no valid causal candidate after the "
                                "bounded validation attempts."
                            ),
                            evidence_ids=evidence_ids,
                        )
                    )
                if external_candidates:
                    evidence_by_id = {
                        evidence.evidence_id: evidence
                        for finding in findings
                        for evidence in finding.evidence
                        if evidence.is_typed
                    }
                    matrices = []
                    for candidate in external_candidates:
                        matrices.append(
                            build_causal_evidence_matrix(
                                CausalHypothesis(
                                    root_cause=str(candidate["root_cause"]),
                                    causal_explanation=str(candidate["causal_explanation"]),
                                    supporting_evidence_ids=tuple(
                                        candidate["supporting_evidence_ids"]
                                    ),
                                    contradicting_evidence_ids=tuple(
                                        candidate["contradicting_evidence_ids"]
                                    ),
                                ),
                                evidence_by_id.values(),
                            )
                        )
                    gaps = build_causal_evidence_gaps(matrices)
                    gaps.extend(build_hypothesis_discrimination_gaps(matrices))
                    challenge_candidates = [
                        {
                            **candidate,
                            "candidate_id": f"{request_id}:llm:{index + 1}",
                        }
                        for index, candidate in enumerate(external_candidates)
                    ]
                    if len(external_candidates) >= 2:
                        try:
                            candidate_comparison = QwenHypothesisCandidateComparator(
                                self.llm_client,
                                prompt_version=self.prompt_version,
                            ).compare(
                                request_id=request_id,
                                candidates=challenge_candidates,
                                matrices=matrices,
                                evidence_gaps=gaps,
                            )
                        except (LLMCallError, LLMOutputValidationError) as exc:
                            candidate_comparison = {
                                "source": "python",
                                "comparison_error": str(exc),
                            }
                    all_lane_ids, active_lane_ids, eliminated_lane_ids = _lane_context(
                        findings
                    )
                    if not all_lane_ids:
                        # Pre-Batch-25 snapshots may not contain concrete Lane
                        # records. There is no alternative Lane to investigate,
                        # so preserve the legacy contract without making an
                        # extra model call.
                        candidate_challenges = [
                            CandidateChallenge(
                                candidate_id=str(candidate["candidate_id"]),
                                supporting_evidence_ids=tuple(
                                    str(item)
                                    for item in candidate.get(
                                        "supporting_evidence_ids", []
                                    )
                                ),
                                challenge_explanation=(
                                    "Legacy evidence snapshot has no concrete Lane "
                                    "inventory; no additional Lane can be compared."
                                ),
                                status="resolved",
                            )
                            for candidate in challenge_candidates
                        ]
                        alternative_search_status = (
                            AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value
                        )
                        challenge_generation = {
                            "source": "python_legacy_compatibility",
                            "attempt_count": 0,
                            "challenge_count": len(candidate_challenges),
                            "validation_errors": [],
                            "output_invalid": False,
                            "alternative_search_status": alternative_search_status,
                        }
                    else:
                        challenge_result = QwenAdversarialChallenger(
                            self.llm_client,
                            prompt_version=self.prompt_version,
                        ).generate(
                            request_id=request_id,
                            candidates=challenge_candidates,
                            matrices=matrices,
                            evidence_gaps=gaps,
                            evidence_ids=list(evidence_by_id),
                            evidence_by_id=evidence_by_id,
                            lane_ids=all_lane_ids,
                            active_lane_ids=active_lane_ids,
                            eliminated_lane_ids=eliminated_lane_ids,
                        )
                        candidate_challenges = list(challenge_result.challenges)
                        alternative_search_status = (
                            challenge_result.alternative_search_status
                        )
                        challenge_generation = {
                            "source": "qwen",
                            "attempt_count": challenge_result.attempt_count,
                            "challenge_count": len(candidate_challenges),
                            "validation_errors": list(
                                challenge_result.validation_errors
                            ),
                            "output_invalid": challenge_result.output_invalid,
                            "alternative_search_status": alternative_search_status,
                        }
                    if challenge_generation.get("output_invalid"):
                        warnings.append(
                            Warning(
                                warning_id="WARN_RCA_QWEN_CHALLENGE_INVALID",
                                message=(
                                    "Qwen adversarial challenge output was invalid; "
                                    "the candidate remains unconfirmed."
                                ),
                                evidence_ids=evidence_ids,
                            )
                        )
            except (LLMCallError, LLMOutputValidationError) as exc:
                candidate_generation = {
                    "source": "qwen",
                    "candidate_count": 0,
                    "attempt_count": (
                        2 if isinstance(exc, LLMOutputValidationError) else 1
                    ),
                    "validation_errors": [str(exc)],
                    "fallback_reason": (
                        "qwen_candidate_output_invalid"
                        if isinstance(exc, LLMOutputValidationError)
                        else "qwen_candidate_provider_failed"
                    ),
                    "candidate_output_invalid": True,
                }
                warnings.append(
                    Warning(
                        warning_id=(
                            "WARN_RCA_QWEN_CANDIDATE_INVALID"
                            if isinstance(exc, LLMOutputValidationError)
                            else "WARN_RCA_LLM_CANDIDATE_FALLBACK"
                        ),
                        message=(
                            "Qwen hypothesis candidate output was invalid; the "
                            "workflow returned an inconclusive result without "
                            "inventing a deterministic replacement candidate."
                            if isinstance(exc, LLMOutputValidationError)
                            else "Qwen hypothesis candidate generation failed; the "
                            "deterministic Evidence Gate continued without model "
                            "candidates."
                        ),
                        evidence_ids=evidence_ids,
                    )
                )
        if candidate_generation.get("candidate_output_invalid"):
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_CANDIDATE_VALIDATION_BOUNDARY",
                    message=(
                        "At least one Qwen candidate was rejected by the Python "
                        "candidate contract or no valid Qwen candidate remained."
                    ),
                    evidence_ids=evidence_ids,
                )
            )

        engine_result = self.hypothesis_engine.analyze(
            request_id=request_id,
            findings=findings,
            mode="active",
            external_candidates=external_candidates,
            include_deterministic_candidates=deterministic_candidates_enabled,
            candidate_comparison=candidate_comparison,
            # Every active Qwen result uses the strict Confirmation Gate.
            # Legacy deterministic/controlled snapshots retain the non-strict
            # compatibility path, but missing Qwen temporal Evidence is a real
            # gap rather than permission to relax the gate.
            strict_confirmation=self.agent_mode == AgentMode.LLM.value,
            alternative_search_status=alternative_search_status,
            candidate_challenges=candidate_challenges,
        )
        decision = engine_result["decision_gate"]
        root_cause = str(decision["root_cause"])
        status = str(decision["status"])
        confidence = float(decision["confidence"])
        supported = status == HypothesisStatus.SUPPORTED.value
        if (
            self.agent_mode == AgentMode.LLM.value
            and external_candidates
            and alternative_search_status
            != AlternativeSearchStatus.ALTERNATIVES_ELIMINATED.value
        ):
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_ALTERNATIVE_SEARCH_INCOMPLETE",
                    message=(
                        "The Qwen candidate has not completed a Python-validated "
                        "adversarial alternative search; confirmation is blocked."
                    ),
                    evidence_ids=evidence_ids,
                )
            )
        active_candidate = next(
            (
                candidate
                for candidate in engine_result["candidates"]
                if candidate["root_cause"] == root_cause
            ),
            None,
        )
        root_cause_evidence_ids = (
            list(active_candidate["evidence_ids"]) if active_candidate is not None else []
        ) or list(evidence_ids)
        rationale = (
            "The Hypothesis Engine decision gate accepted the highest-ranked "
            "evidence-bounded hypothesis."
            if supported
            else "The Hypothesis Engine decision gate is inconclusive."
        )
        actions = _recommended_actions(findings) if supported else []
        if supported and not actions:
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_NO_ACTION_EVIDENCE",
                    message="No evidence-backed recommended actions are available.",
                    evidence_ids=root_cause_evidence_ids,
                )
            )
        if bool(decision.get("conflicting_physics", False)):
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_CONFLICTING_EVIDENCE",
                    message=(
                        "Independent operational Evidence conflicts with the candidate "
                        "mechanism, so the root cause cannot be confirmed."
                    ),
                    evidence_ids=root_cause_evidence_ids,
                )
            )
        if not supported:
            warnings.append(
                Warning(
                    warning_id="WARN_RCA_INCONCLUSIVE",
                    message=rationale,
                    evidence_ids=root_cause_evidence_ids,
                )
            )

        hypothesis = Hypothesis(
            hypothesis_id=f"{request_id}:hypothesis",
            root_cause=root_cause,
            confidence=confidence,
            evidence_ids=root_cause_evidence_ids,
            status=status,
            rationale=rationale,
            supporting_evidence_ids=(
                list(active_candidate["supporting_evidence_ids"])
                if active_candidate is not None
                else []
            ),
            contradicting_evidence_ids=(
                list(active_candidate["contradicting_evidence_ids"])
                if active_candidate is not None
                else []
            ),
            neutral_evidence_ids=(
                list(active_candidate["neutral_evidence_ids"])
                if active_candidate is not None
                else []
            ),
            validation_results=(
                [dict(item) for item in active_candidate["validation_results"]]
                if active_candidate is not None
                else []
            ),
            rank=(int(active_candidate["rank"]) if active_candidate is not None else None),
            rejection_reasons=(
                list(active_candidate["rejection_reasons"])
                if active_candidate is not None
                else []
            ),
        )
        ranked_candidates = [
            {
                "root_cause": candidate["root_cause"],
                "score": candidate["confidence"],
                "basis": candidate["basis"],
                "status": candidate["status"],
                "evidence_ids": list(candidate["evidence_ids"]),
                "supporting_evidence_ids": list(candidate["supporting_evidence_ids"]),
                "contradicting_evidence_ids": list(
                    candidate["contradicting_evidence_ids"]
                ),
                "rejection_reasons": list(candidate["rejection_reasons"]),
                "causal_matrix_status": candidate.get("causal_matrix_status"),
                "causal_chain_completeness": candidate.get(
                    "causal_evidence_matrix", {}
                ).get("causal_chain_completeness"),
                "data_missing_evidence_ids": list(
                    candidate.get("causal_evidence_matrix", {}).get(
                        "data_missing_evidence_ids", []
                    )
                ),
                "mechanism_support_source": candidate.get("mechanism_support_source"),
                "causal_evidence_matrix": dict(
                    candidate.get("causal_evidence_matrix", {})
                ),
            }
            for candidate in engine_result["candidates"]
        ]
        unsupported_source = _unsupported_source_warning(
            findings,
            supported=supported,
            ranked_candidates=ranked_candidates,
        )
        if unsupported_source is not None:
            warnings.append(unsupported_source)
        selected_candidate = active_candidate or (
            engine_result["candidates"][0] if engine_result["candidates"] else None
        )
        source_lot_id = next(
            (
                str(finding.details.get("source_lot_id"))
                for finding in findings
                if finding.details.get("source_lot_id")
            ),
            None,
        )
        observed_impact_lots = next(
            (
                [str(item) for item in finding.details.get("impact_lots", [])]
                for finding in findings
                if finding.details.get("impact_lots") is not None
            ),
            [],
        )
        impact_lot_gate = (
            evaluate_impact_lot_gate(
                source_lot_id=source_lot_id,
                candidate=selected_candidate,
                evidence=[item for finding in findings for item in finding.evidence],
                observed_impact_lots=observed_impact_lots,
            )
            if selected_candidate is not None
            else {
                "source_lot_id": source_lot_id,
                "candidate_root_cause": None,
                "confirmed_impact_lots": [],
                "rows": [],
            }
        )
        if not supported and isinstance(impact_lot_gate, dict):
            # Candidate impact scope remains available for audit, but only an
            # authoritative supported RCA may publish confirmed impact Lots.
            impact_lot_gate = {
                **impact_lot_gate,
                "confirmed_impact_lots": [],
                "confirmation_blocked_reason": (
                    "authoritative RCA conclusion is not supported"
                ),
            }
        return AgentFinding(
            finding_id=f"{request_id}:rca",
            agent=AgentKind.RCA_REASONING.value,
            summary=(
                f"Root cause: {root_cause} (confidence {confidence:.0%})."
                if supported
                else f"RCA result is inconclusive (confidence {confidence:.0%})."
            ),
            confidence=confidence,
            evidence_ids=evidence_ids,
            details={
                "root_cause": root_cause,
                "root_cause_evidence_ids": root_cause_evidence_ids,
                "status": status,
                "hypothesis": hypothesis.to_dict(),
                "evidence_chain": [
                    {
                        "stage": finding.agent,
                        "claim": finding.summary,
                        "confidence": finding.confidence,
                        "evidence_ids": list(finding.evidence_ids),
                    }
                    for finding in findings
                ],
                "recommended_actions": actions,
                "ranked_candidates": ranked_candidates,
                "reasoning_engine": "hypothesis_v1",
                "hypothesis_candidate_generation": candidate_generation,
                "adversarial_challenge_generation": challenge_generation,
                "candidate_challenges": [
                    challenge.to_dict() for challenge in candidate_challenges
                ],
                "alternative_search_status": alternative_search_status,
                "hypothesis_engine_result": engine_result,
                "conclusion_status": str(decision.get("conclusion_status", status)),
                "causal_chain_completeness": decision.get(
                    "causal_chain_completeness"
                ),
                "data_missing_evidence_ids": list(
                    decision.get("data_missing_evidence_ids", [])
                ),
                "evidence_synthesis": engine_result.get(
                    "evidence_synthesis",
                    build_evidence_synthesis(
                        item for finding in findings for item in finding.evidence
                    ),
                ),
                "causal_evidence_gaps": list(
                    engine_result.get("causal_evidence_gaps", [])
                ),
                "candidate_comparison": dict(
                    engine_result.get("candidate_comparison", candidate_comparison or {})
                ),
                "confirmation_gate": dict(
                    decision.get("confirmation_gate", {})
                ),
                "impact_lot_gate": impact_lot_gate,
                "evidence": _merge_evidence_payload(findings),
            },
            warnings=list({warning.warning_id: warning for warning in warnings}.values()),
        )
