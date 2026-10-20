"""Markdown presentation of Finalizer-owned triage; never select a hypothesis."""

from __future__ import annotations

import re
from typing import Any

from yield_rca_core.models import RCAState
from yield_rca_core.triage_result import TriageScope

ASSESSMENT_LABELS = {
    "parameter_support": "Parameter",
    "mechanism_support": "Mechanism",
    "entity_consistency": "Entity",
    "operation_consistency": "Operation",
    "recipe_version_consistency": "Recipe / Version",
    "scope_consistency": "Scope",
    "temporal_consistency": "Time",
    "contradiction": "Contradiction",
    "causal_chain": "Causal Chain",
    "alternative_status": "Alternative",
}
COMPARISON_LABELS = {
    "preference_established": "Evidence preference established; causal confirmation is separate.",
    "preference_not_established": "Preference not established.",
    "tied": "Tied — not distinguishable with current evidence.",
    "not_distinguishable": "Not distinguishable with current evidence.",
}
UNCERTAINTY_LABELS = {
    "not_observed": "Not observed in the scoped observation",
    "not_acquired": "Evidence not yet acquired",
    "unavailable": "Evidence unavailable or assessment not recorded",
    "conflicting": "Conflicting evidence",
    "unresolved_alternative": "Unresolved alternative",
    "unsupported_mechanism": "Mechanism not yet supported",
    "insufficient_scope": "Insufficient scope support",
    "insufficient_temporal_support": "Insufficient time support",
}


def text(value: Any) -> str:
    """Escape authored prose as prose, preventing new Markdown authority headings."""
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", str(value)).replace("\n", " ").replace("\r", " ")


def scope_text(scope: TriageScope) -> str:
    values = [
        (label, getattr(scope, key))
        for key, label in (
            ("entity", "Entity"),
            ("operation", "Operation"),
            ("equipment", "Equipment"),
            ("chamber", "Chamber"),
            ("recipe", "Recipe"),
            ("recipe_version", "Version"),
        )
    ]
    parts = [f"{label}: {value}" for label, value in values if value is not None]
    parts += [f"Scope: {scope.scope_kind}"]
    if scope.lane_ids:
        parts.append("Lanes: " + ", ".join(scope.lane_ids))
    parts.append("Time: " + (" to ".join(scope.time_window) or "Not declared"))
    return "; ".join(parts)


