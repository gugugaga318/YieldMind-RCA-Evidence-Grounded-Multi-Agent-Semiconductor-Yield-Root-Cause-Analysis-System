"""Evidence-bounded engineering synthesis and improvement recommendations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from yield_rca_core.llm_gateway import LLMClient, LLMOutputValidationError, LLMRequest
from yield_rca_core.models import (
    AgentFinding,
    AgentKind,
    AgentMode,
    FindingKind,
    HypothesisStatus,
    ModelValidationError,
    Warning,
)

INPUT_AGENTS = frozenset(
    {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
        AgentKind.KNOWLEDGE.value,
        AgentKind.RCA_REASONING.value,
    }
)
RECOMMENDATION_CATEGORIES = (
    "containment_actions",
    "corrective_actions",
    "recipe_optimization",
    "preventive_actions",
    "fab_system_optimization",
)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _finding_index(findings: list[AgentFinding]) -> dict[str, AgentFinding]:
    indexed: dict[str, AgentFinding] = {}
    for finding in findings:
        if finding.agent not in INPUT_AGENTS:
            raise ModelValidationError(
                f"Improvement Agent does not accept finding from {finding.agent!r}"
            )
        if finding.agent in indexed:
            if finding.agent != AgentKind.KNOWLEDGE.value:
                raise ModelValidationError(f"duplicate finding for {finding.agent!r}")
            if finding.finding_kind == FindingKind.KNOWLEDGE_DISCOVERY.value:
                indexed[finding.agent] = finding
            continue
        indexed[finding.agent] = finding
    missing = INPUT_AGENTS - indexed.keys()
    if missing:
        raise ModelValidationError(
            f"Improvement Agent requires findings from: {sorted(missing)}"
        )
    return indexed


def _preferred_evidence(finding: AgentFinding, prefixes: tuple[str, ...]) -> list[str]:
    selected = [
        evidence_id
        for evidence_id in finding.evidence_ids
        if evidence_id.startswith(prefixes)
    ]
    return selected or list(finding.evidence_ids)


def _recommendation(
    recommendation_id: str,
    category: str,
    action: str,
    rationale: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    if category not in RECOMMENDATION_CATEGORIES:
        raise ModelValidationError(f"unknown recommendation category: {category}")
    if not evidence_ids:
        raise ModelValidationError("improvement recommendation requires evidence_ids")
    return {
        "recommendation_id": recommendation_id,
        "category": category,
        "action": action,
        "rationale": rationale,
        "evidence_ids": _deduplicate(evidence_ids),
    }


def _historical_case_matches_root_cause(root_cause: str, top_case: dict[str, Any]) -> bool:
    ignored = {"the", "and", "for", "with", "from", "reduced", "caused", "case"}

    def tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 4 and token not in ignored
        }

    current_tokens = tokens(root_cause)
    historical_tokens = tokens(
        " ".join(
            [
                str(top_case.get("title", "")),
                str(top_case.get("root_cause", "")),
                str(top_case.get("symptom", "")),
            ]
        )
    )
    return len(current_tokens & historical_tokens) >= 2


def _evidence_payload(
    findings: list[AgentFinding],
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    wanted = set(evidence_ids)
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for finding in findings:
        for item in finding.details.get("evidence", []):
            evidence_id = str(item.get("evidence_id", ""))
            if evidence_id in wanted:
                evidence_by_id[evidence_id] = dict(item)
    missing = wanted - evidence_by_id.keys()
    if missing:
        raise ModelValidationError(
            f"Improvement Agent evidence payload is missing: {sorted(missing)}"
        )
    return [evidence_by_id[evidence_id] for evidence_id in evidence_ids]


@dataclass(frozen=True)
class ImprovementAgent:
    """Summarize a validated RCA and propose evidence-backed engineering actions."""

    llm_client: LLMClient | None = None
    agent_mode: str = AgentMode.DETERMINISTIC.value
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        try:
            AgentMode(self.agent_mode)
        except ValueError as exc:
            raise ModelValidationError(
                f"unknown Improvement Agent mode: {self.agent_mode}"
            ) from exc
        if self.agent_mode == AgentMode.DETERMINISTIC.value and self.llm_client is not None:
            raise ModelValidationError("deterministic Improvement Agent must not configure an LLM")
        if self.agent_mode != AgentMode.DETERMINISTIC.value and self.llm_client is None:
            raise ModelValidationError("LLM/Fake Improvement Agent requires an LLM client")

    def analyze(
        self,
        *,
        request_id: str,
        findings: list[AgentFinding],
    ) -> AgentFinding:
        indexed = _finding_index(findings)
        mes = indexed[AgentKind.MES.value]
        fdc = indexed[AgentKind.FDC.value]
        knowledge = indexed[AgentKind.KNOWLEDGE.value]
        rca = indexed[AgentKind.RCA_REASONING.value]

        root_cause = str(rca.details.get("root_cause", "")).strip()
        rca_status = str(rca.details.get("status", "")).strip()
        supported = rca_status == HypothesisStatus.SUPPORTED.value
        root_evidence_ids = [
            str(item)
            for item in rca.details.get("root_cause_evidence_ids", rca.evidence_ids)
        ]
        affected_lots = [str(item) for item in mes.details.get("affected_lots", [])]
        impact_lots = [str(item) for item in mes.details.get("impact_lots", [])]
        top_case = knowledge.details.get("top_case", {})
        historical_similarity = (
            float(top_case.get("similarity", 0.0)) if isinstance(top_case, dict) else 0.0
        )
        has_historical_case = bool(
            "EV_KNOWLEDGE_MATCH" in knowledge.evidence_ids
            and historical_similarity >= 0.8
            and isinstance(top_case, dict)
            and _historical_case_matches_root_cause(root_cause, top_case)
        )
        cross_lot = len(affected_lots) > 1
        fab_level_criteria = [
            criterion
            for criterion, present in (
                ("cross_lot", cross_lot),
                ("historical_confirmed_case", has_historical_case),
            )
            if present
        ]
        fab_level_supported = supported and bool(fab_level_criteria)

        scope_count = len(affected_lots)
        incident_summary = (
            f"Supported RCA identifies {root_cause} for an affected population of "
            f"{scope_count} Lot(s)."
            if supported
            else (
                f"The investigation remains inconclusive for {scope_count} affected Lot(s); "
                "optimization actions are limited to containment and additional validation."
            )
        )
        fab_level_summary = (
            f"Fab-level improvement review is justified by: {', '.join(fab_level_criteria)}."
            if fab_level_supported
            else (
                "No Fab-level conclusion is permitted because the RCA or recurrence scope "
                "criteria are not satisfied."
            )
        )

        recommendations: dict[str, list[dict[str, Any]]] = {
            category: [] for category in RECOMMENDATION_CATEGORIES
        }
        mes_scope_ids = _preferred_evidence(
            mes,
            (
                "EV_MES_IMPACT_LOTS",
                "EV_MES_COMMON_CHAMBER",
                "EV_ANALYTICS_AFFECTED_LOTS",
                "EV_FDC_EXCURSION_WINDOW",
            ),
        )
        fdc_signal_ids = _preferred_evidence(
            fdc,
            ("EV_SPC_", "EV_OOC_EVENTS", "EV_FDC_"),
        )
        calculated_spc_ids = [
            evidence_id
            for evidence_id in fdc.evidence_ids
            if evidence_id.startswith("EV_SPC_")
            and not evidence_id.startswith("EV_SPC_BASELINE_STATUS")
        ]
        parameter_shift_ids = [
            evidence_id
            for evidence_id in fdc.evidence_ids
            if evidence_id.startswith("EV_FDC_")
            and evidence_id not in {"EV_FDC_EXCURSION_WINDOW", "EV_FDC_CMP_NORMAL_EXCLUSION"}
        ]
        recipe_review_signal_ids = calculated_spc_ids or parameter_shift_ids
        knowledge_ids = _preferred_evidence(knowledge, ("EV_KNOWLEDGE_MATCH",))

        if affected_lots:
            recommendations["containment_actions"].append(
                _recommendation(
                    "REC_CONTAIN_001",
                    "containment_actions",
                    "Maintain containment of the identified affected and exposed population "
                    "until engineering disposition is complete.",
                    "MES scope evidence identifies the population requiring disposition.",
                    mes_scope_ids,
                )
            )

        if supported:
            raw_actions = rca.details.get("recommended_actions", [])
            if isinstance(raw_actions, list):
                for index, action in enumerate(raw_actions, start=1):
                    if not isinstance(action, dict):
                        continue
                    action_text = str(action.get("action", "")).strip()
                    action_evidence = [str(item) for item in action.get("evidence_ids", [])]
                    if action_text and action_evidence:
                        recommendations["corrective_actions"].append(
                            _recommendation(
                                f"REC_CORRECT_{index:03d}",
                                "corrective_actions",
                                action_text,
                                "The action is retained from the matched confirmed RCA case.",
                                action_evidence,
                            )
                        )

            recipe_changes = mes.details.get("recipe_changes", [])
            if "EV_MES_RECIPE_CHANGE" in mes.evidence_ids and recipe_changes:
                recommendations["recipe_optimization"].append(
                    _recommendation(
                        "REC_RECIPE_001",
                        "recipe_optimization",
                        "Validate the detected Recipe version change with an approved split-Lot "
                        "or qualification experiment and Process Engineer approval before "
                        "production release.",
                        "MES evidence records a Recipe version change in the affected scope.",
                        ["EV_MES_RECIPE_CHANGE"],
                    )
                )
            elif recipe_review_signal_ids:
                recommendations["recipe_optimization"].append(
                    _recommendation(
                        "REC_RECIPE_001",
                        "recipe_optimization",
                        "Run a controlled DOE to assess whether the Recipe operating window "
                        "remains robust against the observed process-parameter excursion; do "
                        "not change the production Recipe without Process Engineer approval.",
                        "Calculated SPC evidence identifies parameters requiring window review.",
                        recipe_review_signal_ids,
                    )
                )

            recommendations["preventive_actions"].append(
                _recommendation(
                    "REC_PREVENT_001",
                    "preventive_actions",
                    "Review FDC/SPC alarm response, OCAP coverage, and preventive-maintenance "
                    "triggers for the implicated equipment and parameter set.",
                    "Current FDC/SPC and historical evidence support recurrence prevention.",
                    _deduplicate(fdc_signal_ids + knowledge_ids),
                )
            )

        if fab_level_supported:
            recommendations["fab_system_optimization"].append(
                _recommendation(
                    "REC_FAB_001",
                    "fab_system_optimization",
                    "Review equivalent chambers and recent Lots for the same signature, then "
                    "standardize the validated response in the module OCAP.",
                    "Cross-Lot or confirmed historical evidence supports a wider "
                    "recurrence review.",
                    _deduplicate(mes_scope_ids + knowledge_ids),
                )
            )

        warnings: list[Warning] = []
        if not supported:
            warnings.append(
                Warning(
                    warning_id="WARN_IMPROVEMENT_RCA_INCONCLUSIVE",
                    message=(
                        "Improvement Agent withheld root-cause-specific and Fab-level "
                        "recommendations because RCA is inconclusive."
                    ),
                    evidence_ids=root_evidence_ids,
                )
            )

        recommendation_ids = [
            str(item["recommendation_id"])
            for category in RECOMMENDATION_CATEGORIES
            for item in recommendations[category]
        ]
        evidence_ids = _deduplicate(
            root_evidence_ids
            + [
                str(evidence_id)
                for category in RECOMMENDATION_CATEGORIES
                for item in recommendations[category]
                for evidence_id in item["evidence_ids"]
            ]
        )
        if not evidence_ids:
            raise ModelValidationError("Improvement Agent requires traceable evidence")

        llm_summary: str | None = None
        if self.agent_mode != AgentMode.DETERMINISTIC.value:
            assert self.llm_client is not None
            response = self.llm_client.complete_json(
                LLMRequest(
                    agent=AgentKind.IMPROVEMENT.value,
                    prompt_name="improvement",
                    prompt_version=self.prompt_version,
                    payload={
                        "incident_summary": incident_summary,
                        "fab_level_summary": fab_level_summary,
                        "recommendations": recommendations,
                        "recommendation_ids": recommendation_ids,
                        "evidence_ids": evidence_ids,
                    },
                )
            )
            try:
                llm_summary = str(response.data["engineering_summary"]).strip()
                returned_recommendations = [
                    str(item) for item in response.data["recommendation_ids"]
                ]
                returned_evidence = [str(item) for item in response.data["evidence_ids"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise LLMOutputValidationError(
                    "Improvement Agent returned an invalid structured summary"
                ) from exc
            if not llm_summary:
                raise LLMOutputValidationError("Improvement engineering_summary must not be empty")
            if set(returned_recommendations) != set(recommendation_ids):
                raise LLMOutputValidationError(
                    "Improvement Agent must preserve exactly the recommendation_ids"
                )
            if set(returned_evidence) != set(evidence_ids):
                raise LLMOutputValidationError(
                    "Improvement Agent must preserve exactly the evidence_ids"
                )

        summary = llm_summary or f"{incident_summary} {fab_level_summary}"
        return AgentFinding(
            finding_id=f"{request_id}:improvement",
            agent=AgentKind.IMPROVEMENT.value,
            summary=summary,
            confidence=rca.confidence,
            evidence_ids=evidence_ids,
            details={
                "incident_summary": incident_summary,
                "engineering_summary": summary,
                "rca_status": rca_status,
                "root_cause": root_cause,
                "scope_assessment": {
                    "level": "fab" if fab_level_supported else "event",
                    "fab_level_supported": fab_level_supported,
                    "criteria": fab_level_criteria,
                    "affected_lot_count": len(affected_lots),
                    "impact_lot_count": len(impact_lots),
                    "historical_case_status": (
                        "imported_confirmed" if has_historical_case else "not_available"
                    ),
                    "historical_similarity": historical_similarity,
                    "summary": fab_level_summary,
                },
                "recommendations": recommendations,
                "recommendation_ids": recommendation_ids,
                "memory_status": "candidate_ready_for_step_19_persistence",
                "requires_two_engineer_approval": True,
                "agent_mode": self.agent_mode,
                "llm_prompt_version": (
                    self.prompt_version
                    if self.agent_mode != AgentMode.DETERMINISTIC.value
                    else None
                ),
                "evidence": _evidence_payload(findings, evidence_ids),
            },
            warnings=warnings,
        )
