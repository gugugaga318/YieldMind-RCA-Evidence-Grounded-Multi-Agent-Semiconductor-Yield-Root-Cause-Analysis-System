"""Generate manufacturing-consistent offline Synthetic Fab RCA cases.

This module is a seed-time utility. It never runs in the FastAPI lifecycle.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from generate_synthetic_fab_data import (
    OPERATIONS,
    build_static_rows,
    equipment_for_operation,
    iso,
    recipe_for_operation,
    write_csv,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "seeds" / "multi_case"
DEFAULT_SEED = 20260721
WAFERS_PER_LOT = 25
LOT_COUNT = 75
ROUTE_ID = "ROUTE_40N_SOC_A"


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    lot_start: int
    lot_end: int
    source_lot_no: int
    operation_no: str
    equipment_id: str
    chamber_id: str
    root_cause: str
    expected_status: str

    @property
    def lot_ids(self) -> list[str]:
        return [lot_id(number) for number in range(self.lot_start, self.lot_end + 1)]

    @property
    def source_lot_id(self) -> str:
        return lot_id(self.source_lot_no)


CU_CASE = CaseSpec(
    case_id="CASE_CU_SLURRY_WINDOW",
    lot_start=1,
    lot_end=25,
    source_lot_no=15,
    operation_no="6400",
    equipment_id="CMP_CU03",
    chamber_id="CMP_CU03_CH02",
    root_cause="CMP_CU03_CH02 slurry delivery degradation",
    expected_status="supported",
)
SCRATCH_CASE = CaseSpec(
    case_id="CASE_ISOLATED_WAFER_SCRATCH",
    lot_start=26,
    lot_end=50,
    source_lot_no=38,
    operation_no="6400",
    equipment_id="CMP_CU02",
    chamber_id="CMP_CU02_CH01",
    root_cause="inconclusive",
    expected_status="inconclusive",
)
THIN_FILM_CASE = CaseSpec(
    case_id="CASE_ILD_ODD_EVEN_THICKNESS",
    lot_start=51,
    lot_end=75,
    source_lot_no=63,
    operation_no="5000",
    equipment_id="CVD_ILD_01",
    chamber_id="CVD_ILD_01_CH02",
    root_cause="CVD_ILD_01_CH02 deposition rate excursion",
    expected_status="supported",
)
CASES = (CU_CASE, SCRATCH_CASE, THIN_FILM_CASE)
CU_SUSPECT_LOT_NOS = set(range(11, 16))
CU_FAILED_LOT_NOS = set(range(12, 16))


def lot_id(lot_no: int) -> str:
    return f"LOT_A_{lot_no:03d}"


def wafer_id(lot_no: int, wafer_no: int) -> str:
    return f"{lot_id(lot_no)}_W{wafer_no:02d}"


def case_for_lot(lot_no: int) -> CaseSpec:
    return next(case for case in CASES if case.lot_start <= lot_no <= case.lot_end)


def _assignment(
    *,
    operation_no: str,
    lot_no: int,
    wafer_no: int,
    rng: random.Random,
) -> tuple[str, str]:
    if operation_no == "6400" and lot_no in CU_SUSPECT_LOT_NOS:
        return CU_CASE.equipment_id, CU_CASE.chamber_id
    if operation_no == "6400" and lot_no == SCRATCH_CASE.source_lot_no and wafer_no == 7:
        return SCRATCH_CASE.equipment_id, SCRATCH_CASE.chamber_id
    if operation_no == "5000" and lot_no == THIN_FILM_CASE.source_lot_no:
        chamber = "CVD_ILD_01_CH01" if wafer_no % 2 else "CVD_ILD_01_CH02"
        return "CVD_ILD_01", chamber
    if operation_no == "5000" and THIN_FILM_CASE.lot_start <= lot_no <= THIN_FILM_CASE.lot_end:
        chamber_no = 1 + ((lot_no + wafer_no) % 2)
        return "CVD_ILD_01", f"CVD_ILD_01_CH{chamber_no:02d}"
    if operation_no == "5100" and lot_no == THIN_FILM_CASE.source_lot_no:
        # Wafer pairs share heads, so odd/even parity is independent of CMP head.
        head_no = ((wafer_no - 1) // 2) % 4 + 1
        return "CMP_ILD_01", f"CMP_ILD_01_CH{head_no:02d}"
    return equipment_for_operation(operation_no, lot_no, False, rng)


def _fdc_row(
    *,
    lot_no: int,
    wafer_no: int,
    operation_no: str,
    equipment_id: str,
    chamber_id: str,
    parameter_name: str,
    baseline: float,
    observed: float,
    unit: str,
    severity: str,
    measured_at: datetime,
    ooc: bool = False,
    trend_slope: float = 0.0,
) -> dict[str, str]:
    recipe_id, recipe_version = recipe_for_operation(operation_no)
    delta = 100.0 * (observed - baseline) / baseline if baseline else 0.0
    return {
        "lot_id": lot_id(lot_no),
        "wafer_id": wafer_id(lot_no, wafer_no),
        "operation_no": operation_no,
        "equipment_id": equipment_id,
        "chamber_id": chamber_id,
        "recipe_id": recipe_id,
        "recipe_version": recipe_version,
        "parameter_name": parameter_name,
        "baseline_value": f"{baseline:.3f}",
        "observed_value": f"{observed:.3f}",
        "delta_percent": f"{delta:.3f}",
        "unit": unit,
        "trend_slope": f"{trend_slope:.3f}",
        "ooc_flag": str(ooc).lower(),
        "severity": severity,
        "measured_at": iso(measured_at),
    }


def _normal_cu_fdc(
    rows: dict[str, list[dict[str, Any]]],
    *,
    lot_no: int,
    wafer_no: int,
    equipment_id: str,
    chamber_id: str,
    measured_at: datetime,
    rng: random.Random,
) -> None:
    noise = rng.uniform(-0.8, 0.8)
    rows["fdc_feature"].extend(
        [
            _fdc_row(
                lot_no=lot_no,
                wafer_no=wafer_no,
                operation_no="6400",
                equipment_id=equipment_id,
                chamber_id=chamber_id,
                parameter_name="slurry_flow",
                baseline=150.0,
                observed=150.0 + noise,
                unit="ml/min",
                severity="NORMAL",
                measured_at=measured_at,
            ),
            _fdc_row(
                lot_no=lot_no,
                wafer_no=wafer_no,
                operation_no="6400",
                equipment_id=equipment_id,
                chamber_id=chamber_id,
                parameter_name="endpoint_time",
                baseline=90.0,
                observed=90.0 - noise * 0.2,
                unit="s",
                severity="NORMAL",
                measured_at=measured_at,
            ),
            _fdc_row(
                lot_no=lot_no,
                wafer_no=wafer_no,
                operation_no="6400",
                equipment_id=equipment_id,
                chamber_id=chamber_id,
                parameter_name="estimated_removal_rate",
                baseline=500.0,
                observed=500.0 + noise * 1.5,
                unit="nm/min",
                severity="NORMAL",
                measured_at=measured_at,
            ),
        ]
    )


def _cu_case_fdc(
    rows: dict[str, list[dict[str, Any]]],
    *,
    lot_no: int,
    wafer_no: int,
    equipment_id: str,
    chamber_id: str,
    measured_at: datetime,
    rng: random.Random,
) -> None:
    if lot_no not in CU_SUSPECT_LOT_NOS:
        _normal_cu_fdc(
            rows,
            lot_no=lot_no,
            wafer_no=wafer_no,
            equipment_id=equipment_id,
            chamber_id=chamber_id,
            measured_at=measured_at,
            rng=rng,
        )
        return

    degradation = (lot_no - 10) * 3.6 + wafer_no * 0.025
    slurry = 150.0 - degradation
    removal_rate = 500.0 - degradation * 3.0
    endpoint = 90.0 + degradation * 0.82
    ooc = lot_no == CU_CASE.source_lot_no and wafer_no == 13
    severity = "HIGH" if lot_no == CU_CASE.source_lot_no else "MEDIUM"
    values = (
        ("slurry_flow", 150.0, slurry, "ml/min", -0.8),
        ("endpoint_time", 90.0, endpoint, "s", 0.7),
        ("estimated_removal_rate", 500.0, removal_rate, "nm/min", -2.5),
    )
    for parameter_name, baseline, observed, unit, slope in values:
        rows["fdc_feature"].append(
            _fdc_row(
                lot_no=lot_no,
                wafer_no=wafer_no,
                operation_no="6400",
                equipment_id=CU_CASE.equipment_id,
                chamber_id=CU_CASE.chamber_id,
                parameter_name=parameter_name,
                baseline=baseline,
                observed=observed,
                unit=unit,
                severity=severity,
                measured_at=measured_at,
                ooc=ooc and parameter_name == "slurry_flow",
                trend_slope=slope,
            )
        )


def _thin_film_fdc(
    rows: dict[str, list[dict[str, Any]]],
    *,
    lot_no: int,
    wafer_no: int,
    chamber_id: str,
    measured_at: datetime,
    rng: random.Random,
) -> None:
    abnormal = lot_no == THIN_FILM_CASE.source_lot_no and wafer_no % 2 == 0
    noise = rng.uniform(-1.2, 1.2)
    deposition_rate = 100.0 + noise if not abnormal else 91.0 + noise * 0.2
    film_thickness = 1000.0 + noise * 2.0 if not abnormal else 900.0 + noise
    severity = "MEDIUM" if abnormal else "NORMAL"
    for parameter_name, baseline, observed, unit in (
        ("deposition_rate", 100.0, deposition_rate, "nm/min"),
        ("film_thickness", 1000.0, film_thickness, "nm"),
    ):
        rows["fdc_feature"].append(
            _fdc_row(
                lot_no=lot_no,
                wafer_no=wafer_no,
                operation_no="5000",
                equipment_id="CVD_ILD_01",
                chamber_id=chamber_id,
                parameter_name=parameter_name,
                baseline=baseline,
                observed=observed,
                unit=unit,
                severity=severity,
                measured_at=measured_at,
                trend_slope=-1.2 if abnormal else 0.0,
            )
        )


def _wat_row(lot_no: int, wafer_no: int, tested_at: datetime) -> dict[str, str]:
    failed = lot_no in CU_FAILED_LOT_NOS
    return {
        "lot_id": lot_id(lot_no),
        "wafer_id": wafer_id(lot_no, wafer_no),
        "test_item": "WAT_LEAKAGE_SHORT",
        "parameter_name": "iddq_leakage",
        "measured_value": f"{14.0 + wafer_no * 0.04:.3f}" if failed else "2.100",
        "spec_low": "0.0",
        "spec_high": "5.0",
        "pass_fail": str(not failed).lower(),
        "fail_mode": "leakage_short" if failed else "",
        "tested_at": iso(tested_at),
    }


def _metrology_rows(
    *,
    lot_no: int,
    wafer_no: int,
    pre_measured_at: datetime,
    post_measured_at: datetime,
    rng: random.Random,
) -> list[dict[str, str]]:
    abnormal = lot_no == THIN_FILM_CASE.source_lot_no and wafer_no % 2 == 0
    pre_value = (900.0 if abnormal else 1000.0) + rng.uniform(-2.0, 2.0)
    post_value = (720.0 if abnormal else 800.0) + rng.uniform(-2.0, 2.0)
    payload = []
    for stage, value, low, high, measured_at in (
        ("PRE_CMP", pre_value, 950.0, 1050.0, pre_measured_at),
        ("POST_CMP", post_value, 780.0, 820.0, post_measured_at),
    ):
        payload.append(
            {
                "lot_id": lot_id(lot_no),
                "wafer_id": wafer_id(lot_no, wafer_no),
                "operation_no": "5100",
                "measurement_stage": stage,
                "metric_name": "mean_thickness",
                "measured_value": f"{value:.3f}",
                "unit": "nm",
                "spec_low": f"{low:.1f}",
                "spec_high": f"{high:.1f}",
                "pass_fail": str(low <= value <= high).lower(),
                "metrology_tool": "METRO_THK_01",
                "measured_at": iso(measured_at),
            }
        )
    return payload


def _dynamic_rows(
    seed: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[int, int, str], tuple[datetime, datetime]]]:
    rng = random.Random(seed)
    base_time = datetime(2026, 7, 1, 0, tzinfo=UTC)
    rows: dict[str, list[dict[str, Any]]] = {
        "lot_master": [],
        "wafer_master": [],
        "process_history": [],
        "recipe_history": [],
        "hold_history": [],
        "fdc_feature": [],
        "ooc_event": [],
        "defect_summary": [],
        "metrology_result": [],
        "wat_result": [],
        "rca_case": [],
        "knowledge_document": [],
    }
    windows: dict[tuple[int, int, str], tuple[datetime, datetime]] = {}
    assignments: dict[tuple[int, int, str], tuple[str, str]] = {}

    for lot_no in range(1, LOT_COUNT + 1):
        lot_start = base_time + timedelta(hours=(lot_no - 1) * 6)
        final_end = lot_start + timedelta(minutes=len(OPERATIONS) * 90 + 50)
        rows["lot_master"].append(
            {
                "lot_id": lot_id(lot_no),
                "product_id": "40N_SOC",
                "technology": "40nm",
                "route_id": ROUTE_ID,
                "wafer_qty": str(WAFERS_PER_LOT),
                "lot_type": "PRODUCTION",
                "priority": "8" if lot_no in CU_FAILED_LOT_NOS else "5",
                "status": "COMPLETE",
                "current_operation_no": "9000",
                "created_at": iso(lot_start - timedelta(hours=8)),
                "started_at": iso(lot_start),
                "finished_at": iso(final_end),
            }
        )
        for wafer_no in range(1, WAFERS_PER_LOT + 1):
            rows["wafer_master"].append(
                {
                    "wafer_id": wafer_id(lot_no, wafer_no),
                    "lot_id": lot_id(lot_no),
                    "wafer_no": str(wafer_no),
                    "slot": str(wafer_no),
                    "status": "COMPLETE",
                }
            )

        for op_index, operation in enumerate(OPERATIONS):
            operation_start = lot_start + timedelta(minutes=(op_index + 1) * 90)
            recipe_id, recipe_version = recipe_for_operation(operation.operation_no)
            for wafer_no in range(1, WAFERS_PER_LOT + 1):
                started_at = operation_start + timedelta(minutes=wafer_no - 1)
                ended_at = started_at + timedelta(minutes=20)
                equipment_id, chamber_id = _assignment(
                    operation_no=operation.operation_no,
                    lot_no=lot_no,
                    wafer_no=wafer_no,
                    rng=rng,
                )
                windows[(lot_no, wafer_no, operation.operation_no)] = (started_at, ended_at)
                assignments[(lot_no, wafer_no, operation.operation_no)] = (
                    equipment_id,
                    chamber_id,
                )
                process_row = {
                    "lot_id": lot_id(lot_no),
                    "wafer_id": wafer_id(lot_no, wafer_no),
                    "route_id": ROUTE_ID,
                    "operation_no": operation.operation_no,
                    "operation_name": operation.operation_name,
                    "module": operation.module,
                    "equipment_id": equipment_id,
                    "chamber_id": chamber_id,
                    "recipe_id": recipe_id,
                    "recipe_version": recipe_version,
                    "started_at": iso(started_at),
                    "ended_at": iso(ended_at),
                    "operator_id": "op_auto",
                    "process_result": "PASS",
                }
                rows["process_history"].append(process_row)
                rows["recipe_history"].append(
                    {
                        "lot_id": lot_id(lot_no),
                        "wafer_id": wafer_id(lot_no, wafer_no),
                        "operation_no": operation.operation_no,
                        "equipment_id": equipment_id,
                        "chamber_id": chamber_id,
                        "recipe_id": recipe_id,
                        "recipe_version": recipe_version,
                        "executed_at": iso(started_at),
                    }
                )

        for wafer_no in range(1, WAFERS_PER_LOT + 1):
            cu_start, cu_end = windows[(lot_no, wafer_no, "6400")]
            cu_equipment, cu_chamber = assignments[(lot_no, wafer_no, "6400")]
            if lot_no <= CU_CASE.lot_end:
                _cu_case_fdc(
                    rows,
                    lot_no=lot_no,
                    wafer_no=wafer_no,
                    equipment_id=cu_equipment,
                    chamber_id=cu_chamber,
                    measured_at=cu_end,
                    rng=rng,
                )
            else:
                _normal_cu_fdc(
                    rows,
                    lot_no=lot_no,
                    wafer_no=wafer_no,
                    equipment_id=cu_equipment,
                    chamber_id=cu_chamber,
                    measured_at=cu_end,
                    rng=rng,
                )
            if lot_no >= THIN_FILM_CASE.lot_start:
                dep_start, dep_end = windows[(lot_no, wafer_no, "5000")]
                del dep_start
                _, dep_chamber = assignments[(lot_no, wafer_no, "5000")]
                _thin_film_fdc(
                    rows,
                    lot_no=lot_no,
                    wafer_no=wafer_no,
                    chamber_id=dep_chamber,
                    measured_at=dep_end,
                    rng=rng,
                )
                cmp_start, cmp_end = windows[(lot_no, wafer_no, "5100")]
                rows["metrology_result"].extend(
                    _metrology_rows(
                        lot_no=lot_no,
                        wafer_no=wafer_no,
                        pre_measured_at=cmp_start - timedelta(minutes=5),
                        post_measured_at=cmp_end + timedelta(minutes=5),
                        rng=rng,
                    )
                )
            wat_end = windows[(lot_no, wafer_no, "9000")][1]
            rows["wat_result"].append(_wat_row(lot_no, wafer_no, wat_end))

        if lot_no in CU_FAILED_LOT_NOS:
            inspect_end = windows[(lot_no, 13, "6500")][1]
            rows["defect_summary"].append(
                {
                    "lot_id": lot_id(lot_no),
                    "wafer_id": wafer_id(lot_no, 13),
                    "inspection_operation_no": "6500",
                    "defect_type": "cu_residue",
                    "defect_count": "86",
                    "defect_density": "0.34",
                    "pattern_type": "center_cluster",
                    "location_region": "center",
                    "inspected_at": iso(inspect_end),
                }
            )
        if lot_no == SCRATCH_CASE.source_lot_no:
            inspect_end = windows[(lot_no, 7, "6500")][1]
            rows["defect_summary"].append(
                {
                    "lot_id": lot_id(lot_no),
                    "wafer_id": wafer_id(lot_no, 7),
                    "inspection_operation_no": "6500",
                    "defect_type": "scratch",
                    "defect_count": "1",
                    "defect_density": "0.004",
                    "pattern_type": "isolated",
                    "location_region": "single_site",
                    "inspected_at": iso(inspect_end),
                }
            )

    _add_events_holds_and_knowledge(rows, windows)
    return rows, windows


def _hold_row(
    *,
    hold_id: str,
    lot_no: int,
    wafer_no: int,
    hold_type: str,
    hold_code: str,
    reason: str,
    comment: str,
    created_at: datetime,
    released_at: datetime | None,
) -> dict[str, str]:
    return {
        "hold_id": hold_id,
        "lot_id": lot_id(lot_no),
        "wafer_id": wafer_id(lot_no, wafer_no),
        "hold_type": hold_type,
        "hold_code": hold_code,
        "hold_reason": reason,
        "hold_comment": comment,
        "created_by": "yield_eng",
        "created_at": iso(created_at),
        "released_by": "equipment_eng" if released_at else "",
        "released_at": iso(released_at) if released_at else "",
        "release_comment": "Qualified after corrective action" if released_at else "",
    }


def _add_events_holds_and_knowledge(
    rows: dict[str, list[dict[str, Any]]],
    windows: dict[tuple[int, int, str], tuple[datetime, datetime]],
) -> None:
    cu_trigger = windows[(CU_CASE.source_lot_no, WAFERS_PER_LOT, "6400")][1]
    rows["ooc_event"].append(
        {
            "feature_id": "",
            "equipment_id": CU_CASE.equipment_id,
            "chamber_id": CU_CASE.chamber_id,
            "operation_no": CU_CASE.operation_no,
            "parameter_name": "slurry_flow",
            "alarm_type": "CONTROL_LIMIT",
            "severity": "HIGH",
            "triggered_at": iso(cu_trigger),
            "description": (
                f"{CU_CASE.source_lot_id}: slurry flow crossed the control limit on "
                f"{CU_CASE.chamber_id}"
            ),
        }
    )
    rows["hold_history"].append(
        _hold_row(
            hold_id="HOLD_CU_OOC_001",
            lot_no=CU_CASE.source_lot_no,
            wafer_no=25,
            hold_type="EQUIPMENT",
            hold_code="FDC_OOC_CONTAINMENT",
            reason="Contain chamber and trigger Lot after slurry flow OOC",
            comment=(
                "Slurry flow crossed the FDC control limit; contain CMP_CU03_CH02 and "
                "review Lots processed since the first non-normal feature."
            ),
            created_at=cu_trigger + timedelta(minutes=1),
            released_at=cu_trigger + timedelta(hours=8),
        )
    )

    scratch_detected = windows[(SCRATCH_CASE.source_lot_no, 7, "6500")][1]
    rows["hold_history"].append(
        _hold_row(
            hold_id="HOLD_SCRATCH_W07_001",
            lot_no=SCRATCH_CASE.source_lot_no,
            wafer_no=7,
            hold_type="QUALITY",
            hold_code="KLA_SINGLE_WAFER_REVIEW",
            reason="KLA detected one isolated scratch",
            comment=(
                "Only Wafer 07 has one isolated scratch. Neighbor Wafers and recent Lots show "
                "no matching defect; CMP FDC is normal."
            ),
            created_at=scratch_detected + timedelta(minutes=5),
            released_at=None,
        )
    )

    thin_film_detected = windows[(THIN_FILM_CASE.source_lot_no, 24, "5100")][1]
    rows["hold_history"].append(
        _hold_row(
            hold_id="HOLD_ILD_PARITY_001",
            lot_no=THIN_FILM_CASE.source_lot_no,
            wafer_no=24,
            hold_type="PROCESS",
            hold_code="METROLOGY_ODD_EVEN_SPLIT",
            reason="Post-CMP metrology shows an odd/even Wafer thickness split",
            comment=(
                "Even Wafers are low after ILD CMP and map to CVD_ILD_01_CH02; CMP heads are "
                "balanced across odd/even Wafers and CMP FDC remains normal."
            ),
            created_at=thin_film_detected + timedelta(minutes=5),
            released_at=None,
        )
    )

    knowledge = (
        (
            "RCA_MULTI_CU_001",
            "Cu CMP slurry delivery degradation",
            "Cu CMP",
            "CMP",
            "Slurry flow decline, removal-rate loss, endpoint extension, Cu residue, leakage short",
            "Slurry delivery degradation reduced Cu CMP removal rate",
            "Inspect slurry pump, replace slurry filter, calibrate flow controller, "
            "run qualification wafers",
        ),
        (
            "RCA_MULTI_SCRATCH_001",
            "Isolated single-Wafer scratch investigation",
            "Cu CMP",
            "CMP",
            "One Wafer has an isolated scratch while adjacent Wafers and equipment FDC are normal",
            "Transient particle or handling event; exact source was not confirmed",
            "Inspect handling path, review particle monitors, inspect carrier, "
            "monitor subsequent Lots",
        ),
        (
            "RCA_MULTI_ILD_001",
            "ILD deposition chamber rate excursion",
            "Thin Film",
            "CVD",
            "Even Wafers have low pre-CMP and post-CMP thickness with normal CMP FDC",
            "CVD_ILD_01_CH02 deposition rate excursion",
            "Hold CVD chamber, verify deposition rate, calibrate gas delivery, "
            "run thickness qualification",
        ),
    )
    for index, (case_id, title, module, equipment_type, symptom, cause, solution) in enumerate(
        knowledge, start=1
    ):
        created_at = f"2025-0{index + 3}-12T00:00:00+00:00"
        rows["rca_case"].append(
            {
                "case_id": case_id,
                "title": title,
                "technology": "40nm",
                "module": module,
                "equipment_type": equipment_type,
                "symptom": symptom,
                "root_cause": cause,
                "solution": solution,
                "confidence": "0.92",
                "created_at": created_at,
            }
        )
        rows["knowledge_document"].append(
            {
                "document_id": f"DOC_{case_id}",
                "case_id": case_id,
                "document_type": "RCA_CASE",
                "title": title,
                "content": f"Symptom: {symptom}. Root cause: {cause}. Actions: {solution}.",
                "tags": f"{module};{equipment_type};40N_SOC",
                "created_at": created_at,
            }
        )


def _validate_dataset(
    rows: dict[str, list[dict[str, Any]]],
    static_rows: dict[str, list[dict[str, Any]]],
) -> None:
    expected_lots = {lot_id(number) for number in range(1, LOT_COUNT + 1)}
    actual_lots = {row["lot_id"] for row in rows["lot_master"]}
    if actual_lots != expected_lots:
        raise ValueError("Lot IDs must be continuous LOT_A_001 through LOT_A_075")

    route_operations = {item.operation_no for item in OPERATIONS}
    operations_by_wafer: dict[str, set[str]] = {}
    for row in rows["process_history"]:
        operations_by_wafer.setdefault(row["wafer_id"], set()).add(row["operation_no"])
    if any(operations != route_operations for operations in operations_by_wafer.values()):
        raise ValueError("Every Wafer must follow the complete process route")

    qualified = {
        (row["equipment_id"], row["chamber_id"], row["operation_no"])
        for row in static_rows["equipment_capability"]
        if row["qualification_status"] == "QUALIFIED"
    }
    invalid = [
        row
        for row in rows["process_history"]
        if (row["equipment_id"], row["chamber_id"], row["operation_no"]) not in qualified
    ]
    if invalid:
        raise ValueError(f"Process assignment violates equipment capability: {invalid[0]}")

    ooc_features = [row for row in rows["fdc_feature"] if row["ooc_flag"] == "true"]
    if len(rows["ooc_event"]) != 1 or len(ooc_features) != 1:
        raise ValueError("The Cu case must contain exactly one threshold-crossing OOC")


def _catalog() -> list[dict[str, Any]]:
    return [
        {
            "case_id": CU_CASE.case_id,
            "query": f"Analyze abnormal Lot {CU_CASE.source_lot_id} and identify impact Lots.",
            "source_lot_id": CU_CASE.source_lot_id,
            "root_cause": CU_CASE.root_cause,
            "expected_status": CU_CASE.expected_status,
            "affected_operation": CU_CASE.operation_no,
            "affected_equipment": CU_CASE.equipment_id,
            "affected_chamber": CU_CASE.chamber_id,
            "case_lots": CU_CASE.lot_ids,
            "yield_affected_lots": [lot_id(number) for number in sorted(CU_FAILED_LOT_NOS)],
            "expected_scope_lots": [lot_id(number) for number in range(11, 16)],
            "expected_impact_lots": [lot_id(number) for number in range(11, 15)],
            "expected_scope_level": "mixed",
            "expected_evidence_ids": [
                "EV_MES_SOURCE_LOT_CONTEXT",
                "EV_FDC_EXCURSION_WINDOW",
                "EV_FDC_SLURRY_FLOW",
                "EV_FDC_ESTIMATED_REMOVAL_RATE",
                "EV_DEFECT_CU_RESIDUE",
                "EV_WAT_LEAKAGE_SHORT",
                "EV_KNOWLEDGE_MATCH",
            ],
        },
        {
            "case_id": SCRATCH_CASE.case_id,
            "query": (
                f"Analyze abnormal Lot {SCRATCH_CASE.source_lot_id} with an isolated scratch."
            ),
            "source_lot_id": SCRATCH_CASE.source_lot_id,
            "root_cause": SCRATCH_CASE.root_cause,
            "expected_status": SCRATCH_CASE.expected_status,
            "affected_operation": SCRATCH_CASE.operation_no,
            "case_lots": SCRATCH_CASE.lot_ids,
            "expected_scope_lots": [SCRATCH_CASE.source_lot_id],
            "expected_impact_lots": [],
            "expected_affected_wafers": [wafer_id(SCRATCH_CASE.source_lot_no, 7)],
            "expected_impact_wafers": [],
            "expected_scope_level": "wafer",
            "expected_evidence_ids": [
                "EV_MES_SOURCE_LOT_CONTEXT",
                "EV_MES_IMPACT_LOTS",
                "EV_DEFECT_SCRATCH",
                "EV_MES_LOT_HOLD",
            ],
        },
        {
            "case_id": THIN_FILM_CASE.case_id,
            "query": (
                f"Analyze abnormal Lot {THIN_FILM_CASE.source_lot_id} with "
                "odd/even thickness split."
            ),
            "source_lot_id": THIN_FILM_CASE.source_lot_id,
            "root_cause": THIN_FILM_CASE.root_cause,
            "expected_status": THIN_FILM_CASE.expected_status,
            "affected_operation": THIN_FILM_CASE.operation_no,
            "affected_equipment": THIN_FILM_CASE.equipment_id,
            "affected_chamber": THIN_FILM_CASE.chamber_id,
            "case_lots": THIN_FILM_CASE.lot_ids,
            "expected_scope_lots": [THIN_FILM_CASE.source_lot_id],
            "expected_impact_lots": [],
            "expected_affected_wafers": [
                wafer_id(THIN_FILM_CASE.source_lot_no, number)
                for number in range(2, WAFERS_PER_LOT + 1, 2)
            ],
            "expected_impact_wafers": [
                wafer_id(THIN_FILM_CASE.source_lot_no, number)
                for number in range(2, WAFERS_PER_LOT + 1, 2)
            ],
            "expected_scope_level": "wafer",
            "expected_evidence_ids": [
                "EV_MES_SOURCE_LOT_CONTEXT",
                "EV_FDC_DEPOSITION_RATE",
                "EV_METROLOGY_PRE_CMP_MEAN_THICKNESS",
                "EV_METROLOGY_POST_CMP_MEAN_THICKNESS",
                "EV_FDC_CMP_NORMAL_EXCLUSION",
                "EV_KNOWLEDGE_MATCH",
            ],
        },
    ]


def generate_dataset(output_dir: Path, seed: int) -> None:
    static_rows = build_static_rows()
    dynamic_rows, _ = _dynamic_rows(seed)
    _validate_dataset(dynamic_rows, static_rows)
    for table_name, table_rows in {**static_rows, **dynamic_rows}.items():
        write_csv(output_dir / f"{table_name}.csv", table_rows)

    catalog = _catalog()
    payload = {"schema_version": "2.0", "seed": seed, "cases": catalog}
    write_json(output_dir / "ground_truth.json", payload)
    write_json(
        output_dir / "case_catalog.json",
        {
            **payload,
            "description": (
                "Three Lot-driven cases with complete per-Wafer genealogy, realistic detection "
                "timing, and optional OOC evidence."
            ),
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate offline multi-case Fab seed data.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    generate_dataset(args.output_dir, args.seed)
    print(f"Generated multi-case dataset at {args.output_dir} with seed {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
