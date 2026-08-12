"""Evaluation V2 end-to-end RCA metrics and release gates.

The benchmark truth is consumed only after a workflow run completes.  Hidden
``EV_V2_*`` labels are mapped to runtime Evidence by semantic type; they are
never placed in Planner prompts, Agent context, or Hypothesis inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from yield_rca_core.models import Evidence, RCAState, TaskStatus


@dataclass(frozen=True)
class RCAV2Scenario:
    scenario_id: str
    incident_family_id: str
    partition: str
    query: str
    source_lot_id: str
    expected_status: str
    expected_root_cause: str
    expected_causal_module: str
    expected_discovery_lane: str
    expected_impact_lots: tuple[str, ...]
    required_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    required_warning_ids: tuple[str, ...]
    unavailable_data_sources: tuple[str, ...]
    observed_module: str
    metadata_quality: str

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        family: dict[str, Any],
    ) -> RCAV2Scenario:
        observation = dict(payload.get("observation_scope", {}))
        scenario = cls(
            scenario_id=str(payload["scenario_id"]),
            incident_family_id=str(payload["incident_family_id"]),
            partition=str(payload["partition"]),
            query=str(payload["query"]),
            source_lot_id=str(payload["source_lot_id"]),
            expected_status=str(payload["expected_status"]),
            expected_root_cause=str(payload["expected_root_cause"]),
            expected_causal_module=str(payload["expected_causal_module"]),
            expected_discovery_lane=str(payload["expected_discovery_lane"]),
            expected_impact_lots=tuple(
                str(item) for item in payload.get("expected_impact_lots", [])
            ),
            required_evidence_ids=tuple(
                str(item) for item in payload.get("required_evidence_ids", [])
            ),
            contradicting_evidence_ids=tuple(
                str(item) for item in payload.get("contradicting_evidence_ids", [])
            ),
            required_warning_ids=tuple(
                str(item) for item in payload.get("required_warning_ids", [])
            ),
            unavailable_data_sources=tuple(
                str(item) for item in payload.get("unavailable_data_sources", [])
            ),
            observed_module=str(observation.get("detected_module", "")),
            metadata_quality=str(family.get("metadata_quality", "")),
        )
        if scenario.partition not in {"calibration", "test"}:
            raise ValueError("RCA V2 partition must be calibration or test")
        if scenario.expected_status not in {"supported", "inconclusive"}:
            raise ValueError("RCA V2 expected_status must be supported or inconclusive")
        return scenario

    @property
    def causal_slice(self) -> str:
        if self.expected_causal_module.casefold() == "unresolved":
            return "unresolved"
        return (
            "same_module"
            if self.observed_module.casefold() == self.expected_causal_module.casefold()
            else "cross_module"
        )


def load_scenarios(
    scenario_catalog: dict[str, Any],
    incident_catalog: dict[str, Any],
) -> list[RCAV2Scenario]:
    families = {
        str(item["incident_family_id"]): item
        for item in incident_catalog["incident_families"]
    }
    scenarios: list[RCAV2Scenario] = []
    for payload in scenario_catalog["scenarios"]:
        family_id = str(payload["incident_family_id"])
        if family_id not in families:
            raise ValueError(f"unknown Incident Family for RCA scenario: {family_id}")
        scenarios.append(RCAV2Scenario.from_dict(payload, family=families[family_id]))
    return scenarios


def evidence_type_catalog(incident_catalog: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for family in incident_catalog["incident_families"]:
        for field in ("supporting_evidence", "contradicting_evidence"):
            for item in family.get(field, []):
                evidence_id = str(item["evidence_id"])
                evidence_type = str(item["evidence_type"])
                previous = result.get(evidence_id)
                if previous is not None and previous != evidence_type:
                    raise ValueError(f"conflicting semantic type for {evidence_id}")
                result[evidence_id] = evidence_type
    return result


def _metadata_text(evidence: Evidence, key: str) -> str:
    value = evidence.metadata.get(key, "")
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value).casefold()
    return str(value).casefold()


def _is_current_fdc_normal(evidence: Evidence) -> bool:
    return (
        evidence.source_type == "fdc"
        and evidence.evidence_type == "negative_signal"
        and not evidence.evidence_id.startswith("EV_MES_RECOVERY_CONTROLS")
    )


def _matches_semantic_type(
    expected_type: str,
    evidence: Evidence,
    *,
    predicted_impact_lots: tuple[str, ...],
) -> bool:
    evidence_id = evidence.evidence_id.upper()
    stage = _metadata_text(evidence, "measurement_stage")
    test_items = _metadata_text(evidence, "test_items")
    field = str(evidence.source_field or "").casefold()

    if expected_type == "MES_PROCESS_HISTORY":
        return evidence.evidence_type in {"lot_context", "equipment_exposure"}
    if expected_type == "DEFECT_OR_METROLOGY":
        return evidence.evidence_type in {
            "defect_signal",
            "metrology_deviation",
            "electrical_failure",
        }
    if expected_type == "FDC_OR_OPERATIONAL_CONFIRMATION":
        return evidence.source_type == "fdc" and evidence.evidence_type in {
            "parameter_deviation",
            "ooc_event",
        }
    if expected_type == "METROLOGY_PRE_CMP":
        return evidence.source_table == "metrology_result" and "PRE_CMP" in evidence_id
    if expected_type == "METROLOGY_REFERENCE_CALIBRATION":
        return evidence.source_type == "fdc" and "reference" in field
    if expected_type == "INDEPENDENT_METROLOGY_REMEASURE":
        return "independent_remeasure" in stage or "INDEPENDENT_REMEASURE" in evidence_id
    if expected_type == "INDEPENDENT_METROLOGY_CONFIRMATION":
        return (
            "independent_confirmation" in stage
            or "INDEPENDENT_CONFIRMATION" in evidence_id
        )
    if expected_type == "INDEPENDENT_PROCESS_CONFIRMATION":
        return (
            evidence.source_table == "metrology_result"
            and ("confirmation" in stage or "CONFIRMATION" in evidence_id)
        )
    if expected_type == "PROCESS_FDC_NORMAL_EXCLUSION":
        return _is_current_fdc_normal(evidence)
    if expected_type in {"FDC_NORMAL_EXCLUSION", "DETECTED_STEP_FDC_NORMAL_EXCLUSION"}:
        return _is_current_fdc_normal(evidence)
    if expected_type == "WAT_NORMAL_EXCLUSION":
        return bool(
            evidence.source_type == "wat"
            and evidence.evidence_type == "negative_signal"
        )
    if expected_type == "DEFECT_RECURRENCE_CHECK":
        return evidence.evidence_id == "EV_MES_IMPACT_LOTS" and not predicted_impact_lots
    if expected_type == "CORRELATED_IMPACT_LOT_EVIDENCE":
        return evidence.evidence_id == "EV_MES_IMPACT_LOTS" and bool(
            predicted_impact_lots
        )
    if expected_type == "POST_RECOVERY_CONTROL_EVIDENCE":
        return bool(evidence.evidence_id.startswith("EV_MES_RECOVERY_CONTROLS"))
    if expected_type == "DETECTED_STEP_FDC_EXCURSION":
        return evidence.source_type == "fdc" and evidence.evidence_type in {
            "parameter_deviation",
            "excursion_window",
        }
    if expected_type == "INDEPENDENT_WAT_RETEST":
        return evidence.source_type == "wat" and "independent_retest" in test_items
    if expected_type == "EQUIPMENT_INSPECTION_CONFIRMATION":
        return evidence.source_type == "fdc" and any(
            token in field for token in ("contamination", "inspection", "particle")
        )
    if expected_type == "POST_MAINTENANCE_RECOVERY":
        return (
            evidence.source_type == "wat" and "post_clean" in test_items
        ) or (evidence.source_type == "fdc" and "post_clean" in field)
    if expected_type == "EQUIPMENT_GENEALOGY_SCOPE_AUDIT":
        return bool(evidence.evidence_id == "EV_MES_IMPACT_LOTS")
    return False


def _evidence_matches(
    state: RCAState,
    scenario: RCAV2Scenario,
    expected_types: dict[str, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    predicted = tuple(state.impact_lots)
    for evidence_id in (
        *scenario.required_evidence_ids,
        *scenario.contradicting_evidence_ids,
    ):
        semantic_type = expected_types.get(evidence_id)
        if semantic_type is None:
            raise ValueError(f"missing hidden semantic Evidence type for {evidence_id}")
        matched_ids = [
            evidence.evidence_id
            for evidence in state.evidence
            if _matches_semantic_type(
                semantic_type,
                evidence,
                predicted_impact_lots=predicted,
            )
        ]
        result.append(
            {
                "expected_evidence_id": evidence_id,
                "semantic_type": semantic_type,
                "role": (
                    "contradicting"
                    if evidence_id in scenario.contradicting_evidence_ids
                    else "supporting"
                ),
                "satisfied": bool(matched_ids),
                "matched_runtime_evidence_ids": matched_ids,
            }
        )
    return result


def _knowledge_governance(state: RCAState) -> tuple[list[str], bool]:
    unapproved = sorted(
        {
            evidence.evidence_id
            for evidence in state.evidence
            if evidence.source_type == "knowledge"
            and str(evidence.metadata.get("validation_status", "")) != "CONFIRMED"
        }
    )
    hypothesis = state.hypotheses[-1]
    evidence_by_id = {item.evidence_id: item for item in state.evidence}
    supporting_sources = {
        evidence_by_id[evidence_id].source_type
        for evidence_id in hypothesis.supporting_evidence_ids
        if evidence_id in evidence_by_id
    }
    knowledge_only = hypothesis.status == "supported" and not (
        supporting_sources - {"knowledge"}
    )
    return unapproved, knowledge_only


def evaluate_state(
    state: RCAState,
    scenario: RCAV2Scenario,
    *,
    expected_types: dict[str, str],
    requested_mode: str,
) -> dict[str, Any]:
    hypothesis = state.hypotheses[-1]
    actual_status = hypothesis.status
    status_correct = actual_status == scenario.expected_status
    root_cause_correct = (
        actual_status == "inconclusive"
        if scenario.expected_status == "inconclusive"
        else actual_status == "supported"
        and hypothesis.root_cause == scenario.expected_root_cause
    )
    predicted = set(state.impact_lots)
    expected = set(scenario.expected_impact_lots)
    true_positive = len(predicted & expected)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    evidence_matches = _evidence_matches(state, scenario, expected_types)
    supporting_matches = [
        item for item in evidence_matches if item["role"] == "supporting"
    ]
    warning_ids = {item.warning_id for item in state.warnings}
    missing_warnings = sorted(set(scenario.required_warning_ids) - warning_ids)
    unapproved, knowledge_only = _knowledge_governance(state)
    actual_mode = str(state.execution_metadata.get("orchestration_mode", ""))
    completed = state.job.status == TaskStatus.COMPLETED.value
    scenario_passed = all(
        (
            completed,
            status_correct,
            root_cause_correct,
            predicted == expected,
            all(item["satisfied"] for item in evidence_matches),
            not missing_warnings,
            not unapproved,
            not knowledge_only,
            actual_mode == requested_mode,
        )
    )
    return {
        "scenario_id": scenario.scenario_id,
        "incident_family_id": scenario.incident_family_id,
        "partition": scenario.partition,
        "causal_slice": scenario.causal_slice,
        "metadata_quality": scenario.metadata_quality,
        "expected_discovery_lane": scenario.expected_discovery_lane,
        "expected_status": scenario.expected_status,
        "actual_status": actual_status,
        "expected_root_cause": scenario.expected_root_cause,
        "actual_root_cause": hypothesis.root_cause,
        "status_correct": status_correct,
        "root_cause_correct": root_cause_correct,
        "expected_impact_lots": sorted(expected),
        "actual_impact_lots": sorted(predicted),
        "impact_true_positive": true_positive,
        "impact_false_positive": false_positive,
        "impact_false_negative": false_negative,
        "evidence_matches": evidence_matches,
        "evidence_required_count": len(supporting_matches),
        "evidence_satisfied_count": sum(
            bool(item["satisfied"]) for item in supporting_matches
        ),
        "required_warning_ids": list(scenario.required_warning_ids),
        "actual_warning_ids": sorted(warning_ids),
        "missing_warning_ids": missing_warnings,
        "unavailable_data_sources": list(scenario.unavailable_data_sources),
        "unapproved_knowledge_evidence_ids": unapproved,
        "knowledge_only_promotion": knowledge_only,
        "job_completed": completed,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "llm_call_count": int(state.execution_metadata.get("llm_call_count", 0)),
        "fallback_reason": state.execution_metadata.get("orchestration_fallback_reason"),
        "passed": scenario_passed,
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    supported = [row for row in rows if row["expected_status"] == "supported"]
    inconclusive = [row for row in rows if row["expected_status"] == "inconclusive"]
    evidence_required = sum(int(row["evidence_required_count"]) for row in rows)
    evidence_satisfied = sum(int(row["evidence_satisfied_count"]) for row in rows)
    impact_tp = sum(int(row["impact_true_positive"]) for row in rows)
    impact_fp = sum(int(row["impact_false_positive"]) for row in rows)
    impact_fn = sum(int(row["impact_false_negative"]) for row in rows)
    warning_required = sum(len(row["required_warning_ids"]) for row in rows)
    warning_satisfied = sum(
        len(row["required_warning_ids"]) - len(row["missing_warning_ids"])
        for row in rows
    )
    return {
        "scenario_count": len(rows),
        "scenario_pass_count": sum(bool(row["passed"]) for row in rows),
        "scenario_pass_rate": _safe_ratio(
            sum(bool(row["passed"]) for row in rows), len(rows)
        ),
        "root_cause_correct_count": sum(
            bool(row["root_cause_correct"]) for row in supported
        ),
        "supported_scenario_count": len(supported),
        "root_cause_correctness": _safe_ratio(
            sum(bool(row["root_cause_correct"]) for row in supported),
            len(supported),
        ),
        "evidence_satisfied_count": evidence_satisfied,
        "evidence_required_count": evidence_required,
        "evidence_completeness": _safe_ratio(evidence_satisfied, evidence_required),
        "impact_true_positive": impact_tp,
        "impact_false_positive": impact_fp,
        "impact_false_negative": impact_fn,
        "impact_lot_precision": _safe_ratio(impact_tp, impact_tp + impact_fp),
        "impact_lot_recall": _safe_ratio(impact_tp, impact_tp + impact_fn),
        "correct_abstention_count": sum(
            row["actual_status"] == "inconclusive" for row in inconclusive
        ),
        "inconclusive_scenario_count": len(inconclusive),
        "correct_abstention_rate": _safe_ratio(
            sum(row["actual_status"] == "inconclusive" for row in inconclusive),
            len(inconclusive),
        ),
        "warning_satisfied_count": warning_satisfied,
        "warning_required_count": warning_required,
        "required_warning_recall": _safe_ratio(
            warning_satisfied, warning_required
        ),
        "knowledge_only_promotion_count": sum(
            bool(row["knowledge_only_promotion"]) for row in rows
        ),
        "unapproved_knowledge_evidence_count": sum(
            len(row["unapproved_knowledge_evidence_ids"]) for row in rows
        ),
        "completed_count": sum(bool(row["job_completed"]) for row in rows),
        "completion_rate": _safe_ratio(
            sum(bool(row["job_completed"]) for row in rows), len(rows)
        ),
        "requested_mode_preserved_count": sum(
            row["requested_mode"] == row["actual_mode"] for row in rows
        ),
        "requested_mode_preservation_rate": _safe_ratio(
            sum(row["requested_mode"] == row["actual_mode"] for row in rows),
            len(rows),
        ),
        "llm_call_count": sum(int(row["llm_call_count"]) for row in rows),
    }


def _slice(rows: list[dict[str, Any]], field: str, values: tuple[str, ...]) -> dict[str, Any]:
    return {
        value: _metric_summary([row for row in rows if row[field] == value])
        for value in values
    }


def evaluate_mode(
    scenarios: list[RCAV2Scenario],
    *,
    expected_types: dict[str, str],
    requested_mode: str,
    run_scenario: Callable[[RCAV2Scenario], RCAState],
) -> dict[str, Any]:
    rows = [
        evaluate_state(
            run_scenario(scenario),
            scenario,
            expected_types=expected_types,
            requested_mode=requested_mode,
        )
        for scenario in scenarios
    ]
    partitions = {
        partition: _metric_summary(
            [row for row in rows if row["partition"] == partition]
        )
        for partition in ("calibration", "test")
    }
    test_rows = [row for row in rows if row["partition"] == "test"]
    return {
        "status": "COMPLETE",
        "requested_mode": requested_mode,
        "metrics": _metric_summary(rows),
        "partitions": partitions,
        "test_slices": {
            "causal_scope": _slice(
                test_rows,
                "causal_slice",
                ("same_module", "cross_module", "unresolved"),
            ),
            "metadata_quality": _slice(
                test_rows,
                "metadata_quality",
                ("complete", "missing", "noisy"),
            ),
            "discovery_lane": _slice(
                test_rows,
                "expected_discovery_lane",
                ("same_step", "upstream_route", "shared_resource", "global_semantic"),
            ),
        },
        "results": rows,
        "failed_scenario_ids": [row["scenario_id"] for row in rows if not row["passed"]],
    }


def controlled_compatibility(evaluation: dict[str, Any]) -> dict[str, Any]:
    rows = list(evaluation["results"])
    unsafe_false_supports = [
        row["scenario_id"]
        for row in rows
        if row["expected_status"] == "inconclusive" and row["actual_status"] == "supported"
    ]
    impact_mismatches = [
        row["scenario_id"]
        for row in rows
        if row["expected_impact_lots"] != row["actual_impact_lots"]
    ]
    metrics = evaluation["metrics"]
    passed = all(
        (
            metrics["completion_rate"] == 1.0,
            metrics["requested_mode_preservation_rate"] == 1.0,
            metrics["knowledge_only_promotion_count"] == 0,
            metrics["unapproved_knowledge_evidence_count"] == 0,
            not unsafe_false_supports,
            not impact_mismatches,
        )
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "unsafe_false_support_scenario_ids": unsafe_false_supports,
        "impact_mismatch_scenario_ids": impact_mismatches,
        "note": (
            "Controlled ReAct is a safety/compatibility baseline. Fewer Specialist "
            "actions may intentionally downgrade supported conclusions."
        ),
    }


def rca_quality_gate(
    real_qwen: dict[str, Any],
    *,
    fixed_reference: dict[str, Any],
    controlled_reference: dict[str, Any],
) -> dict[str, Any]:
    if real_qwen.get("status") != "COMPLETE":
        return {
            "status": "BLOCKED",
            "passed": False,
            "reason": str(
                real_qwen.get(
                    "reason",
                    "Real Qwen llm_react evaluation was not run.",
                )
            ),
            "fixed_reference_complete": fixed_reference.get("status") == "COMPLETE",
            "controlled_compatibility": controlled_compatibility(controlled_reference),
        }

    metrics = real_qwen["partitions"]["test"]
    criteria = {
        "all_test_scenarios_pass": metrics["scenario_pass_rate"] == 1.0,
        "root_cause_correctness": metrics["root_cause_correctness"] == 1.0,
        "evidence_completeness": metrics["evidence_completeness"] == 1.0,
        "impact_lot_precision": metrics["impact_lot_precision"] == 1.0,
        "impact_lot_recall": metrics["impact_lot_recall"] == 1.0,
        "correct_abstention_rate": metrics["correct_abstention_rate"] == 1.0,
        "required_warning_recall": metrics["required_warning_recall"] == 1.0,
        "no_knowledge_only_promotion": metrics["knowledge_only_promotion_count"] == 0,
        "no_unapproved_knowledge": metrics["unapproved_knowledge_evidence_count"] == 0,
        "no_orchestration_fallback": metrics["requested_mode_preservation_rate"] == 1.0,
    }
    passed = all(criteria.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "criteria": criteria,
        "fixed_reference_complete": fixed_reference.get("status") == "COMPLETE",
        "controlled_compatibility": controlled_compatibility(controlled_reference),
    }


def governance_gate(
    fixed_reference: dict[str, Any],
    *,
    expected_unsupported_scenarios: int,
) -> dict[str, Any]:
    rows = list(fixed_reference["results"])
    unsupported = [row for row in rows if row["unavailable_data_sources"]]
    explicit = [
        row
        for row in unsupported
        if "WARN_UNSUPPORTED_DATA_SOURCE" in row["actual_warning_ids"]
    ]
    metrics = fixed_reference["metrics"]
    passed = all(
        (
            metrics["unapproved_knowledge_evidence_count"] == 0,
            metrics["knowledge_only_promotion_count"] == 0,
            len(unsupported) == expected_unsupported_scenarios,
            len(explicit) == len(unsupported),
        )
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "unapproved_knowledge_leakage": metrics[
            "unapproved_knowledge_evidence_count"
        ],
        "historical_only_root_cause_promotions": metrics[
            "knowledge_only_promotion_count"
        ],
        "unsupported_source_scenario_count": len(unsupported),
        "unsupported_source_explicit_count": len(explicit),
        "unsupported_source_recall": _safe_ratio(len(explicit), len(unsupported)),
    }


def render_report(result: dict[str, Any]) -> str:
    fixed_test = result["modes"]["fixed"]["partitions"]["test"]
    controlled = result["gates"]["rca_quality"]["controlled_compatibility"]
    qwen = result["modes"]["llm_react"]
    lines = [
        "# Evaluation V2 End-to-End RCA Report",
        "",
        f"- Dataset: `{result['dataset_id']}` (Synthetic benchmark)",
        f"- Fixed reference: **{result['modes']['fixed']['status']}**",
        f"- Controlled compatibility: **{controlled['status']}**",
        f"- Real Qwen llm_react: **{qwen['status']}**",
        f"- RCA quality gate: **{result['gates']['rca_quality']['status']}**",
        f"- Governance gate: **{result['gates']['governance']['status']}**",
        "",
        "## Fixed deterministic reference (Test partition)",
        "",
        f"- Root Cause Correctness: {fixed_test['root_cause_correctness']:.2%} "
        f"({fixed_test['root_cause_correct_count']}/{fixed_test['supported_scenario_count']})",
        f"- Evidence Completeness: {fixed_test['evidence_completeness']:.2%} "
        f"({fixed_test['evidence_satisfied_count']}/{fixed_test['evidence_required_count']})",
        f"- Impact Lot Precision: {fixed_test['impact_lot_precision']:.2%}",
        f"- Impact Lot Recall: {fixed_test['impact_lot_recall']:.2%}",
        f"- Correct Abstention Rate: {fixed_test['correct_abstention_rate']:.2%} "
        f"({fixed_test['correct_abstention_count']}/{fixed_test['inconclusive_scenario_count']})",
        f"- Required Warning Recall: {fixed_test['required_warning_recall']:.2%}",
        "",
        "## Real Qwen boundary",
        "",
    ]
    if qwen["status"] == "NOT_RUN":
        lines.extend(
            [
                f"Real Qwen was not run: {qwen['reason']}",
                "The RCA release gate is therefore BLOCKED. Fake LLM output was not used.",
            ]
        )
    else:
        qwen_test = qwen["partitions"]["test"]
        lines.extend(
            [
                f"- Test scenario pass rate: {qwen_test['scenario_pass_rate']:.2%}",
                f"- Actual llm_react preservation: "
                f"{qwen_test['requested_mode_preservation_rate']:.2%}",
                f"- Paid LLM calls: {qwen_test['llm_call_count']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Failed scenarios",
            "",
        ]
    )
    failed = result["modes"]["fixed"]["failed_scenario_ids"]
    lines.extend([f"- `{scenario_id}`" for scenario_id in failed] or ["- None"])
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "These numbers measure a reviewed Synthetic benchmark, not confidential Fab "
            "data or production accuracy. Hidden labels are used only by the evaluator "
            "after each workflow run.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "RCAV2Scenario",
    "controlled_compatibility",
    "evidence_type_catalog",
    "evaluate_mode",
    "evaluate_state",
    "governance_gate",
    "load_scenarios",
    "rca_quality_gate",
    "render_report",
]
