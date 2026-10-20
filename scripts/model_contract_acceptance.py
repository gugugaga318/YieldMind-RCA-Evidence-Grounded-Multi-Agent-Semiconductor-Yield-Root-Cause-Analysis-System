"""Offline post-run contract acceptance. Never selects or changes RCA authority."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from yield_rca_core.models import RCAState
from yield_rca_core.result_identity import ResultIdentity


def model_contract_acceptance_reasons(
    result: dict[str, Any],
    *,
    state: RCAState | None,
    identity: ResultIdentity,
    requested_mode: str,
    agent_mode: str,
    python_stop_is_governed: Callable[[dict[str, Any]], bool],
) -> list[str]:
    """Identity is supplied by the configured client, never by model response JSON.

    This is a contract audit, not a root-cause score. Repaired model outputs are
    permitted; accepted State remains subject to the original Python validators.
    """
    reasons: list[str] = []
    if requested_mode != "llm_react" or agent_mode != "llm":
        return ["model_contract_requires_llm_react"]
    if identity.basis != "runtime_config" or identity.provider in {None, "fake"}:
        reasons.append("actual_model_identity_missing")
    source = "qwen" if identity.provider in {"qwen", "dashscope"} else "model"
    if result.get("error") is not None or result.get("job_status") != "completed":
        reasons.append("workflow_not_completed")
    if result.get("actual_orchestration_mode") != "llm_react":
        reasons.append("orchestration_fallback")
    if result.get("fallback_reason"):
        reasons.append("orchestration_fallback_reason_present")
    if result.get("hypothesis_candidate_source") != source:
        reasons.append("hypothesis_candidate_not_model_authored")
    if result.get("hypothesis_candidate_fallback_reason"):
        reasons.append("hypothesis_candidate_fallback")
    if result.get("provider_failures") or result.get("provider_failure"):
        reasons.append("provider_failure")
    if result.get("llm_call_cap_exceeded"):
        reasons.append("llm_call_cap_exceeded")
    projector = result.get("terminal_stop_projected_by")
    proposer = result.get("planner_stop_proposed_by")
    if projector not in {None, "python_investigation_finalizer"}:
        reasons.append("terminal_stop_projection_source_invalid")
    if proposer not in {source, "python_runtime"}:
        reasons.append("planner_stop_source_invalid")
    elif proposer == "python_runtime" or projector == "python_investigation_finalizer":
        if not python_stop_is_governed(result):
            reasons.append("python_runtime_stop_not_governed")
    if result.get("terminal_question_updates_source") != "python_evidence_gate":
        reasons.append("terminal_updates_not_python_evidence_gate")
    if result.get("competition_lifecycle_valid") is not True:
        reasons.append("competition_lifecycle_invalid")
    if result.get("investigation_decision_accepted") is not True:
        reasons.append("investigation_decision_invalid")
    if state is None:
        return [*reasons, "state_unavailable"]
    try:
        RCAState.from_dict(state.to_dict())  # Existing validators remain authoritative.
        identities = state.result_identities
    except (ValueError, TypeError, KeyError):
        return [*reasons, "state_contract_invalid"]
    metadata = state.execution_metadata
    if metadata.get("model_identity") != identity.to_dict():
        reasons.append("state_identity_mismatch")
    if any(
        value["author"] == "model" and value != identity.to_dict() for value in identities.values()
    ):
        reasons.append("result_identity_mismatch")
    if not state.llm_usage or any(
        (u.provider, u.model) != (identity.provider, identity.model) for u in state.llm_usage
    ):
        reasons.append("usage_identity_mismatch")
    # Cross-check reported authorship against State, not only a caller-supplied summary.
    finding = state.authoritative_rca_finding
    generation = finding.details.get("hypothesis_candidate_generation", {}) if finding else {}
    if not isinstance(generation, dict) or generation.get("source") != source:
        reasons.append("candidate_identity_mismatch")
    for key in (
        "planner_stop_proposed_by",
        "terminal_stop_projected_by",
        "terminal_question_updates_source",
    ):
        if metadata.get(key) != result.get(key):
            reasons.append("terminal_state_mismatch")
    if state.job.status != result.get("job_status") or state.stop_reason != result.get(
        "terminal_stop_reason"
    ):
        reasons.append("terminal_state_mismatch")
    evidence_ids = {e.evidence_id for e in state.evidence}
    finding_ids = {f.finding_id for f in state.findings}
    if any(not set(f.evidence_ids).issubset(evidence_ids) for f in state.findings):
        reasons.append("invalid_evidence_reference")
    if any(
        not set(a.produced_evidence_ids).issubset(evidence_ids)
        or not set(a.produced_finding_ids).issubset(finding_ids)
        for a in state.action_history
    ):
        reasons.append("invalid_tool_action_reference")
    origins = metadata.get("planner_origins", {})
    if not isinstance(origins, dict):
        reasons.append("planner_authority_invalid")
        origins = {}
    if any(
        not isinstance(o, dict)
        or o.get("decision") not in {source, "python_runtime"}
        or o.get("question_updates") not in {None, "python_evidence_gate"}
        for o in origins.values()
    ):
        reasons.append("planner_authority_invalid")
    # Audit the existing fact stream; no raw response schema validation is added here.
    telemetry = metadata.get("model_telemetry", {})
    if not isinstance(telemetry, dict):
        return list(dict.fromkeys([*reasons, "model_telemetry_incomplete"]))
    events = telemetry.get("events", [])
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        return list(dict.fromkeys([*reasons, "model_telemetry_incomplete"]))
    if any(
        not isinstance(e.get("logical_call_id"), str)
        or not e["logical_call_id"]
        or e.get("kind") not in {"logical_call", "http_attempt", "model_result"}
        or (
            e.get("kind") == "model_result"
            and e.get("outcome") not in {"success", "failed", "invalid_json"}
        )
        or (e.get("kind") == "http_attempt" and type(e.get("http_attempt")) is not int)
        for e in events
    ):
        return list(dict.fromkeys([*reasons, "model_response_ledger_invalid"]))
    if not events or telemetry.get("complete") is not True or telemetry.get("dropped_count") != 0:
        reasons.append("model_telemetry_incomplete")
    elif any(
        (e.get("provider"), e.get("model")) != (identity.provider, identity.model) for e in events
    ):
        reasons.append("telemetry_identity_mismatch")
    else:
        logical = Counter(
            e.get("logical_call_id") for e in events if e.get("kind") == "logical_call"
        )
        finished = Counter(
            e.get("logical_call_id") for e in events if e.get("kind") == "model_result"
        )
        if (
            not logical
            or None in logical
            or "" in logical
            or logical != finished
            or any(n != 1 for n in logical.values())
            or set(logical) != {u.call_id for u in state.llm_usage}
            or any(e["logical_call_id"] not in logical for e in events)
        ):
            reasons.append("model_response_ledger_invalid")
        if any(
            e.get("outcome") == "failed" and e.get("error_category") != "invalid_response"
            for e in events
            if e.get("kind") == "model_result"
        ):
            reasons.append("provider_failure")
        for call_id in logical:
            attempts = [
                e.get("http_attempt")
                for e in events
                if e.get("kind") == "http_attempt" and e.get("logical_call_id") == call_id
            ]
            if not attempts or attempts != list(range(1, len(attempts) + 1)):
                reasons.append("http_attempt_ledger_invalid")
    return list(dict.fromkeys(reasons))
