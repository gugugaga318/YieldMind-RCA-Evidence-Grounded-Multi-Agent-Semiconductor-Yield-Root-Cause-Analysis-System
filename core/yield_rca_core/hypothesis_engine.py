"""Deterministic, evidence-bounded hypothesis engine used in shadow mode.

This module deliberately has no repository, Tool, LLM, report, or workflow
dependency. It is the sole production RCA decision engine after Batch 19.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from yield_rca_core.models import (
    AgentFinding,
    AgentKind,
    FindingKind,
    ModelValidationError,
)

_SIGNATURE_RULES = (
    ("slurry_flow", "slurry delivery degradation"),
    ("carrier_pressure", "carrier pressure instability"),
    ("wf6_flow", "WF6 delivery degradation"),
    ("deposition_rate", "deposition rate excursion"),
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _findings_by_kind(findings: list[AgentFinding]) -> dict[str, AgentFinding]:
    result: dict[str, AgentFinding] = {}
    for finding in findings:
        key = finding.finding_kind
        # MES, FDC, and Defect/WAT intentionally share the generic Specialist
        # observation kind.  Only stage-specific findings are unique here.
        if key == FindingKind.SPECIALIST_OBSERVATION.value:
            continue
        if key in result:
            raise ModelValidationError(f"duplicate finding_kind for HypothesisEngine: {key}")
        result[key] = finding
    return result


def _specialist(findings: list[AgentFinding], agent: str) -> AgentFinding | None:
    return next((finding for finding in findings if finding.agent == agent), None)


def _compact(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def _parameter_tokens(parameter: str) -> set[str]:
    ignored = {
        "bias",
        "drop",
        "error",
        "index",
        "motor",
        "parameter",
        "proxy",
        "rate",
        "signal",
        "speed",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", parameter.lower())
        if len(token) >= 3 and token not in ignored
    }


def _operationally_aligned_knowledge_candidate(
    mes: AgentFinding | None,
    fdc: AgentFinding | None,
    discovery: AgentFinding | None,
) -> str | None:
    """Bind a Knowledge hypothesis to current-Lot equipment and mechanism Evidence."""

    if mes is None or fdc is None or discovery is None:
        return None
    equipment_id = str(
        mes.details.get("target_commonality", {}).get("equipment_id", "")
    ).strip()
    if not equipment_id:
        return None
    mechanism_tokens = {
        token
        for item in fdc.details.get("parameter_summary", [])
        if isinstance(item, dict)
        and abs(float(item.get("avg_delta_percent", 0.0))) >= 5.0
        for token in _parameter_tokens(str(item.get("parameter_name", "")))
    }
    if not mechanism_tokens:
        return None

    candidates: list[tuple[int, float, str]] = []
    for case in discovery.details.get("cases", []):
        if not isinstance(case, dict):
            continue
        root_cause = str(case.get("root_cause", "")).strip()
        if not root_cause or _compact(equipment_id) not in _compact(root_cause):
            continue
        overlap = mechanism_tokens & _mechanism_tokens(root_cause)
        if not overlap:
            continue
        candidates.append(
            (len(overlap), float(case.get("similarity", 0.0)), root_cause)
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[2]


def _signature_candidate(
    mes: AgentFinding | None,
    fdc: AgentFinding | None,
    discovery: AgentFinding | None,
) -> str | None:
    if mes is None or fdc is None:
        return None
    chamber = str(mes.details.get("target_commonality", {}).get("chamber_id", "")).strip()
    parameters = {
        str(item.get("parameter_name", "")): float(item.get("avg_delta_percent", 0.0))
        for item in fdc.details.get("parameter_summary", [])
        if isinstance(item, dict)
    }
    for parameter, failure_mode in _SIGNATURE_RULES:
        if chamber and parameters.get(parameter, 0.0) <= -5.0:
            return f"{chamber} {failure_mode}"
    return _operationally_aligned_knowledge_candidate(mes, fdc, discovery)


def _recipe_candidate(mes: AgentFinding | None) -> str | None:
    if mes is None or "EV_MES_RECIPE_CHANGE" not in mes.evidence_ids:
        return None
    changes = mes.details.get("recipe_changes", [])
    if not changes or not isinstance(changes[0], dict):
        return None
    recipe_id = str(changes[0].get("source_recipe_id", "")).strip()
    version = str(changes[0].get("source_recipe_version", "")).strip()
    return f"{recipe_id} {version} recipe version change" if recipe_id and version else None


def _mechanism_tokens(root_cause: str) -> set[str]:
    ignored = {"delivery", "reduced", "flow", "on", "the", "chamber", "degradation"}
    return {
        token
        for token in re.findall(r"[a-z0-9]+", root_cause.lower())
        if len(token) >= 3 and token not in ignored
    }


def _canonical_root_cause(root_cause: str, signature_root_cause: str | None) -> str:
    """Prefer the current equipment signature over a coarser historical wording."""
    if signature_root_cause is None:
        return root_cause
    shared = _mechanism_tokens(root_cause) & _mechanism_tokens(signature_root_cause)
    return signature_root_cause if len(shared) >= 2 else root_cause


def _mes_strength(finding: AgentFinding | None) -> float:
    if finding is None:
        return 0.0
    commonality = finding.details.get("target_commonality", {})
    affected_lots = finding.details.get("affected_lots", [])
    return float(commonality.get("coverage", 0.0)) if affected_lots else 0.0


def _fdc_strength(finding: AgentFinding | None) -> float:
    if finding is None:
        return 0.0
    signals = [
        min(1.0, abs(float(item.get("avg_delta_percent", 0.0))) / 10.0)
        for item in finding.details.get("parameter_summary", [])
        if isinstance(item, dict)
    ]
    # Independent normal traces are useful exclusion Evidence; they must not
    # dilute a separate, strongly abnormal causal parameter into a false miss.
    parameter_signal = max(signals, default=0.0)
    ooc_signal = 1.0 if int(finding.details.get("event_count", 0)) > 0 else 0.0
    return 0.8 * parameter_signal + 0.2 * ooc_signal


def _defect_wat_strength(finding: AgentFinding | None) -> float:
    if finding is None:
        return 0.0
    defect_counts = finding.details.get("defect_counts", {})
    fail_modes = finding.details.get("wat_fail_modes", {})
    defect_signal = any(int(value) > 0 for value in defect_counts.values())
    wat_signal = any(int(value) > 0 for value in fail_modes.values())
    metrology_signal = int(finding.details.get("metrology_fail_count", 0)) > 0
    if metrology_signal:
        return 1.0
    return (float(defect_signal) + float(wat_signal) + float(defect_signal and wat_signal)) / 3


def _has_conflicting_physics(
    mes: AgentFinding | None,
    fdc: AgentFinding | None,
) -> bool:
    if fdc is None:
        return False
    parameters = {
        str(item.get("parameter_name", "")): float(item.get("avg_delta_percent", 0.0))
        for item in fdc.details.get("parameter_summary", [])
        if isinstance(item, dict)
    }
    if (
        parameters.get("slurry_flow", 0.0) <= -5.0
        and parameters.get("estimated_removal_rate", 0.0) >= 5.0
    ):
        return True
    abnormal_tokens = {
        token
        for parameter, delta in parameters.items()
        if abs(delta) >= 5.0
        for token in _parameter_tokens(parameter)
    }
    normal_tokens: set[str] = set()
    if mes is not None:
        normal_tokens = {
            token
            for evidence in mes.evidence
            if evidence.evidence_type == "negative_signal"
            # Later recovery and passing-control observations describe containment
            # after the excursion.  They support the time-bounded causal story and
            # must not be treated as simultaneous physics that contradicts it.
            and not evidence.evidence_id.startswith(
                ("EV_MES_RECOVERY_CONTROLS", "EV_WAT_PASSING_CONTROLS")
            )
            for entity in evidence.entities
            if entity.entity_type == "parameter"
            for token in _parameter_tokens(entity.entity_id)
        }
    return bool(abnormal_tokens & normal_tokens)


def _candidate_payload(
    *,
    hypothesis_id: str,
    root_cause: str,
    basis: str,
    base_score: float,
    core_evidence_ids: list[str],
    non_supporting_evidence_ids: list[str],
    discovery: AgentFinding | None,
    validation: AgentFinding | None,
    signature_root_cause: str | None,
) -> dict[str, Any]:
    supporting = (
        [
            evidence_id
            for evidence_id in core_evidence_ids
            if evidence_id not in non_supporting_evidence_ids
        ]
        if root_cause == signature_root_cause
        else []
    )
    if basis == "recipe_change":
        supporting.extend(core_evidence_ids)
    neutral: list[str] = (
        list(non_supporting_evidence_ids)
        if root_cause == signature_root_cause
        else []
    )
    contradicting: list[str] = []
    validation_results = []

    if discovery is not None:
        for case in discovery.details.get("cases", []):
            if isinstance(case, dict) and str(case.get("root_cause", "")).strip() == root_cause:
                supporting.extend(discovery.evidence_ids)
                base_score = max(base_score, float(case.get("similarity", 0.0)))

    if validation is not None:
        for result in validation.details.get("validation_results", []):
            if (
                not isinstance(result, dict)
                or str(result.get("root_cause", "")).strip() != root_cause
            ):
                continue
            result_evidence = [str(item) for item in result.get("evidence_ids", [])]
            outcome = str(result.get("validation", "data_missing"))
            validation_results.append(
                {
                    "outcome": outcome,
                    "knowledge_case_id": str(result.get("knowledge_case_id", "")),
                    "evidence_ids": result_evidence,
                }
            )
            if outcome == "supporting":
                supporting.extend(result_evidence)
                base_score = min(1.0, base_score + 0.05)
            elif outcome == "contradicting":
                contradicting.extend(result_evidence)
                base_score = max(0.0, base_score - 0.20)
            else:
                neutral.extend(result_evidence)

    supporting = _unique(supporting)
    contradicting = _unique(contradicting)
    neutral = _unique(neutral)
    evidence_ids = _unique(supporting + contradicting + neutral)
    confidence = round(min(0.95, max(0.0, base_score)), 3)
    status = "conflicted" if contradicting else ("supported" if confidence >= 0.75 else "candidate")
    return {
        "hypothesis_id": hypothesis_id,
        "root_cause": root_cause,
        "basis": basis,
        "supporting_evidence_ids": supporting,
        "contradicting_evidence_ids": contradicting,
        "neutral_evidence_ids": neutral,
        "evidence_ids": evidence_ids,
        "validation_results": validation_results,
        "confidence": confidence,
        "status": status,
        "rejection_reasons": [
            "Explicit normal/exclusion evidence contradicts this hypothesis."
        ]
        if contradicting
        else [],
    }


@dataclass(frozen=True)
class HypothesisEngine:
    """Generate, validate, rank, and gate deterministic RCA hypotheses."""

    supported_threshold: float = 0.75
    maximum_confidence: float = 0.95

    def __post_init__(self) -> None:
        if not 0.0 <= self.supported_threshold <= 1.0:
            raise ModelValidationError(
                "HypothesisEngine supported_threshold must be between 0 and 1"
            )
        if not self.supported_threshold <= self.maximum_confidence <= 1.0:
            raise ModelValidationError("HypothesisEngine maximum_confidence is invalid")

    def analyze(
        self,
        *,
        request_id: str,
        findings: list[AgentFinding],
        mode: str = "shadow",
    ) -> dict[str, Any]:
        """Return a JSON-safe deterministic hypothesis decision result."""
        by_kind = _findings_by_kind(findings)
        mes = _specialist(findings, AgentKind.MES.value)
        fdc = _specialist(findings, AgentKind.FDC.value)
        defect_wat = _specialist(findings, AgentKind.DEFECT_WAT.value)
        discovery = by_kind.get(FindingKind.KNOWLEDGE_DISCOVERY.value)
        validation = by_kind.get(FindingKind.KNOWLEDGE_VALIDATION.value)
        signature_root_cause = _signature_candidate(mes, fdc, discovery)
        core_evidence_ids = _unique(
            [
                evidence_id
                for finding in (mes, fdc, defect_wat)
                if finding is not None
                for evidence_id in finding.evidence_ids
            ]
        )
        non_supporting_evidence_ids = _unique(
            [
                evidence.evidence_id
                for finding in (mes, fdc, defect_wat)
                if finding is not None
                for evidence in finding.evidence
                if evidence.evidence_type in {"data_missing", "negative_signal"}
            ]
        )
        candidates: dict[str, dict[str, Any]] = {}

        def add(root_cause: str | None, basis: str, score: float) -> None:
            if not root_cause:
                return
            candidate = _candidate_payload(
                hypothesis_id=f"{request_id}:shadow:{len(candidates) + 1}",
                root_cause=root_cause,
                basis=basis,
                base_score=score,
                core_evidence_ids=core_evidence_ids,
                non_supporting_evidence_ids=non_supporting_evidence_ids,
                discovery=discovery,
                validation=validation,
                signature_root_cause=signature_root_cause,
            )
            existing = candidates.get(root_cause)
            if existing is None or candidate["confidence"] > existing["confidence"]:
                candidates[root_cause] = candidate

        add(signature_root_cause, "equipment_signature", 0.95)
        add(_recipe_candidate(mes), "recipe_change", 0.80)
        if discovery is not None:
            for case in discovery.details.get("cases", []):
                if isinstance(case, dict):
                    raw_root_cause = str(case.get("root_cause", "")).strip()
                    add(
                        _canonical_root_cause(raw_root_cause, signature_root_cause) or None,
                        "knowledge_discovery",
                        float(case.get("similarity", 0.0)),
                    )
        if validation is not None:
            for candidate in validation.details.get("preliminary_candidates", []):
                if isinstance(candidate, dict):
                    raw_root_cause = str(candidate.get("root_cause", "")).strip()
                    add(
                        _canonical_root_cause(raw_root_cause, signature_root_cause) or None,
                        "legacy_preliminary_candidate",
                        float(candidate.get("score", 0.0)),
                    )

        ranked = sorted(
            candidates.values(),
            key=lambda item: (-float(item["confidence"]), str(item["root_cause"])),
        )[:3]
        for rank, candidate in enumerate(ranked, start=1):
            candidate["rank"] = rank
        selected = ranked[0] if ranked else None
        mes_strength = _mes_strength(mes)
        fdc_strength = _fdc_strength(fdc)
        defect_wat_strength = _defect_wat_strength(defect_wat)
        equipment_signature_supported = (
            selected is not None
            and selected["root_cause"] == signature_root_cause
            and mes_strength >= 0.8
            and fdc_strength >= 0.6
            and defect_wat_strength >= 0.6
        )
        recipe_supported = (
            selected is not None
            and selected["basis"] == "recipe_change"
            and mes_strength >= 0.8
            and defect_wat_strength >= 0.6
        )
        supported = (
            selected is not None
            and selected["status"] == "supported"
            and not _has_conflicting_physics(mes, fdc)
            and (equipment_signature_supported or recipe_supported)
        )
        decision = {
            "status": "supported" if supported else "inconclusive",
            "root_cause": (
                selected["root_cause"] if supported and selected is not None else "inconclusive"
            ),
            "confidence": selected["confidence"] if supported and selected is not None else 0.0,
            "reasons": (
                ["Top hypothesis passed the deterministic evidence and contradiction gates."]
                if supported
                else ["No ranked hypothesis passed the deterministic decision gate."]
            ),
            "conflicting_physics": _has_conflicting_physics(mes, fdc),
        }
        return {
            "engine": "hypothesis_v1",
            "mode": mode,
            "candidates": ranked,
            "decision_gate": decision,
            "input": {
                "finding_kinds": [finding.finding_kind for finding in findings],
                "typed_evidence_ids": _unique(
                    [evidence_id for finding in findings for evidence_id in finding.evidence_ids]
                ),
                "knowledge_validation_present": validation is not None,
            },
        }
