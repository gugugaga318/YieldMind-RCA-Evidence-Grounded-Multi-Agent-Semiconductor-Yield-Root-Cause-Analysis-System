from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from yield_rca_core.models import ModelValidationError
from yield_rca_core.spc_engine import (
    calculate_imr,
    calculate_p_chart,
    calculate_xbar,
    evaluate_nelson_rules,
)
from yield_rca_core.spc_models import SpcChartResult, SpcSample


def samples(values: list[float], *, prefix: str = "S") -> list[SpcSample]:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    return [
        SpcSample(
            sample_id=f"{prefix}{index:03d}",
            lot_id=f"LOT_A_{index:03d}",
            timestamp=(start + timedelta(minutes=index)).isoformat(),
            value=value,
        )
        for index, value in enumerate(values, start=1)
    ]


class SpcEngineTest(unittest.TestCase):
    def test_all_nelson_rules_are_detected(self) -> None:
        scenarios = {
            "NELSON_1": [0.1, 3.5],
            "NELSON_2": [0.4] * 9,
            "NELSON_3": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            "NELSON_4": [1.0, -1.0] * 7,
            "NELSON_5": [2.4, 2.2, 0.1],
            "NELSON_6": [1.3, 1.4, 1.2, 1.5, 0.1],
            "NELSON_7": [0.2, -0.3, 0.4] * 5,
            "NELSON_8": [1.5, -1.5] * 4,
        }
        for expected, values in scenarios.items():
            with self.subTest(rule=expected):
                violations = evaluate_nelson_rules(
                    samples(values),
                    center_line=0.0,
                    sigma=1.0,
                    parameter_name="test_parameter",
                )
                self.assertIn(expected, {item.rule_code for item in violations})

    def test_imr_calculates_limits_capability_and_round_trip(self) -> None:
        baseline = samples([100 + ((index % 5) - 2) * 0.4 for index in range(25)])
        analysis = samples([100.1, 100.2, 104.0], prefix="A")

        result = calculate_imr(
            baseline,
            analysis,
            parameter_name="pressure",
            unit="psi",
            spec_lower=95.0,
            spec_upper=105.0,
        )

        self.assertEqual(result.chart_type, "I_MR")
        self.assertEqual(result.status, "OOC")
        self.assertIn("NELSON_1", {item.rule_code for item in result.violations})
        assert result.capability is not None
        self.assertFalse(result.capability.valid_for_decision)
        self.assertEqual(SpcChartResult.from_dict(result.to_dict()), result)

    def test_xbar_s_and_xbar_r_support_equal_subgroups(self) -> None:
        baseline: list[SpcSample] = []
        analysis: list[SpcSample] = []
        start = datetime(2026, 6, 1, tzinfo=UTC)
        for group in range(10):
            for item in range(5):
                baseline.append(
                    SpcSample(
                        sample_id=f"B{group}-{item}",
                        lot_id=f"LOT_B_{group:03d}",
                        subgroup_id=f"B{group}",
                        timestamp=(start + timedelta(hours=group, minutes=item)).isoformat(),
                        value=100 + (item - 2) * 0.2 + (group % 2) * 0.05,
                    )
                )
        for group, center in enumerate((100.0, 103.0)):
            for item in range(5):
                analysis.append(
                    SpcSample(
                        sample_id=f"A{group}-{item}",
                        lot_id=f"LOT_A_{group:03d}",
                        subgroup_id=f"A{group}",
                        timestamp=(
                            start + timedelta(days=40, hours=group, minutes=item)
                        ).isoformat(),
                        value=center + (item - 2) * 0.2,
                    )
                )

        for chart_type in ("XBAR_S", "XBAR_R"):
            with self.subTest(chart_type=chart_type):
                result = calculate_xbar(
                    baseline,
                    analysis,
                    parameter_name="removal_rate",
                    unit="nm/min",
                    chart_type=chart_type,
                    spec_lower=95.0,
                    spec_upper=105.0,
                )
                self.assertEqual(result.chart_type, chart_type)
                self.assertEqual(result.secondary_chart["subgroup_size"], 5)
                self.assertEqual(result.analysis_sample_count, 10)

    def test_p_chart_uses_variable_subgroup_limits(self) -> None:
        baseline = [
            SpcSample(
                sample_id=f"P{index}",
                lot_id=f"LOT_B_{index:03d}",
                timestamp=f"2026-06-{index:02d}T00:00:00+00:00",
                value=0.04,
                sample_size=25,
                defect_count=1,
            )
            for index in range(1, 21)
        ]
        analysis = [
            SpcSample(
                sample_id="PA1",
                lot_id="LOT_A_001",
                timestamp="2026-07-01T00:00:00+00:00",
                value=0.6,
                sample_size=25,
                defect_count=15,
            )
        ]

        result = calculate_p_chart(baseline, analysis)

        self.assertEqual(result.chart_type, "P")
        self.assertEqual(result.status, "OOC")
        self.assertEqual(result.series[0]["value"], 0.6)

    def test_zero_variation_baseline_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            calculate_imr(
                samples([1.0] * 20),
                samples([1.0], prefix="A"),
                parameter_name="constant",
                unit="u",
            )


if __name__ == "__main__":
    unittest.main()
