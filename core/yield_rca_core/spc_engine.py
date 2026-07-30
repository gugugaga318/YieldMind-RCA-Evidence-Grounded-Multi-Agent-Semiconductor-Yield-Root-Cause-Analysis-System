"""Deterministic SPC chart calculations and Nelson rule evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from itertools import pairwise
from math import gamma, sqrt
from statistics import mean, stdev

from yield_rca_core.models import ModelValidationError
from yield_rca_core.spc_models import (
    SpcCapabilityResult,
    SpcChartResult,
    SpcChartType,
    SpcRuleViolation,
    SpcSample,
)

D2_MOVING_RANGE = 1.128
D4_MOVING_RANGE = 3.267
XBAR_S_CONSTANTS = {
    2: (2.659, 0.0, 3.267),
    3: (1.954, 0.0, 2.568),
    4: (1.628, 0.0, 2.266),
    5: (1.427, 0.0, 2.089),
    6: (1.287, 0.03, 1.97),
    7: (1.182, 0.118, 1.882),
    8: (1.099, 0.185, 1.815),
    9: (1.032, 0.239, 1.761),
    10: (0.975, 0.284, 1.716),
    15: (0.789, 0.428, 1.572),
    20: (0.68, 0.51, 1.49),
    25: (0.606, 0.565, 1.435),
}
XBAR_R_CONSTANTS = {
    2: (1.88, 0.0, 3.267),
    3: (1.023, 0.0, 2.574),
    4: (0.729, 0.0, 2.282),
    5: (0.577, 0.0, 2.114),
    6: (0.483, 0.0, 2.004),
    7: (0.419, 0.076, 1.924),
    8: (0.373, 0.136, 1.864),
    9: (0.337, 0.184, 1.816),
    10: (0.308, 0.223, 1.777),
}
D2_SUBGROUP = {
    2: 1.128,
    3: 1.693,
    4: 2.059,
    5: 2.326,
    6: 2.534,
    7: 2.704,
    8: 2.847,
    9: 2.97,
    10: 3.078,
}


def _ordered(samples: list[SpcSample]) -> list[SpcSample]:
    return sorted(samples, key=lambda item: (item.timestamp, item.sample_id))


def _window_violation(
    rule_number: int,
    description: str,
    direction: str,
    samples: list[SpcSample],
    parameter_name: str,
) -> SpcRuleViolation:
    normalized = parameter_name.upper().replace(" ", "_")
    end_id = samples[-1].sample_id.upper().replace(":", "_")
    return SpcRuleViolation(
        rule_code=f"NELSON_{rule_number}",
        description=description,
        direction=direction,
        sample_ids=[item.sample_id for item in samples],
        lot_ids=list(dict.fromkeys(item.lot_id for item in samples)),
        wafer_ids=list(dict.fromkeys(item.wafer_id for item in samples if item.wafer_id)),
        start_timestamp=samples[0].timestamp,
        end_timestamp=samples[-1].timestamp,
        evidence_id=f"EV_SPC_{normalized}_NELSON_{rule_number}_{end_id}",
    )


def evaluate_nelson_rules(
    samples: list[SpcSample],
    *,
    center_line: float,
    sigma: float,
    parameter_name: str,
) -> list[SpcRuleViolation]:
    """Evaluate Nelson rules 1-8 over an ordered analysis sequence."""

    if sigma <= 0:
        raise ModelValidationError("Nelson rules require positive sigma")
    ordered = _ordered(samples)
    z = [(item.value - center_line) / sigma for item in ordered]
    violations: list[SpcRuleViolation] = []

    def add_windows(
        size: int,
        rule_number: int,
        description: str,
        predicate: Callable[[list[float]], str | None],
    ) -> None:
        for start in range(len(z) - size + 1):
            direction = predicate(z[start : start + size])
            if direction:
                violations.append(
                    _window_violation(
                        rule_number,
                        description,
                        direction,
                        ordered[start : start + size],
                        parameter_name,
                    )
                )

    add_windows(
        1,
        1,
        "One point more than 3 sigma from the center line",
        lambda values: "high" if values[0] > 3 else "low" if values[0] < -3 else None,
    )
    add_windows(
        9,
        2,
        "Nine consecutive points on the same side of the center line",
        lambda values: (
            "high" if all(v > 0 for v in values) else "low" if all(v < 0 for v in values) else None
        ),
    )

    def trend(values: list[float]) -> str | None:
        if all(left < right for left, right in pairwise(values)):
            return "increasing"
        if all(left > right for left, right in pairwise(values)):
            return "decreasing"
        return None

    add_windows(6, 3, "Six consecutive points increasing or decreasing", trend)

    def alternating(values: list[float]) -> str | None:
        differences = [right - left for left, right in pairwise(values)]
        if all(left * right < 0 for left, right in pairwise(differences)):
            return "alternating"
        return None

    add_windows(14, 4, "Fourteen consecutive points alternating direction", alternating)

    def same_zone(values: list[float], threshold: float, required: int) -> str | None:
        if sum(v > threshold for v in values) >= required:
            return "high"
        if sum(v < -threshold for v in values) >= required:
            return "low"
        return None

    add_windows(
        3,
        5,
        "Two of three consecutive points more than 2 sigma on the same side",
        lambda values: same_zone(values, 2, 2),
    )
    add_windows(
        5,
        6,
        "Four of five consecutive points more than 1 sigma on the same side",
        lambda values: same_zone(values, 1, 4),
    )
    add_windows(
        15,
        7,
        "Fifteen consecutive points within 1 sigma of the center line",
        lambda values: "center" if all(abs(v) < 1 for v in values) else None,
    )
    add_windows(
        8,
        8,
        "Eight consecutive points outside 1 sigma on both sides of the center line",
        lambda values: (
            "both_sides"
            if all(abs(v) > 1 for v in values)
            and any(v > 1 for v in values)
            and any(v < -1 for v in values)
            else None
        ),
    )
    unique: dict[tuple[str, str], SpcRuleViolation] = {}
    for violation in violations:
        unique[(violation.rule_code, violation.end_timestamp)] = violation
    return list(unique.values())


def _capability(
    analysis_values: list[float],
    *,
    within_sigma: float,
    spec_lower: float | None,
    spec_upper: float | None,
    stable: bool,
) -> SpcCapabilityResult | None:
    if spec_lower is None and spec_upper is None:
        return None
    process_mean = mean(analysis_values)
    overall_sigma = stdev(analysis_values) if len(analysis_values) >= 2 else 0.0
    if within_sigma <= 0 or overall_sigma <= 0:
        return SpcCapabilityResult(
            cp=None,
            cpk=None,
            pp=None,
            ppk=None,
            spec_lower=spec_lower,
            spec_upper=spec_upper,
            valid_for_decision=False,
            warning="Capability requires non-zero within and overall variation.",
        )

    def two_sided(sigma_value: float) -> tuple[float | None, float | None]:
        potential = (
            (spec_upper - spec_lower) / (6 * sigma_value)
            if spec_lower is not None and spec_upper is not None
            else None
        )
        distances = []
        if spec_upper is not None:
            distances.append((spec_upper - process_mean) / (3 * sigma_value))
        if spec_lower is not None:
            distances.append((process_mean - spec_lower) / (3 * sigma_value))
        return potential, min(distances) if distances else None

    cp, cpk = two_sided(within_sigma)
    pp, ppk = two_sided(overall_sigma)
    warning = None if stable else "Process is statistically unstable; capability is informational."
    return SpcCapabilityResult(
        cp=round(cp, 4) if cp is not None else None,
        cpk=round(cpk, 4) if cpk is not None else None,
        pp=round(pp, 4) if pp is not None else None,
        ppk=round(ppk, 4) if ppk is not None else None,
        spec_lower=spec_lower,
        spec_upper=spec_upper,
        valid_for_decision=stable,
        warning=warning,
    )


def calculate_imr(
    baseline: list[SpcSample],
    analysis: list[SpcSample],
    *,
    parameter_name: str,
    unit: str,
    spec_lower: float | None = None,
    spec_upper: float | None = None,
) -> SpcChartResult:
    baseline = _ordered(baseline)
    analysis = _ordered(analysis)
    if len(baseline) < 20 or not analysis:
        raise ModelValidationError("I-MR requires at least 20 baseline points and analysis data")
    baseline_values = [item.value for item in baseline]
    moving_ranges = [abs(right - left) for left, right in pairwise(baseline_values)]
    mr_bar = mean(moving_ranges)
    sigma = mr_bar / D2_MOVING_RANGE
    if sigma <= 0:
        raise ModelValidationError("I-MR baseline variation must be positive")
    center = mean(baseline_values)
    lcl = center - 3 * sigma
    ucl = center + 3 * sigma
    violations = evaluate_nelson_rules(
        analysis,
        center_line=center,
        sigma=sigma,
        parameter_name=parameter_name,
    )
    series = [
        {
            **item.to_dict(),
            "center_line": round(center, 6),
            "lower_control_limit": round(lcl, 6),
            "upper_control_limit": round(ucl, 6),
            "spec_lower": spec_lower,
            "spec_upper": spec_upper,
        }
        for item in analysis
    ]
    capability = _capability(
        [item.value for item in analysis],
        within_sigma=sigma,
        spec_lower=spec_lower,
        spec_upper=spec_upper,
        stable=not violations,
    )
    return SpcChartResult(
        chart_type=SpcChartType.I_MR.value,
        parameter_name=parameter_name,
        unit=unit,
        center_line=round(center, 6),
        lower_control_limit=round(lcl, 6),
        upper_control_limit=round(ucl, 6),
        sigma=round(sigma, 6),
        baseline_sample_count=len(baseline),
        analysis_sample_count=len(analysis),
        series=series,
        violations=violations,
        capability=capability,
        secondary_chart={
            "type": "MR",
            "center_line": round(mr_bar, 6),
            "lower_control_limit": 0.0,
            "upper_control_limit": round(D4_MOVING_RANGE * mr_bar, 6),
        },
    )


def _subgroups(samples: list[SpcSample]) -> dict[str, list[SpcSample]]:
    grouped: dict[str, list[SpcSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.subgroup_id or sample.lot_id].append(sample)
    return dict(sorted(grouped.items()))


def calculate_xbar(
    baseline: list[SpcSample],
    analysis: list[SpcSample],
    *,
    parameter_name: str,
    unit: str,
    chart_type: str,
    spec_lower: float | None = None,
    spec_upper: float | None = None,
) -> SpcChartResult:
    chart = SpcChartType(chart_type)
    if chart not in {SpcChartType.XBAR_S, SpcChartType.XBAR_R}:
        raise ModelValidationError("calculate_xbar requires XBAR_S or XBAR_R")
    baseline_groups = _subgroups(baseline)
    analysis_groups = _subgroups(analysis)
    if len(baseline_groups) < 10 or not analysis_groups:
        raise ModelValidationError("Xbar charts require 10 baseline subgroups")
    sizes = {len(items) for items in baseline_groups.values()}
    if len(sizes) != 1:
        raise ModelValidationError("Xbar baseline subgroup sizes must be equal")
    subgroup_size = sizes.pop()
    constants = (
        XBAR_S_CONSTANTS.get(subgroup_size)
        if chart == SpcChartType.XBAR_S
        else XBAR_R_CONSTANTS.get(subgroup_size)
    )
    if constants is None:
        raise ModelValidationError(f"unsupported subgroup size {subgroup_size} for {chart.value}")
    a, lower_factor, upper_factor = constants
    baseline_means = [mean(item.value for item in items) for items in baseline_groups.values()]
    if chart == SpcChartType.XBAR_S:
        spreads = [stdev(item.value for item in items) for items in baseline_groups.values()]
    else:
        spreads = [
            max(item.value for item in items) - min(item.value for item in items)
            for items in baseline_groups.values()
        ]
    spread_bar = mean(spreads)
    center = mean(baseline_means)
    lcl = center - a * spread_bar
    ucl = center + a * spread_bar
    sigma = (ucl - center) / 3
    subgroup_samples: list[SpcSample] = []
    series = []
    for subgroup_id, items in analysis_groups.items():
        ordered_items = _ordered(items)
        subgroup_mean = mean(item.value for item in ordered_items)
        representative = ordered_items[-1]
        subgroup_sample = SpcSample(
            sample_id=f"SUBGROUP:{subgroup_id}",
            lot_id=representative.lot_id,
            timestamp=representative.timestamp,
            value=subgroup_mean,
            subgroup_id=subgroup_id,
        )
        subgroup_samples.append(subgroup_sample)
        spread = (
            stdev(item.value for item in ordered_items)
            if chart == SpcChartType.XBAR_S and len(ordered_items) >= 2
            else max(item.value for item in ordered_items)
            - min(item.value for item in ordered_items)
        )
        series.append(
            {
                **subgroup_sample.to_dict(),
                "subgroup_size": len(ordered_items),
                "spread": round(spread, 6),
                "center_line": round(center, 6),
                "lower_control_limit": round(lcl, 6),
                "upper_control_limit": round(ucl, 6),
                "spec_lower": spec_lower,
                "spec_upper": spec_upper,
            }
        )
    violations = evaluate_nelson_rules(
        subgroup_samples,
        center_line=center,
        sigma=sigma,
        parameter_name=parameter_name,
    )
    if chart == SpcChartType.XBAR_S:
        c4 = (
            sqrt(2 / (subgroup_size - 1))
            * gamma(subgroup_size / 2)
            / gamma((subgroup_size - 1) / 2)
        )
        within_sigma = spread_bar / c4
    else:
        within_sigma = spread_bar / D2_SUBGROUP[subgroup_size]
    capability = _capability(
        [item.value for item in analysis],
        within_sigma=within_sigma,
        spec_lower=spec_lower,
        spec_upper=spec_upper,
        stable=not violations,
    )
    return SpcChartResult(
        chart_type=chart.value,
        parameter_name=parameter_name,
        unit=unit,
        center_line=round(center, 6),
        lower_control_limit=round(lcl, 6),
        upper_control_limit=round(ucl, 6),
        sigma=round(sigma, 6),
        baseline_sample_count=len(baseline),
        analysis_sample_count=len(analysis),
        series=series,
        violations=violations,
        capability=capability,
        secondary_chart={
            "type": "S" if chart == SpcChartType.XBAR_S else "R",
            "center_line": round(spread_bar, 6),
            "lower_control_limit": round(lower_factor * spread_bar, 6),
            "upper_control_limit": round(upper_factor * spread_bar, 6),
            "subgroup_size": subgroup_size,
        },
    )


def calculate_p_chart(
    baseline: list[SpcSample],
    analysis: list[SpcSample],
    *,
    parameter_name: str = "wat_fail_fraction",
) -> SpcChartResult:
    if len(baseline) < 20 or not analysis:
        raise ModelValidationError("p-chart requires 20 baseline subgroups")
    if any(item.sample_size is None or item.defect_count is None for item in baseline + analysis):
        raise ModelValidationError("p-chart samples require defect_count and sample_size")
    baseline_defects = sum(int(item.defect_count or 0) for item in baseline)
    baseline_total = sum(int(item.sample_size or 0) for item in baseline)
    p_bar = baseline_defects / baseline_total
    if not 0 < p_bar < 1:
        raise ModelValidationError("p-chart baseline proportion must be between zero and one")
    standardized: list[SpcSample] = []
    series = []
    for item in _ordered(analysis):
        n = int(item.sample_size or 0)
        proportion = int(item.defect_count or 0) / n
        point_sigma = sqrt(p_bar * (1 - p_bar) / n)
        standardized.append(
            SpcSample(
                sample_id=item.sample_id,
                lot_id=item.lot_id,
                timestamp=item.timestamp,
                value=(proportion - p_bar) / point_sigma,
                subgroup_id=item.subgroup_id,
            )
        )
        series.append(
            {
                **item.to_dict(),
                "value": round(proportion, 6),
                "center_line": round(p_bar, 6),
                "lower_control_limit": round(max(0.0, p_bar - 3 * point_sigma), 6),
                "upper_control_limit": round(min(1.0, p_bar + 3 * point_sigma), 6),
            }
        )
    violations = evaluate_nelson_rules(
        standardized,
        center_line=0.0,
        sigma=1.0,
        parameter_name=parameter_name,
    )
    average_n = mean(int(item.sample_size or 0) for item in baseline)
    sigma = sqrt(p_bar * (1 - p_bar) / average_n)
    return SpcChartResult(
        chart_type=SpcChartType.P.value,
        parameter_name=parameter_name,
        unit="fraction",
        center_line=round(p_bar, 6),
        lower_control_limit=round(max(0.0, p_bar - 3 * sigma), 6),
        upper_control_limit=round(min(1.0, p_bar + 3 * sigma), 6),
        sigma=round(sigma, 6),
        baseline_sample_count=len(baseline),
        analysis_sample_count=len(analysis),
        series=series,
        violations=violations,
    )
