"""Read-only projections for the Python Finalizer; no investigation or inference.

Only persisted admission, ordering, checks, coverage and proposals are consumed.
This module constructs values; investigation_finalizer alone attaches authority.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import fields
from typing import Any, cast

from yield_rca_core.models import ModelValidationError, RCAState
from yield_rca_core.triage_result import (
    DiscriminatingOutcome,
    NextInvestigationAction,
    TriageClaimAssessment,
    TriageComparison,
    TriageEvidenceAssessment,
    TriageHypothesis,
    TriageResult,
    TriageScope,
    TriageUncertainty,
    UncertaintyKind,
)


def _scope(profile: dict[str, Any]) -> TriageScope:
    return TriageScope(
        entity=profile.get("entity"),
        operation=profile.get("claimed_operation"),
        equipment=profile.get("claimed_equipment"),
        chamber=profile.get("claimed_chamber"),
        recipe=profile.get("claimed_recipe"),
        recipe_version=profile.get("recipe_version"),
        scope_kind=profile.get("claimed_scope_kind", "unresolved"),
        lane_ids=tuple(profile.get("claimed_lane_ids", ())),
        time_window=tuple(profile.get("time_window", ())),
    )


def _hypothesis(row: dict[str, Any], profile: dict[str, Any]) -> TriageHypothesis:
    return TriageHypothesis(
        candidate_id=row["candidate_id"],
        engineering_statement=row["root_cause"],
        causal_explanation=row["causal_explanation"],
        primary_mechanism=profile["primary_mechanism"],
        supporting_evidence_ids=tuple(row.get("supporting_evidence_ids", ())),
        contradicting_evidence_ids=tuple(row.get("contradicting_evidence_ids", ())),
        scope=_scope(profile),
    )


_CLAIMS = {
    "parameter_support": "parameter",
    "mechanism_support": "mechanism",
    "operation_consistency": "operation",
    "recipe_version_consistency": "recipe_version",
    "scope_consistency": "scope",
    "temporal_consistency": "temporal",
    "contradiction": "contradiction",
    "causal_chain": "causal_chain",
    "alternative_status": "alternative",
}
_STATUSES = {"supported", "plausible", "incomplete", "conflicted", "unavailable"}


def _claim(
    raw: dict[str, Any] | None, path: str, evidence_ids: set[str]
) -> TriageClaimAssessment | None:
    if not raw or raw.get("status") not in _STATUSES:
        return None
    ids = tuple(raw.get("evidence_ids", ()))
    if not set(ids) <= evidence_ids:
        return None
    # A cited claim is observed; absence of citations cannot assert a negative.
    observation = raw.get("observation_status", "observed" if ids else "unknown")
    try:
        return TriageClaimAssessment(
            status=raw["status"],
            evidence_ids=ids,
            explanation=raw.get("reason") or path,
            source_path=path,
            observation_status=observation,
        )
    except ModelValidationError:
        # Frozen triage/1.0 cannot express e.g. supported-without-citations.
        # Keep the old matrix intact; leave the projection unassessed.
        return None


def _assessment(
    h: TriageHypothesis, row: dict[str, Any], details: dict[str, Any], evidence_ids: set[str]
) -> TriageEvidenceAssessment:
    matrix = row.get("causal_evidence_matrix", {})
    claims = matrix.get("claims", {})
    base = f"ranked_candidates[candidate_id={h.candidate_id}].causal_evidence_matrix"
    values = {
        name: _claim(claims.get(key), f"{base}.claims.{key}", evidence_ids)
        for name, key in _CLAIMS.items()
    }
    # Every explicitly declared entity must be consistent. Preserve the weakest
    # existing check; a supported chamber cannot hide conflicting equipment.
    entity_keys = [key for key in ("equipment", "chamber") if getattr(h.scope, key)]
    if not entity_keys:
        entity_keys = ["entity"]
    entity_checks = [_claim(claims.get(k), f"{base}.claims.{k}", evidence_ids) for k in entity_keys]
    severity = {"conflicted": 0, "unavailable": 1, "incomplete": 2, "plausible": 3, "supported": 4}
    explicit = [c for c in entity_checks if c is not None]
    weakest = min(explicit, key=lambda c: severity[c.status]) if explicit else None
    values["entity_consistency"] = (
        weakest
        if weakest and (weakest.status != "supported" or all(c is not None for c in entity_checks))
        else None
    )
    chain = matrix.get("causal_chain", {})
    # Chain completeness != mechanism confirmation. Retain the weakest stage.
    if chain.get("status") == "complete":
        stages = set(chain.get("stages", {}).values())
        if stages and stages <= {"supported", "plausible"}:
            chain = {**chain, "status": "plausible" if "plausible" in stages else "supported"}
    values["causal_chain"] = _claim(chain, f"{base}.causal_chain", evidence_ids)
    search = details.get("alternative_search_status")
    if search in {
        "unresolved",
        "not_searched",
        "in_progress",
        "alternative_found",
        "blocked_by_missing_data",
    }:
        values["alternative_status"] = TriageClaimAssessment(
            status="incomplete",
            evidence_ids=(),
            observation_status="unknown",
            explanation=f"Persisted alternative search status: {search}.",
            source_path="alternative_search_status",
        )
    return TriageEvidenceAssessment(candidate_id=h.candidate_id, **values)


def _uncertainties(
    hypotheses: tuple[TriageHypothesis, ...],
    assessments: tuple[TriageEvidenceAssessment, ...],
    details: dict[str, Any],
    profiles: dict[str, Any],
) -> list[TriageUncertainty]:
    result = []
    for h, assessment in zip(hypotheses, assessments, strict=True):
        for field in fields(assessment):
            if field.name == "candidate_id":
                continue
            claim = getattr(assessment, field.name)
            if claim is not None and claim.status == "supported":
                continue
            kind = (
                "unavailable"
                if claim is None or claim.status == "unavailable"
                else "conflicting"
                if claim.status == "conflicted"
                else "unresolved_alternative"
                if field.name == "alternative_status"
                else "insufficient_scope"
                if field.name in {"scope_consistency", "recipe_version_consistency"}
                else "insufficient_temporal_support"
                if field.name == "temporal_consistency"
                else "unsupported_mechanism"
                if field.name in {"mechanism_support", "causal_chain"}
                else "unavailable"
            )
            if claim and claim.observation_status in {
                "not_observed",
                "not_acquired",
                "unavailable",
            }:
                kind = claim.observation_status
            result.append(
                TriageUncertainty(
                    kind=cast(UncertaintyKind, kind),
                    description=(
                        claim.explanation
                        if claim
                        else (
                            f"{field.name}: no representable persisted assessment; "
                            "not a negative observation."
                        )
                    ),
                    candidate_ids=(h.candidate_id,),
                    evidence_ids=claim.evidence_ids if claim else (),
                    scope=h.scope,
                )
            )
    # Coverage is a persisted observation contract, not inferred from empty outcomes.
    lanes = details.get("evidence_synthesis", {}).get("active_causal_lanes", [])
    for lane in lanes:
        targets = tuple(
            h
            for h in hypotheses
            if lane.get("lane_id")
            in (*h.scope.lane_ids, *profiles[h.candidate_id].get("comparison_lane_ids", []))
        )
        if not targets:
            continue
        coverage = lane.get("outcome_observation_coverage", {})
        status = coverage.get("status", "unknown")
        if status == "acquired":
            continue
        kind = (
            "not_acquired"
            if status == "not_acquired" or coverage.get("not_acquired_lot_ids")
            else "unavailable"
        )
        for h in targets:
            result.append(
                TriageUncertainty(
                    kind=cast(UncertaintyKind, kind),
                    description=f"Outcome coverage for {lane.get('lane_id')}: {status}; "
                    "unobserved/unqueried outcomes are not negative observations.",
                    candidate_ids=(h.candidate_id,),
                    evidence_ids=(),
                    scope=_scope(
                        {
                            "claimed_operation": lane.get("operation") or None,
                            "claimed_equipment": lane.get("equipment") or None,
                            "claimed_chamber": lane.get("chamber") or None,
                            "claimed_recipe": lane.get("recipe") or None,
                            "claimed_scope_kind": "lane",
                            "claimed_lane_ids": [lane["lane_id"]],
                            "time_window": lane.get("time_window", []),
                        }
                    ),
                )
            )
    return result


def _next_action(
    state: RCAState,
    details: dict[str, Any],
    hypotheses: tuple[TriageHypothesis, ...],
    profiles: dict[str, Any],
) -> NextInvestigationAction | None:
    gaps = details.get("causal_evidence_gaps", [])
    selected = details.get("candidate_comparison", {}).get("selected_gap_ids", [])
    # Existing selection, then persisted gap order. No new action-value algorithm.
    gaps = sorted(
        enumerate(gaps), key=lambda item: (item[1].get("gap_id") not in selected, item[0])
    )
    ids = tuple(h.candidate_id for h in hypotheses)
    for _, gap in gaps:
        owner = gap.get("candidate_id")
        if owner and owner not in ids:
            continue
        if gap.get("status") in {"resolved", "closed", "supported"}:
            continue
        kind = gap.get("discriminator_kind")
        predictions = []
        for h in hypotheses:
            matches = [
                p
                for p in profiles[h.candidate_id].get("distinguishing_predictions", [])
                if p.get("discriminator_kind") == kind
                and p.get("prediction")
                and set(h.scope.lane_ids) & set(p.get("lane_ids", []))
            ]
            if not matches:
                break
            predictions.append(matches[0])
        if len(predictions) != len(hypotheses) or not hypotheses:
            continue
        if len(ids) == 2 and (
            predictions[0]["prediction"].strip().casefold()
            == predictions[1]["prediction"].strip().casefold()
            or not set(predictions[0].get("lane_ids", [])) & set(predictions[1].get("lane_ids", []))
        ):
            continue
        target = gap.get("target_scope", {})
        lane_id = target.get("lane_id")
        if lane_id and any(lane_id not in p.get("lane_ids", []) for p in predictions):
            continue
        options = [
            a
            for a in state.latest_action_value_assessments
            if a.gap_id == gap.get("gap_id")
            and a.candidate_id in (None, *ids)
            and a.lane_id in (None, lane_id)
        ]
        option = next(
            (a for a in options if a.eligible and a.high_value), options[0] if options else None
        )
        executable = bool(
            option
            and option.eligible
            and option.high_value
            and option.source_availability == "available"
            and option.discriminative_analysis_executability != "engineer_evidence_required"
            and option.action_kind in gap.get("allowed_actions", [])
            and option.remaining_tool_budget >= option.estimated_tool_cost
            and option.action_kind != "run_rca_reasoning"
        )
        scope = hypotheses[0].scope
        target = gap.get("target_scope", {})
        # Action scope is independently proposed by the existing gap, not a new claim.
        raw_window = (target.get("window_start"), target.get("window_end"))
        action_profile = {
            "claimed_operation": target.get("operation"),
            "claimed_equipment": target.get("equipment"),
            "claimed_chamber": target.get("chamber"),
            "claimed_recipe": target.get("recipe"),
            "claimed_scope_kind": "lane" if target.get("lane_id") else "unresolved",
            "claimed_lane_ids": [target["lane_id"]] if target.get("lane_id") else [],
            "time_window": list(raw_window) if all(raw_window) else [],
        }
        if target:
            try:
                scope = _scope(action_profile)
            except ModelValidationError:
                # Do not silently repair an invalid or timezone-free proposed scope.
                continue
        request = gap.get("reason")
        if not isinstance(request, str) or not request.strip():
            continue
        expected_types = gap.get("expected_evidence_types", [])
        if expected_types:
            request += " Requested evidence types: " + ", ".join(expected_types) + "."
        return NextInvestigationAction(
            action_id=f"TRIAGE_NEXT_{gap['gap_id']}",
            purpose="discriminate_hypotheses" if len(ids) == 2 else "acquire_missing_evidence",
            target_candidate_ids=ids,
            requested_observation=request,
            scope=scope,
            expected_discriminating_outcomes=tuple(
                DiscriminatingOutcome(
                    candidate_id=h.candidate_id,
                    expected_observation=p["prediction"],
                    interpretation=(
                        f"Tests the persisted prediction for {h.candidate_id}; "
                        "not causal confirmation."
                    ),
                )
                for h, p in zip(hypotheses, predictions, strict=True)
            ),
            inconclusive_outcome=(
                "Predictions remain untested or compatible with both hypotheses; "
                "retain uncertainty."
            ),
            executor="system_tool" if executable else "human_engineer",
            approval_required=not executable,
            executability="executable" if executable else "external_only",
            availability=(
                "available"
                if executable
                else "unavailable"
                if option and option.source_availability == "unavailable"
                else "unknown"
            ),
            availability_reason=(
                f"Existing ActionValue option {option.option_id}: "
                f"{option.rejection_reason or option.source_availability}. "
                if option
                else "No executable ActionValue option recorded. "
            )
            + "Recommendation only; engineer must assess feasibility before external acquisition.",
        )
    return None


def build_triage_projection(state: RCAState) -> TriageResult | None:
    """Called only after successful old-authority finalization, never on load/API."""
    finding = state.authoritative_rca_finding
    authority = state.authoritative_rca_result
    if finding is None or authority is None:
        return None
    if authority.source_finding_id != finding.finding_id:
        return None
    if state.competition_trace is not None and state.competition_trace.competition_status in {
        "active",
        "pending",
        "failed",
    }:
        return None
    if state.job.status != "completed" and not (
        state.job.status == "running" and state.stop_reason == "budget_exhausted"
    ):
        return None
    if (
        authority.competition_status
        not in {
            "not_required",
            "complete_confirmed",
            "complete_rejected",
            "exhausted",
            "resolved",
            "blocked_by_missing_data",
            "budget_exhausted",
        }
        or state.execution_metadata.get("investigation_decision_accepted") is False
    ):
        return None
    details = finding.details
    if "hypothesis_candidate_generation" not in details:
        return None  # Native/controlled and historical non-model contracts stay unchanged.
    rows = details.get("ranked_candidates", [])
    profiles_list = details.get("candidate_semantic_profiles", [])
    counts = Counter(p.get("candidate_id") for p in profiles_list)
    row_counts = Counter(r.get("candidate_id") for r in rows)
    profiles = {p.get("candidate_id"): p for p in profiles_list}
    resolutions = {
        r.get("candidate_id"): r.get("status") for r in details.get("candidate_resolutions", [])
    }
    evidence_ids = {e.evidence_id for e in state.evidence}
    selected: list[tuple[TriageHypothesis, dict[str, Any]]] = []
    if details["hypothesis_candidate_generation"].get("candidate_output_invalid") is False:
        for row in rows:
            cid = row.get("candidate_id")
            p = profiles.get(cid, {})
            if (
                counts[cid] != 1
                or row_counts[cid] != 1
                or row.get("basis") != "llm_evidence_composition"
                or p.get("mechanism_relation") not in {"reference", "independent_alternative"}
                or p.get("depends_on_candidate_id")
                or row.get("status") == "rejected"
                or resolutions.get(cid) == "contradicted"
            ):
                continue
            try:
                h = _hypothesis(row, p)
            except (ModelValidationError, KeyError, TypeError):
                continue
            if not set(h.supporting_evidence_ids + h.contradicting_evidence_ids) <= evidence_ids:
                continue
            if (
                selected
                and h.primary_mechanism.strip().casefold()
                == selected[0][0].primary_mechanism.strip().casefold()
            ):
                continue
            selected.append((h, row))
            if len(selected) == 2:
                break
    hypotheses = tuple(h for h, _ in selected)
    primary = hypotheses[0] if hypotheses else None
    alternative = hypotheses[1] if len(hypotheses) == 2 else None
    comparison = details.get("candidate_comparison", {})
    ranks = comparison.get("final_ranks", [])
    scores = []
    for h in hypotheses:
        matches = [r for r in ranks if r.get("root_cause") == h.engineering_statement]
        scores.append(matches[0].get("python_matrix_score") if len(matches) == 1 else None)
    tied = bool(alternative and scores[0] is not None and scores[0] == scores[1])
    assessments = tuple(_assessment(h, row, details, evidence_ids) for h, row in selected)
    uncertainty = _uncertainties(hypotheses, assessments, details, profiles)
    search = details.get("alternative_search_status")
    if alternative or search in {
        "unresolved",
        "not_searched",
        "in_progress",
        "alternative_found",
        "blocked_by_missing_data",
    }:
        uncertainty.append(
            TriageUncertainty(
                kind="unresolved_alternative",
                description=(
                    f"Alternative search: {search or 'not recorded'}; rank is not exclusion."
                ),
                candidate_ids=tuple(h.candidate_id for h in hypotheses),
                evidence_ids=(),
                scope=primary.scope if primary else _scope({}),
            )
        )
    if not uncertainty:
        uncertainty.append(
            TriageUncertainty(
                kind="unavailable",
                description="Engineering sign-off is not recorded."
                if primary
                else "No admissible independent candidate can be projected.",
                candidate_ids=tuple(h.candidate_id for h in hypotheses),
                evidence_ids=(),
                scope=primary.scope if primary else _scope({}),
            )
        )
    action = _next_action(state, details, hypotheses, profiles)
    terminal = {
        "budget_exhausted": "budget_exhausted",
        "data_unavailable": "data_unavailable",
        "no_high_value_action": "no_high_value_action",
        "no_allowed_action": "no_allowed_action",
        "critical_contradiction": "critical_contradiction",
    }.get(state.stop_reason)
    if terminal is None:
        terminal = (
            "hypotheses_tied"
            if alternative
            else "single_candidate_unconfirmed"
            if primary
            else "insufficient_candidate_evidence"
        )
    if (
        action
        and action.executor == "human_engineer"
        and terminal not in {"budget_exhausted", "data_unavailable"}
    ):
        terminal = "engineer_evidence_required"
    result = TriageResult(
        result_id=f"TRIAGE_{finding.finding_id}",
        source_finding_id=finding.finding_id,
        primary=primary,
        strongest_alternative=alternative,
        comparison=TriageComparison(
            preference_status=("tied" if tied else "not_distinguishable")
            if alternative
            else "preference_not_established",
            ordering_basis="stable_presentation_order"
            if alternative
            else "single"
            if primary
            else "none",
            primary_ordering_reason=(
                "Persisted admitted-candidate ranking supplies presentation "
                "order only; no causal winner asserted."
            ),
            alternative_retention_reason=(
                "Independent admitted mechanism with supporting Evidence remains unresolved."
            )
            if alternative
            else None,
            alternative_disposition="retained"
            if alternative
            else (
                "no_valid_alternative_identified"
                if search in {"alternatives_eliminated", "alternative_found"}
                else "alternative_not_investigated"
            ),
            discriminating_evidence_ids=(),
            preferred_candidate_id=None,
        ),
        evidence_assessment=assessments,
        remaining_uncertainty=tuple(uncertainty),
        next_investigation_action=action,
        no_next_action_reason=None
        if action
        else (
            "No persisted scope-compatible action and conditional predictions meet triage/1.0; "
            "no engineering outcome invented."
        ),
        terminal_reason=terminal,
    )
    result.validate_state_references(state.findings, evidence_ids)
    return result
