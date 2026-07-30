"""Offline evaluation harness and deterministic scenario data variants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil
from time import perf_counter
from typing import Any

from yield_rca_core.models import AgentKind, RCAState
from yield_rca_core.repositories import FabRepository, Row
from yield_rca_core.tool_layer import capture_tool_latencies
from yield_rca_core.workflow import build_workflow

EVIDENCE_ID_PATTERN = re.compile(r"\bEV_[A-Z0-9_]+\b")
TOOL_P95_LIMIT_MS = 1500.0
END_TO_END_P95_LIMIT_MS = 3000.0
CALIBRATION_ECE_LIMIT = 0.15


@dataclass(frozen=True)
class EvaluationScenario:
    scenario_id: str
    title: str
    query: str
    source_lot_id: str
    expected_status: str
    expected_root_cause: str
    confidence_min: float
    confidence_max: float
    required_evidence_ids: list[str]
    required_warning_ids: list[str]
    historical_similarity_min: float | None = None
    expected_impact_lots: list[str] | None = None
    expected_impact_wafers: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationScenario:
        scenario = cls(
            scenario_id=str(data["scenario_id"]),
            title=str(data["title"]),
            query=str(data["query"]),
            source_lot_id=str(data["source_lot_id"]),
            expected_status=str(data["expected_status"]),
            expected_root_cause=str(data["expected_root_cause"]),
            confidence_min=float(data.get("confidence_min", 0.0)),
            confidence_max=float(data.get("confidence_max", 1.0)),
            required_evidence_ids=[str(item) for item in data.get("required_evidence_ids", [])],
            required_warning_ids=[str(item) for item in data.get("required_warning_ids", [])],
            historical_similarity_min=(
                float(data["historical_similarity_min"])
                if "historical_similarity_min" in data
                else None
            ),
            expected_impact_lots=(
                [str(item) for item in data["expected_impact_lots"]]
                if "expected_impact_lots" in data
                else None
            ),
            expected_impact_wafers=(
                [str(item) for item in data["expected_impact_wafers"]]
                if "expected_impact_wafers" in data
                else None
            ),
        )
        if not scenario.scenario_id or not scenario.source_lot_id:
            raise ValueError("evaluation scenario requires scenario_id and source_lot_id")
        if scenario.expected_status not in {"supported", "inconclusive"}:
            raise ValueError("expected_status must be supported or inconclusive")
        if not 0.0 <= scenario.confidence_min <= scenario.confidence_max <= 1.0:
            raise ValueError("invalid scenario confidence range")
        if scenario.historical_similarity_min is not None and not (
            0.0 <= scenario.historical_similarity_min <= 1.0
        ):
            raise ValueError("historical_similarity_min must be between 0 and 1")
        return scenario


class ScenarioFabRepository:
    """Read-only scenario projection over an existing offline Fab repository."""

    def __init__(self, base: FabRepository, scenario_id: str) -> None:
        self.base = base
        self.scenario_id = scenario_id
        self._cache: dict[str, list[Row]] = {}

    def rows(self, table_name: str) -> list[Row]:
        if table_name not in self._cache:
            rows = [dict(row) for row in self.base.rows(table_name)]
            self._cache[table_name] = self._transform(table_name, rows)
        return [dict(row) for row in self._cache[table_name]]

    def _transform(self, table_name: str, rows: list[Row]) -> list[Row]:
        if self.scenario_id == "EVAL_RECIPE_VERSION_CHANGE":
            return self._recipe_change(table_name, rows)
        if self.scenario_id == "EVAL_SCRATCH_WAT_FAIL":
            return self._add_source_wat_failure(table_name, rows)
        if self.scenario_id == "EVAL_MES_NO_FDC":
            return self._normalize_cu_fdc(table_name, rows)
        if self.scenario_id == "EVAL_FDC_NO_YIELD":
            return self._remove_cu_yield_impact(table_name, rows)
        if self.scenario_id == "EVAL_CONFLICTING_EVIDENCE":
            return self._conflicting_cu_physics(table_name, rows)
        if self.scenario_id == "EVAL_MISSING_DATA":
            return self._remove_source_fdc(table_name, rows)
        if self.scenario_id == "EVAL_HIGH_HISTORY_MATCH":
            return self._high_history_match(table_name, rows)
        return rows

    @staticmethod
    def _set_wat_failure(row: Row) -> Row:
        if row["lot_id"] == "LOT_A_038" and row["wafer_id"] == "LOT_A_038_W07":
            return {
                **row,
                "measured_value": "12.800",
                "pass_fail": "false",
                "fail_mode": "leakage_short",
            }
        return row

    def _add_source_wat_failure(self, table_name: str, rows: list[Row]) -> list[Row]:
        if table_name == "wat_result":
            return [self._set_wat_failure(row) for row in rows]
        return rows

    def _recipe_change(self, table_name: str, rows: list[Row]) -> list[Row]:
        if table_name in {"process_history", "recipe_history"}:
            return [
                {
                    **row,
                    "recipe_version": "R19",
                }
                if row["lot_id"] == "LOT_A_038" and row["operation_no"] == "6400"
                else row
                for row in rows
            ]
        if table_name == "recipe_master":
            template = next(
                row
                for row in rows
                if row["recipe_id"] == "CU_CMP_40N" and row["recipe_version"] == "R18"
            )
            return [
                *rows,
                {
                    **template,
                    "recipe_version": "R19",
                    "recipe_name": "Cu CMP 40N evaluation change",
                    "released_at": "2026-07-09T00:00:00+00:00",
                },
            ]
        if table_name == "wat_result":
            return [self._set_wat_failure(row) for row in rows]
        if table_name == "rca_case":
            return [
                *rows,
                {
                    "case_id": "RCA_EVAL_RECIPE_001",
                    "title": "Cu CMP R19 recipe version excursion",
                    "technology": "40nm",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                    "symptom": "R19 recipe change followed by scratch and leakage short",
                    "root_cause": "CU_CMP_40N R19 recipe version change",
                    "solution": "Review R19 change, restore R18, run qualification wafers",
                    "confidence": "0.98",
                    "created_at": "2026-06-01T00:00:00+00:00",
                },
            ]
        if table_name == "knowledge_document":
            return [
                *rows,
                {
                    "document_id": "DOC_RCA_EVAL_RECIPE_001",
                    "case_id": "RCA_EVAL_RECIPE_001",
                    "document_type": "RCA_CASE",
                    "title": "Cu CMP R19 recipe version excursion",
                    "content": (
                        "R19 recipe change caused scratch and leakage. Restore R18 and qualify."
                    ),
                    "tags": "Cu CMP;recipe change;R19;leakage short",
                    "created_at": "2026-06-01T00:00:00+00:00",
                },
            ]
        return rows

    @staticmethod
    def _normalize_cu_fdc(table_name: str, rows: list[Row]) -> list[Row]:
        if table_name == "fdc_feature":
            return [
                {
                    **row,
                    "observed_value": row["baseline_value"],
                    "delta_percent": "0.0",
                    "trend_slope": "0.0",
                    "ooc_flag": "false",
                    "severity": "NORMAL",
                }
                if row["lot_id"] == "LOT_A_015" and row["operation_no"] == "6400"
                else row
                for row in rows
            ]
        if table_name == "ooc_event":
            return [row for row in rows if row["operation_no"] != "6400"]
        return rows

    @staticmethod
    def _remove_cu_yield_impact(table_name: str, rows: list[Row]) -> list[Row]:
        cu_lots = {f"LOT_A_{number:03d}" for number in range(11, 16)}
        if table_name == "wat_result":
            return [
                {**row, "measured_value": "2.100", "pass_fail": "true", "fail_mode": ""}
                if row["lot_id"] in cu_lots
                else row
                for row in rows
            ]
        if table_name == "defect_summary":
            return [
                row
                for row in rows
                if not (row["lot_id"] in cu_lots and row["defect_type"] == "cu_residue")
            ]
        return rows

    @staticmethod
    def _conflicting_cu_physics(table_name: str, rows: list[Row]) -> list[Row]:
        if table_name != "fdc_feature":
            return rows
        cu_lots = {f"LOT_A_{number:03d}" for number in range(11, 16)}
        return [
            {
                **row,
                "observed_value": "550.0",
                "delta_percent": "10.0",
                "trend_slope": "2.5",
            }
            if row["lot_id"] in cu_lots and row["parameter_name"] == "estimated_removal_rate"
            else row
            for row in rows
        ]

    @staticmethod
    def _remove_source_fdc(table_name: str, rows: list[Row]) -> list[Row]:
        if table_name == "fdc_feature":
            return [row for row in rows if row["lot_id"] != "LOT_A_038"]
        return rows

    @staticmethod
    def _high_history_match(table_name: str, rows: list[Row]) -> list[Row]:
        if table_name == "rca_case":
            return [
                *rows,
                {
                    "case_id": "RCA_EVAL_HIGH_MATCH_001",
                    "title": "Exact isolated scratch historical match",
                    "technology": "40nm",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                    "symptom": (
                        "Isolated scratch with normal slurry flow endpoint time and removal rate"
                    ),
                    "root_cause": "Transient particle event",
                    "solution": "Inspect handling path and monitor subsequent Lots",
                    "confidence": "0.99",
                    "created_at": "2026-06-15T00:00:00+00:00",
                },
            ]
        if table_name == "knowledge_document":
            return [
                *rows,
                {
                    "document_id": "DOC_RCA_EVAL_HIGH_MATCH_001",
                    "case_id": "RCA_EVAL_HIGH_MATCH_001",
                    "document_type": "RCA_CASE",
                    "title": "Exact isolated scratch historical match",
                    "content": (
                        "Historical isolated scratch case attributed to a transient particle."
                    ),
                    "tags": "Cu CMP;scratch;normal FDC;transient particle",
                    "created_at": "2026-06-15T00:00:00+00:00",
                },
            ]
        return rows


def _citation_counts(state: RCAState) -> tuple[int, int]:
    known = {item.evidence_id for item in state.evidence}
    references = [item for finding in state.findings for item in finding.evidence_ids]
    for finding in state.findings:
        for candidate in finding.details.get("ranked_candidates", []):
            references.extend(str(item) for item in candidate.get("evidence_ids", []))
    references.extend(item for hypothesis in state.hypotheses for item in hypothesis.evidence_ids)
    references.extend(item for warning in state.warnings for item in warning.evidence_ids)
    if state.report is not None:
        references.extend(state.report.cited_evidence_ids)
        references.extend(EVIDENCE_ID_PATTERN.findall(state.report.markdown))
    hallucinated = sum(reference not in known for reference in references)
    return len(references), hallucinated


def _traceability_ok(state: RCAState) -> bool:
    citation_count, hallucinated_count = _citation_counts(state)
    return bool(state.evidence) and citation_count > 0 and hallucinated_count == 0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 3)


def _latency_summary(values: list[float]) -> dict[str, int | float]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 3),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 3),
    }


def _confidence_calibration(results: list[dict[str, Any]]) -> dict[str, int | float]:
    supported = [item for item in results if item["expected_status"] == "supported"]
    if not supported:
        return {"sample_count": 0, "ece": 0.0, "brier_score": 0.0}

    observations = [
        (
            float(item["confidence"]),
            float(
                item["actual_status"] == "supported"
                and item["actual_root_cause"] == item["expected_root_cause"]
            ),
        )
        for item in supported
    ]
    bins: dict[int, list[tuple[float, float]]] = {}
    for confidence, correct in observations:
        bins.setdefault(min(9, int(confidence * 10)), []).append((confidence, correct))
    ece = sum(
        len(items)
        / len(observations)
        * abs(
            sum(confidence for confidence, _ in items) / len(items)
            - sum(correct for _, correct in items) / len(items)
        )
        for items in bins.values()
    )
    brier_score = sum((confidence - correct) ** 2 for confidence, correct in observations) / len(
        observations
    )
    return {
        "sample_count": len(observations),
        "ece": round(ece, 4),
        "brier_score": round(brier_score, 4),
    }


def evaluate_scenarios(
    base_repository: FabRepository,
    scenarios: list[EvaluationScenario],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        started = perf_counter()
        workflow = build_workflow(ScenarioFabRepository(base_repository, scenario.scenario_id))
        with capture_tool_latencies() as tool_latencies:
            state = workflow.run(
                scenario.query,
                job_id=f"EVAL_JOB_{scenario.scenario_id}",
                lot_id=scenario.source_lot_id,
            )
        duration_ms = round((perf_counter() - started) * 1000.0, 3)
        hypothesis = state.hypotheses[-1]
        rca_finding = next(
            finding for finding in state.findings if finding.agent == AgentKind.RCA_REASONING.value
        )
        ranked_candidates = [
            dict(item) for item in rca_finding.details.get("ranked_candidates", [])
        ]
        ranked_root_causes = [str(item["root_cause"]) for item in ranked_candidates]
        evidence_ids = {item.evidence_id for item in state.evidence}
        warning_ids = {item.warning_id for item in state.warnings}
        citation_count, hallucinated_citation_count = _citation_counts(state)
        checks = {
            "status": hypothesis.status == scenario.expected_status,
            "root_cause": hypothesis.root_cause == scenario.expected_root_cause,
            "confidence": scenario.confidence_min
            <= hypothesis.confidence
            <= scenario.confidence_max,
            "required_evidence": set(scenario.required_evidence_ids) <= evidence_ids,
            "required_warnings": set(scenario.required_warning_ids) <= warning_ids,
            "traceability": _traceability_ok(state),
            "no_hallucinated_citations": hallucinated_citation_count == 0,
        }
        if scenario.expected_status == "supported":
            checks["top3"] = scenario.expected_root_cause in ranked_root_causes[:3]
        if scenario.expected_impact_lots is not None:
            checks["impact_lots"] = state.impact_lots == scenario.expected_impact_lots
        if scenario.expected_impact_wafers is not None:
            checks["impact_wafers"] = state.impact_wafers == scenario.expected_impact_wafers

        knowledge = next(
            finding for finding in state.findings if finding.agent == AgentKind.KNOWLEDGE.value
        )
        historical_similarity = float(knowledge.details.get("top_case", {}).get("similarity", 0.0))
        if scenario.historical_similarity_min is not None:
            checks["historical_similarity"] = (
                historical_similarity >= scenario.historical_similarity_min
            )
        results.append(
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "passed": all(checks.values()),
                "checks": checks,
                "expected_status": scenario.expected_status,
                "actual_status": hypothesis.status,
                "expected_root_cause": scenario.expected_root_cause,
                "actual_root_cause": hypothesis.root_cause,
                "confidence": hypothesis.confidence,
                "duration_ms": duration_ms,
                "tool_latencies": list(tool_latencies),
                "ranked_candidates": ranked_candidates,
                "ranked_root_causes": ranked_root_causes,
                "evidence_ids": sorted(evidence_ids),
                "citation_count": citation_count,
                "hallucinated_citation_count": hallucinated_citation_count,
                "warning_ids": sorted(warning_ids),
                "required_warning_ids": scenario.required_warning_ids,
                "impact_lots": state.impact_lots,
                "impact_wafers": state.impact_wafers,
                "historical_similarity": historical_similarity,
            }
        )

    supported = [item for item in results if item["expected_status"] == "supported"]
    inconclusive = [item for item in results if item["expected_status"] == "inconclusive"]
    scoped = [
        item
        for item in results
        if "impact_lots" in item["checks"] or "impact_wafers" in item["checks"]
    ]
    warning_cases = [item for item in results if item["required_warning_ids"]]
    all_tool_latencies = [
        float(record["duration_ms"])
        for item in results
        for record in item["tool_latencies"]
    ]
    tool_names = sorted(
        {str(record["tool_name"]) for item in results for record in item["tool_latencies"]}
    )
    tool_latency_by_name = {
        tool_name: _latency_summary(
            [
                float(record["duration_ms"])
                for item in results
                for record in item["tool_latencies"]
                if record["tool_name"] == tool_name
            ]
        )
        for tool_name in tool_names
    }
    end_to_end_latencies = [float(item["duration_ms"]) for item in results]
    total_citations = sum(int(item["citation_count"]) for item in results)
    hallucinated_citations = sum(
        int(item["hallucinated_citation_count"]) for item in results
    )
    calibration = _confidence_calibration(results)
    metrics: dict[str, Any] = {
        "scenario_count": len(results),
        "scenario_pass_rate": _rate(sum(bool(item["passed"]) for item in results), len(results)),
        "top1_root_cause_accuracy": _rate(
            sum(
                item["actual_status"] == "supported"
                and item["actual_root_cause"] == item["expected_root_cause"]
                for item in supported
            ),
            len(supported),
        ),
        "top3_recall": _rate(
            sum(
                item["expected_root_cause"] in item["ranked_root_causes"][:3]
                for item in supported
            ),
            len(supported),
        ),
        "inconclusive_handling_rate": _rate(
            sum(item["actual_status"] == "inconclusive" for item in inconclusive),
            len(inconclusive),
        ),
        "false_positive_rate": _rate(
            sum(item["actual_status"] == "supported" for item in inconclusive),
            len(inconclusive),
        ),
        "evidence_traceability_rate": _rate(
            sum(bool(item["checks"]["traceability"]) for item in results), len(results)
        ),
        "hallucinated_citation_rate": _rate(hallucinated_citations, total_citations),
        "citation_count": total_citations,
        "hallucinated_citation_count": hallucinated_citations,
        "confidence_calibration": calibration,
        "tool_latency_ms": _latency_summary(all_tool_latencies),
        "tool_latency_by_name_ms": tool_latency_by_name,
        "end_to_end_latency_ms": _latency_summary(end_to_end_latencies),
        "scope_accuracy": _rate(
            sum(
                all(
                    value
                    for name, value in item["checks"].items()
                    if name in {"impact_lots", "impact_wafers"}
                )
                for item in scoped
            ),
            len(scoped),
        ),
        "warning_requirement_rate": _rate(
            sum(bool(item["checks"]["required_warnings"]) for item in warning_cases),
            len(warning_cases),
        ),
        # Compatibility aliases retained for existing report consumers.
        "supported_top1_accuracy": _rate(
            sum(
                item["actual_status"] == "supported"
                and item["actual_root_cause"] == item["expected_root_cause"]
                for item in supported
            ),
            len(supported),
        ),
        "inconclusive_accuracy": _rate(
            sum(item["actual_status"] == "inconclusive" for item in inconclusive),
            len(inconclusive),
        ),
        "mean_duration_ms": _latency_summary(end_to_end_latencies)["mean"],
    }
    acceptance = {
        "scenario_pass_rate": metrics["scenario_pass_rate"] >= 1.0,
        "top1_root_cause_accuracy": metrics["top1_root_cause_accuracy"] >= 1.0,
        "top3_recall": metrics["top3_recall"] >= 1.0,
        "inconclusive_handling_rate": metrics["inconclusive_handling_rate"] >= 1.0,
        "false_positive_rate": metrics["false_positive_rate"] == 0.0,
        "evidence_traceability_rate": metrics["evidence_traceability_rate"] >= 1.0,
        "hallucinated_citation_rate": metrics["hallucinated_citation_rate"] == 0.0,
        "confidence_calibration_ece": calibration["ece"] <= CALIBRATION_ECE_LIMIT,
        "tool_latency_p95": metrics["tool_latency_ms"]["p95"] <= TOOL_P95_LIMIT_MS,
        "end_to_end_latency_p95": (
            metrics["end_to_end_latency_ms"]["p95"] <= END_TO_END_P95_LIMIT_MS
        ),
        "scope_accuracy": metrics["scope_accuracy"] >= 1.0,
        "warning_requirement_rate": metrics["warning_requirement_rate"] >= 1.0,
    }
    return {
        "schema_version": "1.1",
        "passed": all(acceptance.values()),
        "acceptance": acceptance,
        "metrics": metrics,
        "results": results,
    }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def render_evaluation_report(evaluation: dict[str, Any]) -> str:
    metrics = evaluation["metrics"]
    calibration = metrics["confidence_calibration"]
    tool_latency = metrics["tool_latency_ms"]
    end_to_end_latency = metrics["end_to_end_latency_ms"]
    lines = [
        "# Yield RCA Step 14 Evaluation Report",
        "",
        f"- Overall status: **{'PASS' if evaluation['passed'] else 'FAIL'}**",
        f"- Scenarios: {metrics['scenario_count']}",
        f"- Scenario pass rate: {metrics['scenario_pass_rate']:.1%}",
        f"- Top-1 root-cause accuracy: {metrics['top1_root_cause_accuracy']:.1%}",
        f"- Top-3 recall: {metrics['top3_recall']:.1%}",
        f"- Inconclusive handling rate: {metrics['inconclusive_handling_rate']:.1%}",
        f"- False-positive rate: {metrics['false_positive_rate']:.1%}",
        f"- Evidence traceability: {metrics['evidence_traceability_rate']:.1%}",
        f"- Hallucinated citation rate: {metrics['hallucinated_citation_rate']:.1%} "
        f"({metrics['hallucinated_citation_count']}/{metrics['citation_count']})",
        f"- Confidence calibration ECE: {calibration['ece']:.4f} "
        f"(Brier: {calibration['brier_score']:.4f}, n={calibration['sample_count']})",
        f"- Tool latency: mean {tool_latency['mean']:.3f} ms, "
        f"P50 {tool_latency['p50']:.3f} ms, P95 {tool_latency['p95']:.3f} ms, "
        f"max {tool_latency['max']:.3f} ms",
        f"- End-to-end latency: mean {end_to_end_latency['mean']:.3f} ms, "
        f"P50 {end_to_end_latency['p50']:.3f} ms, "
        f"P95 {end_to_end_latency['p95']:.3f} ms, "
        f"max {end_to_end_latency['max']:.3f} ms",
        f"- Scope accuracy: {metrics['scope_accuracy']:.1%}",
        f"- Required Warning recall: {metrics['warning_requirement_rate']:.1%}",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Result | Expected | Actual | Top-3 candidates | Confidence | Runtime (ms) |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for result in evaluation["results"]:
        top3 = "<br>".join(result["ranked_root_causes"][:3]) or "None"
        lines.append(
            f"| {result['scenario_id']} | {'PASS' if result['passed'] else 'FAIL'} | "
            f"{result['expected_status']}: {result['expected_root_cause']} | "
            f"{result['actual_status']}: {result['actual_root_cause']} | "
            f"{top3} | {result['confidence']:.1%} | {result['duration_ms']:.3f} |"
        )
    lines.extend(["", "## Tool Latency By Name", ""])
    lines.extend(
        [
            "| Tool | Calls | Mean (ms) | P50 (ms) | P95 (ms) | Max (ms) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for tool_name, summary in metrics["tool_latency_by_name_ms"].items():
        lines.append(
            f"| {tool_name} | {summary['count']} | {summary['mean']:.3f} | "
            f"{summary['p50']:.3f} | {summary['p95']:.3f} | {summary['max']:.3f} |"
        )
    lines.extend(["", "## Failed Checks", ""])
    failures = [item for item in evaluation["results"] if not item["passed"]]
    if not failures:
        lines.append("No failed checks.")
    else:
        for result in failures:
            failed = [name for name, passed in result["checks"].items() if not passed]
            lines.append(f"- `{result['scenario_id']}`: {', '.join(failed)}")
    return "\n".join(lines).strip() + "\n"
