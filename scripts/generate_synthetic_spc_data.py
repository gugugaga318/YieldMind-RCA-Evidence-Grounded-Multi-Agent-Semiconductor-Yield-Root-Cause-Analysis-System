"""Generate an offline SPC evidence dataset derived from the multi-case Fab model."""

from __future__ import annotations

import argparse
import copy
import random
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from generate_synthetic_fab_data import build_static_rows, iso, write_csv, write_json
from generate_synthetic_multi_case_data import (
    DEFAULT_SEED,
    WAFERS_PER_LOT,
    _catalog,
    _dynamic_rows,
    _hold_row,
    _validate_dataset,
    lot_id,
    wafer_id,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "seeds" / "spc_case"
BASELINE_LOT_START = 76
BASELINE_LOT_END = 105
TEMPLATE_LOT_NO = 16
EXCURSION_ID = "SPC_EXCURSION_CU_001"
EVENT_KEY = "SPC_OOC_CU_001"

PARAMETERS = {
    "slurry_flow": (150.0, 0.65, "ml/min", 130.0, 170.0),
    "endpoint_time": (90.0, 0.35, "s", 84.0, 98.0),
    "estimated_removal_rate": (500.0, 2.2, "nm/min", 475.0, 525.0),
    "head_pressure": (3.0, 0.025, "psi", 2.8, 3.2),
    "platen_speed": (60.0, 0.18, "rpm", 58.0, 62.0),
    "motor_current": (12.0, 0.08, "A", 10.5, 13.5),
    "post_cmp_thickness": (820.0, 3.0, "nm", 790.0, 850.0),
}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _shift_timestamps(row: dict[str, Any], delta: timedelta) -> dict[str, Any]:
    shifted = copy.deepcopy(row)
    for key, value in list(shifted.items()):
        if not value or not isinstance(value, str):
            continue
        if key.endswith("_at") or key in {"started_at", "ended_at"}:
            try:
                shifted[key] = iso(_parse(value) + delta)
            except ValueError:
                pass
    return shifted


def _clone_baseline_lots(
    rows: dict[str, list[dict[str, Any]]],
    *,
    rng: random.Random,
) -> None:
    template_lot_id = lot_id(TEMPLATE_LOT_NO)
    template_lot = next(row for row in rows["lot_master"] if row["lot_id"] == template_lot_id)
    template_created = _parse(str(template_lot["created_at"]))
    template_wafer_rows = [row for row in rows["wafer_master"] if row["lot_id"] == template_lot_id]
    template_process_rows = [
        row for row in rows["process_history"] if row["lot_id"] == template_lot_id
    ]
    template_recipe_rows = [
        row for row in rows["recipe_history"] if row["lot_id"] == template_lot_id
    ]
    template_wat_rows = [row for row in rows["wat_result"] if row["lot_id"] == template_lot_id]

    baseline_lot_count = BASELINE_LOT_END - BASELINE_LOT_START + 1
    raw_slurry_offsets = [rng.gauss(0, 1) for _ in range(baseline_lot_count)]
    raw_center = sum(raw_slurry_offsets) / len(raw_slurry_offsets)
    centered_offsets = [value - raw_center for value in raw_slurry_offsets]
    raw_mr_bar = sum(
        abs(right - left) for left, right in pairwise(centered_offsets)
    ) / (len(centered_offsets) - 1)
    desired_mr_bar = 5.8 * 1.128
    slurry_lot_offsets = [value * desired_mr_bar / raw_mr_bar for value in centered_offsets]

    for offset, baseline_lot_no in enumerate(range(BASELINE_LOT_START, BASELINE_LOT_END + 1)):
        target_lot_id = lot_id(baseline_lot_no)
        target_created = datetime(2026, 6, 1, 0, tzinfo=UTC) + timedelta(hours=18 * offset)
        delta = target_created - template_created
        lot_row = _shift_timestamps(template_lot, delta)
        lot_row["lot_id"] = target_lot_id
        rows["lot_master"].append(lot_row)

        for source in template_wafer_rows:
            cloned = copy.deepcopy(source)
            cloned["lot_id"] = target_lot_id
            cloned["wafer_id"] = wafer_id(baseline_lot_no, int(source["wafer_no"]))
            rows["wafer_master"].append(cloned)

        process_end_by_wafer: dict[int, datetime] = {}
        for source in template_process_rows:
            cloned = _shift_timestamps(source, delta)
            wafer_no = int(str(source["wafer_id"]).rsplit("W", maxsplit=1)[1])
            cloned["lot_id"] = target_lot_id
            cloned["wafer_id"] = wafer_id(baseline_lot_no, wafer_no)
            if cloned["operation_no"] == "6400":
                cloned["equipment_id"] = "CMP_CU03"
                cloned["chamber_id"] = "CMP_CU03_CH02"
                process_end_by_wafer[wafer_no] = _parse(str(cloned["ended_at"]))
            rows["process_history"].append(cloned)

        for source in template_recipe_rows:
            cloned = _shift_timestamps(source, delta)
            wafer_no = int(str(source["wafer_id"]).rsplit("W", maxsplit=1)[1])
            cloned["lot_id"] = target_lot_id
            cloned["wafer_id"] = wafer_id(baseline_lot_no, wafer_no)
            if cloned["operation_no"] == "6400":
                cloned["equipment_id"] = "CMP_CU03"
                cloned["chamber_id"] = "CMP_CU03_CH02"
            rows["recipe_history"].append(cloned)

        for source in template_wat_rows:
            cloned = _shift_timestamps(source, delta)
            wafer_no = int(str(source["wafer_id"]).rsplit("W", maxsplit=1)[1])
            cloned["lot_id"] = target_lot_id
            cloned["wafer_id"] = wafer_id(baseline_lot_no, wafer_no)
            background_fail = offset % 5 == 0 and wafer_no == 25
            cloned["pass_fail"] = str(not background_fail).lower()
            cloned["measured_value"] = "0.91" if background_fail else "0.12"
            cloned["fail_mode"] = "background_parametric_tail" if background_fail else ""
            rows["wat_result"].append(cloned)

        for wafer_no in range(1, WAFERS_PER_LOT + 1):
            measured_at = process_end_by_wafer[wafer_no]
            for parameter_name, (baseline, sigma, unit, _, _) in PARAMETERS.items():
                lot_offset = slurry_lot_offsets[offset] if parameter_name == "slurry_flow" else 0.0
                observed = baseline + lot_offset + rng.gauss(0, sigma)
                rows["fdc_feature"].append(
                    _fdc_row(
                        baseline_lot_no,
                        wafer_no,
                        parameter_name,
                        baseline,
                        observed,
                        unit,
                        measured_at,
                    )
                )


def _fdc_row(
    lot_no: int,
    wafer_no: int,
    parameter_name: str,
    baseline: float,
    observed: float,
    unit: str,
    measured_at: datetime,
) -> dict[str, str]:
    return {
        "lot_id": lot_id(lot_no),
        "wafer_id": wafer_id(lot_no, wafer_no),
        "operation_no": "6400",
        "equipment_id": "CMP_CU03",
        "chamber_id": "CMP_CU03_CH02",
        "recipe_id": "CU_CMP_40N",
        "recipe_version": "R18",
        "parameter_name": parameter_name,
        "baseline_value": f"{baseline:.4f}",
        "observed_value": f"{observed:.4f}",
        "delta_percent": f"{100 * (observed - baseline) / baseline:.4f}",
        "unit": unit,
        "trend_slope": "0.0000",
        "ooc_flag": "false",
        "severity": "NORMAL",
        "measured_at": iso(measured_at),
    }


def _augment_analysis_parameters(rows: dict[str, list[dict[str, Any]]], rng: random.Random) -> None:
    existing = {
        (row["lot_id"], row["wafer_id"], row["parameter_name"]) for row in rows["fdc_feature"]
    }
    process_end = {
        (row["lot_id"], row["wafer_id"]): _parse(str(row["ended_at"]))
        for row in rows["process_history"]
        if row["operation_no"] == "6400"
        and row["equipment_id"] == "CMP_CU03"
        and row["chamber_id"] == "CMP_CU03_CH02"
    }
    for lot_no in range(11, 16):
        degradation = lot_no - 10
        for wafer_no in range(1, WAFERS_PER_LOT + 1):
            values = {
                "head_pressure": 3.0 + rng.gauss(0, 0.018),
                "platen_speed": 60.0 + rng.gauss(0, 0.14),
                "motor_current": 12.0 + degradation * 0.28 + wafer_no * 0.002,
                "post_cmp_thickness": 820.0 + degradation * 8.0 + wafer_no * 0.05,
            }
            for parameter_name, observed in values.items():
                key = (lot_id(lot_no), wafer_id(lot_no, wafer_no), parameter_name)
                if key in existing:
                    continue
                baseline, _, unit, _, _ = PARAMETERS[parameter_name]
                rows["fdc_feature"].append(
                    _fdc_row(
                        lot_no,
                        wafer_no,
                        parameter_name,
                        baseline,
                        observed,
                        unit,
                        process_end[(lot_id(lot_no), wafer_id(lot_no, wafer_no))],
                    )
                )


def _add_profiles_and_excursion(rows: dict[str, list[dict[str, Any]]]) -> None:
    baseline_start = "2026-06-01T00:00:00+00:00"
    baseline_end = "2026-06-30T23:59:59+00:00"
    profile_specs = (
        ("SPC_BASE_CU_SLURRY_IMR_V1", "fdc_feature", "I_MR", "slurry_flow"),
        ("SPC_BASE_CU_ENDPOINT_IMR_V1", "fdc_feature", "I_MR", "endpoint_time"),
        ("SPC_BASE_CU_REMOVAL_XS_V1", "fdc_feature", "XBAR_S", "estimated_removal_rate"),
        ("SPC_BASE_CU_CURRENT_IMR_V1", "fdc_feature", "I_MR", "motor_current"),
        ("SPC_BASE_CU_WAT_P_V1", "wat_result", "P", "wat_fail_fraction"),
    )
    rows["spc_baseline_profile"] = []
    for baseline_id, source_table, chart_type, parameter_name in profile_specs:
        if parameter_name == "wat_fail_fraction":
            unit, spec_lower, spec_upper = "fraction", 0.0, 0.08
        else:
            _, _, unit, spec_lower, spec_upper = PARAMETERS[parameter_name]
        rows["spc_baseline_profile"].append(
            {
                "baseline_id": baseline_id,
                "source_table": source_table,
                "chart_type": chart_type,
                "product_id": "40N_SOC",
                "operation_no": "6400",
                "equipment_id": "CMP_CU03",
                "chamber_id": "CMP_CU03_CH02",
                "recipe_id": "CU_CMP_40N",
                "recipe_version": "R18",
                "parameter_name": parameter_name,
                "unit": unit,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "minimum_sample_count": "20",
                "spec_lower": f"{spec_lower:.4f}",
                "spec_upper": f"{spec_upper:.4f}",
                "status": "REFERENCE",
                "created_at": "2026-07-01T00:00:00+00:00",
            }
        )

    trigger_row = rows["ooc_event"][0]
    trigger_time = _parse(str(trigger_row["triggered_at"]))
    excursion_start = min(
        _parse(str(row["measured_at"]))
        for row in rows["fdc_feature"]
        if row["lot_id"] == "LOT_A_011"
        and row["equipment_id"] == "CMP_CU03"
        and row["chamber_id"] == "CMP_CU03_CH02"
    )
    trigger_row.update(
        {
            "event_key": EVENT_KEY,
            "event_source": "SPC",
            "trigger_lot_id": "LOT_A_015",
            "trigger_wafer_id": "",
            "trigger_hold_id": "HOLD_CU_OOC_001",
            "excursion_id": EXCURSION_ID,
            "spc_rule_codes": "NELSON_1;NELSON_5;NELSON_6",
        }
    )
    trigger_hold = next(row for row in rows["hold_history"] if row["hold_id"] == "HOLD_CU_OOC_001")
    trigger_hold.update(
        {
            "wafer_id": "",
            "hold_code": "SPC_OOC_TRIGGER_CONTAINMENT",
            "hold_reason": "Contain Trigger Lot after slurry-flow SPC OOC",
            "hold_comment": (
                "The LOT_A_015 lot-close slurry-flow point triggered SPC rules on "
                "CMP_CU03_CH02; contain the Trigger Lot and review the excursion window."
            ),
        }
    )
    rows["spc_excursion"] = [
        {
            "excursion_id": EXCURSION_ID,
            "baseline_id": "SPC_BASE_CU_SLURRY_IMR_V1",
            "product_id": "40N_SOC",
            "operation_no": "6400",
            "equipment_id": "CMP_CU03",
            "chamber_id": "CMP_CU03_CH02",
            "recipe_id": "CU_CMP_40N",
            "recipe_version": "R18",
            "parameter_name": "slurry_flow",
            "excursion_start": iso(excursion_start),
            "triggered_at": iso(trigger_time),
            "excursion_end": iso(trigger_time + timedelta(hours=8)),
            "description": (
                "Cu CMP slurry-flow SPC excursion from the first sustained shift through "
                "the trigger Lot and chamber qualification."
            ),
        }
    ]
    rows["spc_excursion_lot"] = []
    for lot_no in range(11, 15):
        hold_id = f"HOLD_CU_IMPACT_{lot_no:03d}"
        impact_hold = _hold_row(
            hold_id=hold_id,
            lot_no=lot_no,
            wafer_no=25,
            hold_type="EQUIPMENT",
            hold_code="SPC_EXCURSION_CONTAINMENT",
            reason="Contain impact Lot within Cu CMP SPC excursion window",
            comment=(
                f"{lot_id(lot_no)} did not trigger the OOC point but was processed on "
                "CMP_CU03_CH02 after the last confirmed in-control point."
            ),
            created_at=trigger_time + timedelta(minutes=2 + lot_no - 11),
            released_at=trigger_time + timedelta(hours=8),
        )
        impact_hold["wafer_id"] = ""
        rows["hold_history"].append(impact_hold)
        rows["spc_excursion_lot"].append(
            {
                "excursion_id": EXCURSION_ID,
                "lot_id": lot_id(lot_no),
                "scope_role": "IMPACT",
                "hold_id": hold_id,
                "selection_reason": (
                    "Same operation/equipment/chamber/recipe within the SPC excursion window"
                ),
                "linked_at": iso(trigger_time + timedelta(minutes=2 + lot_no - 11)),
            }
        )
    rows["spc_excursion_lot"].append(
        {
            "excursion_id": EXCURSION_ID,
            "lot_id": "LOT_A_015",
            "scope_role": "TRIGGER",
            "hold_id": "HOLD_CU_OOC_001",
            "selection_reason": "Lot containing the SPC OOC trigger measurement",
            "linked_at": iso(trigger_time + timedelta(minutes=1)),
        }
    )


def _rule_scenarios() -> list[dict[str, Any]]:
    return [
        {"scenario": "single_3_sigma", "expected_rule": "NELSON_1", "z_values": [0.1, 3.5]},
        {"scenario": "same_side", "expected_rule": "NELSON_2", "z_values": [0.4] * 9},
        {
            "scenario": "trend",
            "expected_rule": "NELSON_3",
            "z_values": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        },
        {"scenario": "alternating", "expected_rule": "NELSON_4", "z_values": [1.0, -1.0] * 7},
        {
            "scenario": "two_of_three_2_sigma",
            "expected_rule": "NELSON_5",
            "z_values": [2.4, 2.2, 0.1],
        },
        {
            "scenario": "four_of_five_1_sigma",
            "expected_rule": "NELSON_6",
            "z_values": [1.3, 1.4, 1.2, 1.5, 0.1],
        },
        {
            "scenario": "fifteen_center",
            "expected_rule": "NELSON_7",
            "z_values": [0.2, -0.3, 0.4] * 5,
        },
        {"scenario": "eight_outer", "expected_rule": "NELSON_8", "z_values": [1.5, -1.5] * 4},
    ]


def _validate_spc_dataset(rows: dict[str, list[dict[str, Any]]]) -> None:
    baseline_lots = {lot_id(number) for number in range(BASELINE_LOT_START, BASELINE_LOT_END + 1)}
    baseline_fdc = [
        row
        for row in rows["fdc_feature"]
        if row["lot_id"] in baseline_lots
        and row["equipment_id"] == "CMP_CU03"
        and row["chamber_id"] == "CMP_CU03_CH02"
    ]
    if len(baseline_fdc) != len(baseline_lots) * WAFERS_PER_LOT * len(PARAMETERS):
        raise ValueError("SPC baseline feature population is incomplete")
    trigger_rows = [row for row in rows["ooc_event"] if row.get("event_source") == "SPC"]
    if len(trigger_rows) != 1 or trigger_rows[0]["trigger_lot_id"] != "LOT_A_015":
        raise ValueError("SPC OOC must identify exactly one Trigger Lot")
    scopes = rows["spc_excursion_lot"]
    if sum(row["scope_role"] == "TRIGGER" for row in scopes) != 1:
        raise ValueError("SPC excursion must contain exactly one Trigger Lot")
    hold_ids = {row["hold_id"] for row in rows["hold_history"]}
    if any(row["hold_id"] not in hold_ids for row in scopes):
        raise ValueError("Every SPC excursion Lot must reference its Hold")


def generate_dataset(output_dir: Path, seed: int) -> None:
    static_rows = build_static_rows()
    rows, _ = _dynamic_rows(seed)
    _validate_dataset(rows, static_rows)
    rows["spc_baseline_profile"] = []
    rows["spc_excursion"] = []
    rows["spc_excursion_lot"] = []
    rng = random.Random(seed + 20)
    _clone_baseline_lots(rows, rng=rng)
    _augment_analysis_parameters(rows, rng)
    _add_profiles_and_excursion(rows)
    _validate_spc_dataset(rows)
    for table_name, table_rows in {**static_rows, **rows}.items():
        write_csv(output_dir / f"{table_name}.csv", table_rows)

    catalog = _catalog()
    payload = {
        "schema_version": "3.0",
        "seed": seed,
        "cases": catalog,
        "spc": {
            "baseline_lots": [
                lot_id(number) for number in range(BASELINE_LOT_START, BASELINE_LOT_END + 1)
            ],
            "baseline_window": ["2026-06-01", "2026-06-30"],
            "analysis_window": ["2026-07-01", "2026-07-31"],
            "trigger_lot_id": "LOT_A_015",
            "impact_lot_ids": [lot_id(number) for number in range(11, 15)],
            "excursion_id": EXCURSION_ID,
            "event_key": EVENT_KEY,
            "trigger_hold_id": "HOLD_CU_OOC_001",
            "expected_chart_types": ["I_MR", "XBAR_S", "P"],
        },
    }
    write_json(output_dir / "ground_truth.json", payload)
    write_json(output_dir / "case_catalog.json", payload)
    write_json(
        output_dir / "spc_rule_ground_truth.json",
        {"schema_version": "1.0", "scenarios": _rule_scenarios()},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate offline advanced SPC seed data.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    generate_dataset(args.output_dir, args.seed)
    print(f"Generated SPC dataset at {args.output_dir} with seed {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
