"""Qwen proposes novel causal hypotheses; Python owns every acceptance gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yield_rca_core.evidence_models import Evidence, EvidenceType
from yield_rca_core.llm_gateway import (
    LLMClient,
    LLMOutputValidationError,
    LLMRequest,
)
from yield_rca_core.models import AgentFinding, AgentKind, ModelValidationError

_OUTPUT_ATTEMPTS = 2
_MAX_CANDIDATES = 2
_MAX_ENTITIES_PER_EVIDENCE = 12
_NON_SUPPORTING_TYPES = {
    EvidenceType.DATA_MISSING.value,
    EvidenceType.NEGATIVE_SIGNAL.value,
    EvidenceType.SOP_GUIDANCE.value,
}
_KNOWLEDGE_MECHANISM_TYPES = {
    EvidenceType.HISTORICAL_CASE_MATCH.value,
    EvidenceType.ENGINEERING_NOTE.value,
}
_EXPOSURE_TYPES = {
    EvidenceType.LOT_CONTEXT.value,
    EvidenceType.PROCESS_EXPOSURE.value,
    EvidenceType.EQUIPMENT_EXPOSURE.value,
    EvidenceType.IMPACT_SCOPE.value,
    EvidenceType.EXCURSION_WINDOW.value,
}
_PROCESS_TYPES = {
    EvidenceType.RECIPE_CHANGE.value,
    EvidenceType.HOLD_EVENT.value,
    EvidenceType.PARAMETER_DEVIATION.value,
    EvidenceType.TREND_DEVIATION.value,
    EvidenceType.OOC_EVENT.value,
    EvidenceType.SPC_VIOLATION.value,
}
_PRODUCT_TYPES = {
    EvidenceType.DEFECT_SIGNAL.value,
    EvidenceType.METROLOGY_DEVIATION.value,
    EvidenceType.ELECTRICAL_FAILURE.value,
}


@dataclass(frozen=True)
class HypothesisCandidateProposal:
    """A model-authored explanation with IDs bound to immutable Evidence."""

    root_cause: str
    causal_explanation: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.root_cause, str) or not self.root_cause.strip():
            raise ModelValidationError("candidate root_cause must be non-empty")
        if self.root_cause.strip().casefold() == "inconclusive":
            raise ModelValidationError("inconclusive is not a hypothesis candidate")
        if (
            not isinstance(self.causal_explanation, str)
            or not self.causal_explanation.strip()
        ):
            raise ModelValidationError(
                "candidate causal_explanation must be non-empty"
            )
        for field_name, values in (
            ("supporting_evidence_ids", self.supporting_evidence_ids),
            ("contradicting_evidence_ids", self.contradicting_evidence_ids),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ModelValidationError(
                    f"candidate {field_name} must contain non-empty strings"
                )
            if len(values) != len(set(values)):
                raise ModelValidationError(
                    f"candidate {field_name} must not contain duplicates"
                )
        if not self.supporting_evidence_ids:
            raise ModelValidationError(
                "candidate supporting_evidence_ids must not be empty"
            )
        if set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids):
            raise ModelValidationError(
                "candidate Evidence cannot be both supporting and contradicting"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_cause": self.root_cause,
            "causal_explanation": self.causal_explanation,
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "contradicting_evidence_ids": list(self.contradicting_evidence_ids),
        }


@dataclass(frozen=True)
class HypothesisCandidateGeneration:
    candidates: tuple[HypothesisCandidateProposal, ...]
    attempt_count: int
    validation_errors: tuple[str, ...] = ()
    candidate_output_invalid: bool = False


def _evidence_register(findings: list[AgentFinding]) -> list[dict[str, Any]]:
    evidence_by_id = {
        evidence.evidence_id: evidence
        for finding in findings
        for evidence in finding.evidence
        if evidence.is_typed
    }
    register: list[dict[str, Any]] = []
    for evidence in evidence_by_id.values():
        serialized = evidence.to_dict()
        register.append(
            {
                "evidence_id": evidence.evidence_id,
                "source_type": evidence.source_type,
                "evidence_type": evidence.evidence_type,
                "source_field": evidence.source_field,
                "timestamp": evidence.timestamp,
                "observation": evidence.observation,
                "confidence": evidence.confidence,
                "source_agent": evidence.source_agent,
                "source_tool": evidence.source_tool,
                "metadata": serialized.get("metadata", {}),
                "entities": serialized.get("entities", [])[:_MAX_ENTITIES_PER_EVIDENCE],
            }
        )
    return register


def _eligible_evidence_ids_by_lane(
    evidence_by_id: dict[str, Evidence],
) -> dict[str, list[str]]:
    """Expose typed repair choices without claiming causal relevance for Qwen."""

    lane_types = {
        "shared_exposure": _EXPOSURE_TYPES,
        "process_anomaly": _PROCESS_TYPES,
        "product_outcome": _PRODUCT_TYPES,
    }
    result = {
        lane: sorted(
            evidence_id
            for evidence_id, evidence in evidence_by_id.items()
            if evidence.evidence_type in evidence_types
            and evidence.evidence_type not in _NON_SUPPORTING_TYPES
        )
        for lane, evidence_types in lane_types.items()
    }
    result["mechanism_support"] = sorted(
        evidence_id
        for evidence_id, evidence in evidence_by_id.items()
        if _is_approved_knowledge_support(evidence)
    )
    return result


def _is_approved_knowledge_support(evidence: Evidence) -> bool:
    """Knowledge may support mechanism only after explicit approval."""

    if (
        evidence.source_type != "knowledge"
        or evidence.evidence_type not in _KNOWLEDGE_MECHANISM_TYPES
    ):
        return False
    statuses = [
        str(value).upper()
        for key, value in evidence.metadata.items()
        if str(key).casefold() == "validation_status"
    ]
    statuses.extend(
        str(value).upper()
        for entity in evidence.entities
        for key, value in entity.attributes.items()
        if str(key).casefold() == "validation_status"
    )
    return bool(statuses) and all(status == "CONFIRMED" for status in statuses)


def _candidate_repair_feedback(
    validation_error: str,
    *,
    evidence_by_id: dict[str, Evidence],
) -> dict[str, Any]:
    eligible_by_lane = _eligible_evidence_ids_by_lane(evidence_by_id)
    missing_lanes = [
        lane for lane in eligible_by_lane if lane in validation_error
    ]
    return {
        "message": validation_error,
        "missing_causal_lanes": missing_lanes,
        "eligible_supporting_evidence_ids_by_lane": eligible_by_lane,
        "source_agent_by_evidence_id": {
            evidence_id: evidence.source_agent
            for evidence_id, evidence in sorted(evidence_by_id.items())
            if evidence.source_agent is not None
        },
        "repair_instruction": (
            "Repair every proposed candidate using only causally relevant IDs "
            "from eligible_supporting_evidence_ids_by_lane and preserve at "
            "least three independent Specialist source agents. Do not attach an "
            "irrelevant Evidence ID merely to satisfy the schema. If no complete "
            "three-lane causal chain is justified, return candidates=[] with a "
            "non-empty analysis_summary; that is a valid evidence-bounded answer."
        ),
        "valid_empty_output": {
            "candidates": [],
            "analysis_summary": (
                "No candidate is justified by a complete three-lane causal chain."
            ),
        },
    }


def _parse_candidate(
    payload: object,
    *,
    index: int,
    evidence_by_id: dict[str, Evidence],
) -> HypothesisCandidateProposal:
    if not isinstance(payload, dict):
        raise LLMOutputValidationError(f"candidates[{index}] must be an object")
    expected = {
        "root_cause",
        "causal_explanation",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
    }
    if set(payload) != expected:
        raise LLMOutputValidationError(
            f"candidates[{index}] must contain exactly {sorted(expected)}"
        )
    if not isinstance(payload.get("root_cause"), str) or not isinstance(
        payload.get("causal_explanation"), str
    ):
        raise LLMOutputValidationError(
            f"candidates[{index}] root_cause and causal_explanation must be strings"
        )
    supporting = payload.get("supporting_evidence_ids")
    contradicting = payload.get("contradicting_evidence_ids")
    if not isinstance(supporting, list) or not isinstance(contradicting, list):
        raise LLMOutputValidationError(
            f"candidates[{index}] Evidence IDs must be arrays"
        )
    try:
        proposal = HypothesisCandidateProposal(
            root_cause=payload["root_cause"].strip(),
            causal_explanation=payload["causal_explanation"].strip(),
            supporting_evidence_ids=tuple(supporting),
            contradicting_evidence_ids=tuple(contradicting),
        )
    except ModelValidationError as exc:
        raise LLMOutputValidationError(str(exc)) from exc
    referenced = set(proposal.supporting_evidence_ids) | set(
        proposal.contradicting_evidence_ids
    )
    unknown = sorted(referenced - set(evidence_by_id))
    if unknown:
        raise LLMOutputValidationError(
            f"candidates[{index}] references unknown Evidence IDs: {unknown}"
        )
    invalid_support = sorted(
        evidence_id
        for evidence_id in proposal.supporting_evidence_ids
        if (
            evidence_by_id[evidence_id].evidence_type in _NON_SUPPORTING_TYPES
            or (
                evidence_by_id[evidence_id].evidence_type
                in _KNOWLEDGE_MECHANISM_TYPES
                and not _is_approved_knowledge_support(evidence_by_id[evidence_id])
            )
        )
    )
    if invalid_support:
        raise LLMOutputValidationError(
            f"candidates[{index}] uses non-supporting Evidence as support: "
            f"{invalid_support}"
        )
    supporting_evidence = [
        evidence_by_id[evidence_id]
        for evidence_id in proposal.supporting_evidence_ids
    ]
    causal_lanes = {
        "shared_exposure": [
            item.evidence_id
            for item in supporting_evidence
            if item.evidence_type in _EXPOSURE_TYPES
        ],
        "process_anomaly": [
            item.evidence_id
            for item in supporting_evidence
            if item.evidence_type in _PROCESS_TYPES
        ],
        "product_outcome": [
            item.evidence_id
            for item in supporting_evidence
            if item.evidence_type in _PRODUCT_TYPES
        ],
    }
    missing_lanes = [lane for lane, evidence_ids in causal_lanes.items() if not evidence_ids]
    if missing_lanes:
        raise LLMOutputValidationError(
            f"candidates[{index}] is missing required causal Evidence lanes "
            f"{missing_lanes}; cite a supporting Evidence ID for every lane"
        )
    source_agents = {
        str(item.source_agent)
        for item in supporting_evidence
        if item.source_agent is not None and item.source_type != "knowledge"
    }
    if len(source_agents) < 3:
        raise LLMOutputValidationError(
            f"candidates[{index}] must cite supporting Evidence from at least "
            "three independent Specialist agents; current agents are "
            f"{sorted(source_agents)}"
        )
    return proposal


@dataclass(frozen=True)
class QwenHypothesisCandidateGenerator:
    """Generate at most two proposals without deciding the RCA conclusion."""

    llm_client: LLMClient
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if self.llm_client is None:
            raise ModelValidationError(
                "Qwen Hypothesis Candidate Generator requires an LLM client"
            )

    def generate(
        self,
        *,
        request_id: str,
        findings: list[AgentFinding],
    ) -> HypothesisCandidateGeneration:
        evidence_by_id = {
            evidence.evidence_id: evidence
            for finding in findings
            for evidence in finding.evidence
            if evidence.is_typed
        }
        if not evidence_by_id:
            return HypothesisCandidateGeneration(candidates=(), attempt_count=0)

        validation_errors: list[str] = []
        for attempt in range(1, _OUTPUT_ATTEMPTS + 1):
            request = LLMRequest(
                    agent=AgentKind.RCA_REASONING.value,
                    prompt_name="hypothesis_candidate_generator",
                    prompt_version=self.prompt_version,
                    payload={
                        "request_id": request_id,
                        "specialist_findings": [
                            {
                                "agent": finding.agent,
                                "summary": finding.summary,
                                "confidence": finding.confidence,
                                "evidence_ids": list(finding.evidence_ids),
                            }
                            for finding in findings
                        ],
                        "typed_evidence_register": _evidence_register(findings),
                        "max_candidates": _MAX_CANDIDATES,
                        "output_attempt": attempt,
                        "previous_validation_feedback": (
                            _candidate_repair_feedback(
                                validation_errors[-1],
                                evidence_by_id=evidence_by_id,
                            )
                            if validation_errors
                            else None
                        ),
                        "deterministic_candidate_proposals": [],
                    },
                    temperature=0.0,
                )
            try:
                response = self.llm_client.complete_json(request)
            except LLMOutputValidationError as exc:
                validation_errors.append(str(exc).strip() or type(exc).__name__)
                continue
            try:
                if set(response.data) != {"candidates", "analysis_summary"}:
                    raise LLMOutputValidationError(
                        "candidate output must contain exactly candidates and "
                        "analysis_summary"
                    )
                raw_candidates = response.data.get("candidates")
                summary = response.data.get("analysis_summary")
                if not isinstance(raw_candidates, list) or len(raw_candidates) > _MAX_CANDIDATES:
                    raise LLMOutputValidationError(
                        f"candidates must be an array with at most {_MAX_CANDIDATES} items"
                    )
                if not isinstance(summary, str) or not summary.strip():
                    raise LLMOutputValidationError(
                        "analysis_summary must be a non-empty string"
                    )
                proposals_list: list[HypothesisCandidateProposal] = []
                candidate_errors: list[str] = []
                for index, candidate in enumerate(raw_candidates):
                    try:
                        proposals_list.append(
                            _parse_candidate(
                                candidate,
                                index=index,
                                evidence_by_id=evidence_by_id,
                            )
                        )
                    except (LLMOutputValidationError, TypeError, ValueError) as exc:
                        candidate_errors.append(
                            str(exc).strip() or f"candidates[{index}] is invalid"
                        )
                if candidate_errors and proposals_list:
                    validation_errors.extend(candidate_errors)
                if candidate_errors and not proposals_list:
                    validation_errors.extend(candidate_errors)
                    if attempt < _OUTPUT_ATTEMPTS:
                        continue
                    return HypothesisCandidateGeneration(
                        candidates=(),
                        attempt_count=attempt,
                        validation_errors=tuple(validation_errors),
                        candidate_output_invalid=True,
                    )
                proposals = tuple(proposals_list)
                roots = [candidate.root_cause.casefold() for candidate in proposals]
                if len(roots) != len(set(roots)):
                    raise LLMOutputValidationError(
                        "candidate root_cause values must not be duplicates"
                    )
                return HypothesisCandidateGeneration(
                    candidates=proposals,
                    attempt_count=attempt,
                    validation_errors=tuple(validation_errors),
                )
            except (LLMOutputValidationError, TypeError, ValueError) as exc:
                validation_errors.append(str(exc).strip() or type(exc).__name__)

        raise LLMOutputValidationError(
            "Qwen Hypothesis Candidate Generator returned invalid output twice: "
            + " | ".join(validation_errors)
        )


__all__ = [
    "HypothesisCandidateGeneration",
    "HypothesisCandidateProposal",
    "QwenHypothesisCandidateGenerator",
]
