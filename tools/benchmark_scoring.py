"""Evaluator-only deterministic scorer. Never imported by the runtime observer."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from benchmark_observer import sha


def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def load_reference(path: Path, expected_hash: str):
    value = json.loads(path.read_text(encoding="utf-8"))
    if sha(value) != expected_hash:
        raise ValueError("frozen scoring reference hash mismatch")
    return value


def match_candidate(candidate, root, all_roots=()):
    profile = candidate.get("semantic_profile") or {}
    scope_profile = profile.get("claimed_scope") or {}
    mechanism = (
        candidate.get("mechanism_id")
        or profile.get("primary_mechanism")
        or candidate.get("root_cause")
    )
    text = normalize(mechanism)
    aliases = {normalize(x) for x in [root["mechanism_id"], *root["equivalent_labels"]]}
    # Reviewed full mechanism labels only. Unseen prose goes to blinded adjudication.
    negated = bool(re.search(r"\b(no|not|without|excluded|unlikely)\b", text))
    known_other = any(
        text in {normalize(x) for x in [r["mechanism_id"], *r["equivalent_labels"]]}
        for r in all_roots
        if r["mechanism_id"] != root["mechanism_id"]
    )
    mech = (
        None if negated or not text else True if text in aliases else False if known_other else None
    )
    result = {"mechanism_match": mech}
    for dim in ("operation", "equipment", "chamber"):
        expected = root["operation"] if dim == "operation" else root["entity"][dim]
        actual = candidate.get(dim) or profile.get("claimed_" + dim) or scope_profile.get(dim)
        result[dim + "_match"] = (
            None
            if expected is None or actual is None
            else str(actual).casefold() == str(expected).casefold()
        )
    recipe = candidate.get("recipe") or profile.get("claimed_recipe") or scope_profile.get("recipe")
    result["recipe_match"] = None if not recipe else recipe in root["recipe"]["allowed_ids"]
    scope = (
        candidate.get("scope_kind")
        or profile.get("claimed_scope_kind")
        or scope_profile.get("scope_kind")
    )
    result["scope_match"] = None if not scope else scope == root["scope"]["kind"]
    window = candidate.get("time_window")
    result["time_match"] = result["time_overclaim"] = None
    if window:
        try:
            a, b = (datetime.fromisoformat(window[x]) for x in ("start", "end"))
            c, d = (datetime.fromisoformat(root["time_window"][x]) for x in ("start", "end"))
            if a > b:
                result["time_match"] = False
            else:
                result["time_match"] = max(a, c) <= min(b, d)
                result["time_overclaim"] = a < c or b > d
        except (TypeError, ValueError, KeyError):
            pass
    # Recipe/version is a pair, never a cross-product of separately allowed lists.
    version = (
        candidate.get("recipe_version")
        or profile.get("claimed_recipe_version")
        or scope_profile.get("recipe_version")
    )
    pairs = root["recipe"].get("allowed_pairs", [])
    version_required = bool(root["recipe"].get("allowed_versions"))
    result["recipe_version_match"] = (
        None
        if not version or not recipe
        else {"recipe": recipe, "version": str(version)} in pairs
        if pairs
        else str(version) in root["recipe"].get("allowed_versions", [])
    )
    applicability = {
        dim + "_match": "required" if root["entity"][dim] is not None else "not_applicable"
        for dim in ("equipment", "chamber")
    }
    result["dimension_applicability"] = applicability
    # No inferred hierarchy: GT absence is N/A, not a guessed chamber constraint.
    required = ["mechanism_match", "operation_match"]
    required += [k for k, v in applicability.items() if v == "required"]
    if root["recipe"]["requirement"] == "required_identity":
        required.append("recipe_match")
        if version_required:
            required.append("recipe_version_match")

    def combine(values):
        return False if False in values else None if None in values else True

    result["identity_hit"] = combine([result[k] for k in required])
    # A known primary mechanism must not hide an additional unreviewed component claim.
    assertion_fields = ("root_cause", "causal_explanation", "primary_mechanism")
    assertions = [
        normalize(obj[k]) for obj in (candidate, profile) for k in assertion_fields if obj.get(k)
    ]
    detailed = any(
        obj.get(k)
        for obj in (candidate, profile, scope_profile)
        for k in (
            "component",
            "components",
            "claimed_component",
            "sub_step",
            "sub_steps",
            "claimed_sub_step",
        )
    )
    restricted = bool(root.get("proof_granularity"))
    result["proof_granularity_match"] = (
        False
        if restricted and detailed
        else None
        if restricted and any(x not in aliases for x in assertions)
        else True
    )
    full_fields = [
        *required,
        "recipe_match",
        "scope_match",
        "time_match",
        "proof_granularity_match",
    ]
    if version_required:
        full_fields.append("recipe_version_match")
    result["fully_grounded_hit"] = combine(
        [result[k] for k in full_fields] + [False if result["time_overclaim"] else True]
    )
    result["adjudication_required"] = (
        result["identity_hit"] is None or result["fully_grounded_hit"] is None
    )
    return result


def aligned_funnel(snapshots, root, all_roots):
    """Preserve operation/attempt keys; a repair cannot backfill an initial output."""
    stages = (
        "initial_raw_candidate",
        "admitted_candidate",
        "competition_survival",
        "ranking_survival",
        "confirmation_status",
    )
    grouped = {}
    summaries = {stage: [] for stage in stages}
    for snapshot in snapshots:
        stage = snapshot["stage"]
        if stage not in stages:
            continue
        observed = snapshot.get("candidates", [])
        if stage == "confirmation_status":
            gate = snapshot.get("decision_gate") or {}
            status = (
                (gate.get("confirmation_gate") or {}).get("status")
                or gate.get("confirmation_status")
                or gate.get("status")
            )
            selected = snapshot.get("selected_candidate_id")
            observed = [
                c
                for c in observed
                if status == "supported"
                and selected
                and selected in (c.get("candidate_id"), c.get("hypothesis_id"))
            ]
        decisions = [match_candidate(c, root, all_roots) for c in observed]
        field = "fully_grounded_hit" if stage == "confirmation_status" else "identity_hit"
        values = [d[field] for d in decisions]
        hit = True if True in values else None if None in values else False
        assessment = {"hit": hit, "decisions": decisions}
        op, attempt = snapshot.get("operation_id"), snapshot.get("attempt_index")
        bound = isinstance(op, str) and isinstance(attempt, int) and not isinstance(attempt, bool)
        summaries[stage].append(
            {**assessment, "operation_id": op, "attempt_index": attempt, "bound": bound}
        )
        if bound:
            current = grouped.setdefault(op, {}).setdefault(attempt, {"attempt_index": attempt})
            # Multiple observations of the same boundary remain auditable.
            if snapshot.get("attempt_kind"):
                current["attempt_kind"] = snapshot["attempt_kind"]
            current.setdefault("observations", []).append({"stage": stage, **assessment})
            current[stage] = assessment
    groups = [
        {"operation_id": op, "attempts": [attempts[k] for k in sorted(attempts)]}
        for op, attempts in grouped.items()
    ]
    result = {}
    for stage, observations in summaries.items():
        initial = [o for o in observations if o["bound"] and o["attempt_index"] == 0]
        chosen = initial if stage == "initial_raw_candidate" else observations
        result[stage] = {
            "hit": chosen[0]["hit"] if chosen else None,
            "coverage": "available"
            if chosen and all(o["bound"] for o in chosen)
            else "partial"
            if chosen
            else "missing",
            "decisions_by_generation": [o["decisions"] for o in observations],
            "hits_by_generation": [o["hit"] for o in observations],
            "observations": observations,
            "basis": "stage observations; aligned survival comparisons use candidate_funnel",
        }
    # Initial recall belongs to the first actual Candidate operation, not the first
    # later operation that happened to have a decoded initial snapshot.
    initial_recall = None
    if groups:
        first = groups[0]["attempts"]
        zero = next((a for a in first if a["attempt_index"] == 0), {})
        initial_recall = zero.get("initial_raw_candidate", {}).get("hit")
    result["initial_raw_candidate"]["hit"] = initial_recall
    return result, groups


def score_run(case, *, job_status, authority, snapshots, all_roots=()):
    result = {
        "expected_outcome": case["expected_outcome"],
        "primary_eligible": case["quality_primary_eligible"],
        "root_cause_hit": None,
        "candidate_recall": None,
        "supported_root_cause_recall": None,
        "correct_inconclusive": None,
        "correct_insufficient_evidence": None,
        "correct_terminal_decision": None,
        "funnel": {},
    }
    completed = job_status == "completed"
    root_published = bool(authority.get("root_cause"))
    expected = case["expected_outcome"]
    if expected != "supported":
        correct = (
            None
            if completed and "root_cause" not in authority
            else completed
            and not root_published
            and authority.get("confirmation_status") == expected
        )
        result["correct_" + expected] = correct
        result["correct_terminal_decision"] = correct
        return result
    root = case["roots"][0]
    result["funnel"], result["candidate_funnel"] = aligned_funnel(snapshots, root, all_roots)
    result["candidate_recall"] = result["funnel"]["initial_raw_candidate"]["hit"]
    result["supported_root_cause_recall"] = (
        result["funnel"]["confirmation_status"]["hits_by_generation"][-1]
        if result["funnel"]["confirmation_status"].get("hits_by_generation")
        else None
    )
    if completed and "root_cause" not in authority:
        result["root_cause_hit"] = None
    elif not completed or not root_published or authority.get("confirmation_status") != "supported":
        result["root_cause_hit"] = False
    else:
        # Match publication to captured candidate using the explicit authority
        # ID, not list position.
        published_id = authority.get("root_cause_candidate_id") or authority.get(
            "source_hypothesis_id"
        )
        captured = [
            c
            for s in snapshots
            if s["stage"] == "ranking_survival"
            for c in s.get("candidates", [])
            if published_id and published_id in (c.get("candidate_id"), c.get("hypothesis_id"))
        ]
        candidate = captured[-1] if captured else {"root_cause": authority["root_cause"]}
        decision = match_candidate(candidate, root, all_roots)
        result["publication_match"] = decision
        result["root_cause_hit"] = decision["fully_grounded_hit"]
    result["correct_terminal_decision"] = result["root_cause_hit"]
    return result


def quality_rates(scores):
    primary = [s for s in scores if s["primary_eligible"]]
    result = {}
    for field, expected in (
        ("root_cause_hit", "supported"),
        ("candidate_recall", "supported"),
        ("supported_root_cause_recall", "supported"),
        ("correct_inconclusive", "inconclusive"),
        ("correct_insufficient_evidence", "insufficient_evidence"),
        ("correct_terminal_decision", None),
    ):
        eligible = [s for s in primary if expected is None or s["expected_outcome"] == expected]
        known = [s[field] for s in eligible if s[field] is not None]
        result[field] = {
            "numerator": sum(known),
            "denominator": len(eligible),
            "unknown": len(eligible) - len(known),
            "value": sum(known) / len(eligible)
            if eligible and len(known) == len(eligible)
            else None,
        }
    return result


def canonicalize_funnel(case, snapshots, all_roots=()):
    """Evaluator-side linkage; never sent back to the model/runtime."""
    result = []
    for snapshot in snapshots:
        items = []
        for candidate in snapshot.get("candidates", []):
            decisions = [
                (root, match_candidate(candidate, root, all_roots)) for root in case["roots"]
            ]
            matched = [
                root["mechanism_id"]
                for root, decision in decisions
                if decision["mechanism_match"] is True
            ]
            items.append(
                {
                    **candidate,
                    "canonical_mechanism_ids": matched,
                    "canonical_identity_status": "mapped" if matched else "requires_adjudication",
                    "dimension_decisions": [d for _, d in decisions],
                }
            )
        result.append({**snapshot, "candidates": items})
    return result
