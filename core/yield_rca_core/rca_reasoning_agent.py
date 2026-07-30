"""Hypothesis Engine RCA reasoning.

Batch 19 removes the retired Legacy scoring engine.  Historical RCA snapshots
remain readable through the domain DTO compatibility contracts; new work always
uses the deterministic, evidence-bounded Hypothesis Engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yield_rca_core.hypothesis_engine import HypothesisEngine
from yield_rca_core.llm_gateway import LLMClient
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


@dataclass(frozen=True)
class RCAReasoningAgent:
    """Produce the official RCA finding from the Hypothesis Engine only."""

    hypothesis_engine: HypothesisEngine = HypothesisEngine()
    # Retained injection fields preserve the workflow constructor contract.
    # Hypothesis generation is deterministic and does not invoke an LLM.
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

        engine_result = self.hypothesis_engine.analyze(
            request_id=request_id,
            findings=findings,
            mode="active",
        )
        decision = engine_result["decision_gate"]
        root_cause = str(decision["root_cause"])
        status = str(decision["status"])
        confidence = float(decision["confidence"])
        supported = status == HypothesisStatus.SUPPORTED.value
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
                        "FDC evidence is physically conflicting: slurry flow declines while "
                        "estimated removal rate increases."
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
                "evidence_ids": list(candidate["evidence_ids"]),
            }
            for candidate in engine_result["candidates"]
        ]
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
                "hypothesis_engine_result": engine_result,
                "evidence": _merge_evidence_payload(findings),
            },
            warnings=list({warning.warning_id: warning for warning in warnings}.values()),
        )