def triage_sections(state: RCAState) -> tuple[list[str], list[str]]:
    t = state.triage_result
    if t is None:
        return [], []
    citations: list[str] = []
    evidence = state.evidence_by_id
    lines = [
        "## Investigation Triage",
        "",
        "Suspected hypotheses; engineering sign-off: Not recorded.",
        "Execution completed; scientific question remains unresolved.",
        "",
    ]
    for label, h in (
        ("Primary Suspected Cause", t.primary),
        ("Strongest Alternative", t.strongest_alternative),
    ):
        lines += [f"### {label}", ""]
        if h is None:
            lines.append(
                "No admitted primary hypothesis."
                if label.startswith("Primary")
                else "No valid alternative identified."
                if t.comparison.alternative_disposition == "no_valid_alternative_identified"
                else "Alternative not investigated."
            )
            continue
        lines += [
            text(h.engineering_statement),
            "",
            text(h.causal_explanation),
            "",
            f"- Candidate ID: `{h.candidate_id}`",
            f"- {text(scope_text(h.scope))}",
        ]
        for role, ids in (
            ("Supporting Evidence", h.supporting_evidence_ids),
            ("Contradictory Evidence", h.contradicting_evidence_ids),
        ):
            lines += ["", f"#### {role}", ""]
            if not ids:
                lines.append("None cited; this does not prove absence.")
            for eid in ids:
                item = evidence.get(eid)
                observation = (
                    (item.observation or item.summary)
                    if item
                    else ("Referenced evidence unavailable in this view.")
                )
                lines.append(f"- `{eid}`: {text(observation)}")
                citations.append(eid)
    lines += [
        "",
        "### Comparison",
        "",
        COMPARISON_LABELS[t.comparison.preference_status],
        text(t.comparison.primary_ordering_reason),
    ]
    if t.comparison.alternative_retention_reason:
        lines.append(text(t.comparison.alternative_retention_reason))
    if t.comparison.discriminating_evidence_ids:
        lines.append(
            "Discriminating Evidence: " + ", ".join(t.comparison.discriminating_evidence_ids)
        )
        citations.extend(t.comparison.discriminating_evidence_ids)
    lines += [
        "",
        "### Evidence Assessment",
        "",
        "| Hypothesis | Dimension | Status | Observation | Explanation | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    if not t.hypotheses:
        lines.append("No admitted hypotheses to assess.")
    for a in t.evidence_assessment:
        role = (
            "Primary" if t.primary and a.candidate_id == t.primary.candidate_id else "Alternative"
        )
        for key, label in ASSESSMENT_LABELS.items():
            claim = getattr(a, key)
            lines.append(
                f"| {role} | {label} | {claim.status if claim else 'Not assessed'} | "
                f"{claim.observation_status.replace('_', ' ') if claim else 'Unknown'} | "
                f"{text(claim.explanation) if claim else 'No recorded assessment'} | "
                f"{', '.join(claim.evidence_ids) if claim else 'None'} |"
            )
            if claim:
                citations.extend(claim.evidence_ids)
    lines += ["", "### Remaining Uncertainty", ""]
    for gap in t.remaining_uncertainty:
        lines += [
            f"- **{UNCERTAINTY_LABELS[gap.kind]}**: {text(gap.description)}",
            f"  Scope: {text(scope_text(gap.scope))}",
        ]
        citations.extend(gap.evidence_ids)
    lines += ["", "### Recommended Next Investigation", ""]
    action = t.next_investigation_action
    if action is None:
        lines.append(text(t.no_next_action_reason))
    else:
        lines += [
            text(action.requested_observation),
            "",
            f"- Targets: {', '.join(action.target_candidate_ids) or 'No established hypotheses'}",
            f"- {text(scope_text(action.scope))}",
            f"- Executor: {action.executor.replace('_', ' ')}",
            "- Recommendation only; no automatic execution.",
            f"- Approval required: {'Yes' if action.approval_required else 'No'}",
            f"- Executability: {action.executability.replace('_', ' ')}",
            f"- Availability: {action.availability}; {text(action.availability_reason)}",
        ]
        if action.executor == "human_engineer":
            lines.append("This investigation is a recommendation for an engineer to perform.")
        for outcome in action.expected_discriminating_outcomes:
            role = (
                "Primary"
                if t.primary and outcome.candidate_id == t.primary.candidate_id
                else "Alternative"
            )
            lines += [
                f"- Observation supporting {role}: {text(outcome.expected_observation)}",
                f"  {text(outcome.interpretation)}",
            ]
        lines.append(f"- Remaining indistinguishability: {text(action.inconclusive_outcome)}")
    return lines, list(dict.fromkeys(citations))


def confirmation_sections(state: RCAState) -> tuple[list[str], list[str]]:
    """Coexisting old authority is projected literally, never from triage text."""
    a = state.authoritative_rca_result
    lines = ["## Confirmation Status", "", "- Engineering sign-off: Not recorded."]
    if a is None:
        return lines + ["- Internal confirmation: Not recorded."], []
    status = (
        "Internally supported"
        if a.confirmation_status == "supported"
        else a.confirmation_status.replace("_", " ")
    )
    lines += [
        f"- Internal confirmation: {status}",
        f"- Old RCA conclusion: {a.conclusion_status}",
        f"- Internal RCA root cause: {text(a.root_cause) if a.root_cause else 'Not published'}",
    ]
    return lines, list(a.evidence_refs)
