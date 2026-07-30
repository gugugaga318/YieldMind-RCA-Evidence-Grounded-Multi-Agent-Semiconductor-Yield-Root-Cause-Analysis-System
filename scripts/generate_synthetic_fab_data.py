"""Generate the offline golden Synthetic Fab dataset for Step 4.

This script is intentionally offline-only. It writes deterministic CSV/JSON
seed files under data/seeds/golden_case and does not connect to FastAPI,
Tools, Agents, or any runtime service.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_SEED = 20260716
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "seeds" / "golden_case"


@dataclass(frozen=True)
class Operation:
    operation_no: str
    operation_name: str
    module: str
    process_area: str
    material: str | None
    canonical_equipment_type: str
    is_critical: bool


OPERATIONS = [
    Operation("1000", "Pre STI Wet Clean", "Wet Clean", "FEOL", None, "WET", True),
    Operation("1100", "Pad Oxide Diffusion", "Diffusion", "FEOL", "Oxide", "DIFFUSION", True),
    Operation("1200", "STI Nitride Deposition", "Thin Film", "FEOL", "Nitride", "CVD", True),
    Operation("1300", "STI Lithography", "STI Litho", "FEOL", None, "LITHO", True),
    Operation("1400", "STI Etch", "STI Etch", "FEOL", "Oxide", "ETCH", True),
    Operation("1450", "STI Oxide Fill", "Thin Film", "FEOL", "Oxide", "CVD", True),
    Operation("1500", "STI CMP", "STI CMP", "FEOL", "Oxide", "CMP", True),
    Operation("1510", "Post STI CMP Wet Clean", "Wet Clean", "FEOL", None, "WET", True),
    Operation("5000", "ILD Deposition", "Thin Film", "MOL", "Oxide", "CVD", True),
    Operation("5100", "ILD CMP", "ILD CMP", "MOL", "Oxide", "CMP", True),
    Operation("5110", "Post ILD CMP Wet Clean", "Wet Clean", "MOL", None, "WET", True),
    Operation("5200", "Contact Lithography", "Contact Litho", "MOL", None, "LITHO", True),
    Operation("5210", "Contact Etch", "Contact Etch", "MOL", "Oxide", "ETCH", True),
    Operation("5220", "Pre W Wet Clean", "Wet Clean", "MOL", None, "WET", True),
    Operation("5230", "Barrier Liner Deposition", "Barrier Liner", "MOL", "Ti/TiN", "PVD", True),
    Operation("5240", "W CVD Fill", "Thin Film", "MOL", "Tungsten", "CVD", True),
    Operation("5300", "W Plug CMP", "W CMP", "MOL", "Tungsten", "CMP", True),
    Operation("5310", "Post W CMP Wet Clean", "Wet Clean", "MOL", None, "WET", True),
    Operation("6000", "Low-k IMD Deposition", "Thin Film", "BEOL", "Low-k", "CVD", True),
    Operation("6100", "IMD CMP", "IMD CMP", "BEOL", "Low-k", "CMP", True),
    Operation("6110", "Post IMD CMP Wet Clean", "Wet Clean", "BEOL", None, "WET", True),
    Operation("6200", "Cu Lithography", "Cu Litho", "BEOL", None, "LITHO", True),
    Operation("6210", "Cu Trench Etch", "Cu Etch", "BEOL", "Low-k", "ETCH", True),
    Operation("6220", "Pre Cu Wet Clean", "Wet Clean", "BEOL", None, "WET", True),
    Operation("6230", "Barrier Seed Deposition", "Barrier Seed", "BEOL", "Ta/Cu", "PVD", True),
    Operation("6240", "Cu ECP Fill", "Cu Plating", "BEOL", "Copper", "ECP", True),
    Operation("6400", "Cu CMP", "Cu CMP", "BEOL", "Copper", "CMP", True),
    Operation("6410", "Post Cu CMP Wet Clean", "Wet Clean", "BEOL", None, "WET", True),
    Operation("6500", "Post Cu CMP Inspection", "Defect Inspection", "BEOL", None, "KLA", True),
    Operation("9000", "WAT", "WAT", "TEST", None, "TEST", True),
]

EQUIPMENT = [
    {
        "equipment_id": "WET_FEOL_01",
        "equipment_type": "WET",
        "module": "Wet Clean",
        "process_area": "FEOL",
        "material": "",
        "vendor": "SCREEN",
        "model": "SU-3200",
        "location": "FAB01-WET",
        "status": "QUALIFIED",
        "installed_at": "2024-01-02",
    },
    {
        "equipment_id": "WET_MOL_01",
        "equipment_type": "WET",
        "module": "Wet Clean",
        "process_area": "MOL",
        "material": "",
        "vendor": "SCREEN",
        "model": "SU-3200",
        "location": "FAB01-WET-MOL",
        "status": "QUALIFIED",
        "installed_at": "2024-01-03",
    },
    {
        "equipment_id": "WET_BEOL_01",
        "equipment_type": "WET",
        "module": "Wet Clean",
        "process_area": "BEOL",
        "material": "",
        "vendor": "SCREEN",
        "model": "SU-3200",
        "location": "FAB01-WET-BEOL",
        "status": "QUALIFIED",
        "installed_at": "2024-01-04",
    },
    {
        "equipment_id": "FURNACE_DIFF_01",
        "equipment_type": "DIFFUSION",
        "module": "Diffusion",
        "process_area": "FEOL",
        "material": "Oxide",
        "vendor": "Tokyo Electron",
        "model": "Alpha-8SE",
        "location": "FAB01-DIFF",
        "status": "QUALIFIED",
        "installed_at": "2024-01-08",
    },
    {
        "equipment_id": "LITHO_STI_01",
        "equipment_type": "LITHO",
        "module": "STI Litho",
        "process_area": "FEOL",
        "material": "",
        "vendor": "ASML",
        "model": "XT",
        "location": "FAB01-LITHO",
        "status": "QUALIFIED",
        "installed_at": "2024-01-15",
    },
    {
        "equipment_id": "LITHO_CONTACT_01",
        "equipment_type": "LITHO",
        "module": "Contact Litho",
        "process_area": "MOL",
        "material": "",
        "vendor": "ASML",
        "model": "XT",
        "location": "FAB01-LITHO-MOL",
        "status": "QUALIFIED",
        "installed_at": "2024-01-16",
    },
    {
        "equipment_id": "LITHO_CU_01",
        "equipment_type": "LITHO",
        "module": "Cu Litho",
        "process_area": "BEOL",
        "material": "",
        "vendor": "ASML",
        "model": "XT",
        "location": "FAB01-LITHO-BEOL",
        "status": "QUALIFIED",
        "installed_at": "2024-01-17",
    },
    {
        "equipment_id": "CVD_STI_01",
        "equipment_type": "CVD",
        "module": "Thin Film",
        "process_area": "FEOL",
        "material": "Nitride",
        "vendor": "Applied Materials",
        "model": "Producer",
        "location": "FAB01-THINFILM",
        "status": "QUALIFIED",
        "installed_at": "2024-01-22",
    },
    {
        "equipment_id": "ETCH_STI_01",
        "equipment_type": "ETCH",
        "module": "STI Etch",
        "process_area": "FEOL",
        "material": "Oxide",
        "vendor": "Lam",
        "model": "2300",
        "location": "FAB01-ETCH-FEOL",
        "status": "QUALIFIED",
        "installed_at": "2024-01-24",
    },
    {
        "equipment_id": "CMP_STI_01",
        "equipment_type": "CMP",
        "module": "STI CMP",
        "process_area": "FEOL",
        "material": "Oxide",
        "vendor": "Applied Materials",
        "model": "Mirra",
        "location": "FAB01-CMP-STI",
        "status": "QUALIFIED",
        "installed_at": "2024-02-01",
    },
    {
        "equipment_id": "ETCH_CONTACT_01",
        "equipment_type": "ETCH",
        "module": "Contact Etch",
        "process_area": "MOL",
        "material": "Oxide",
        "vendor": "Lam",
        "model": "2300",
        "location": "FAB01-ETCH",
        "status": "QUALIFIED",
        "installed_at": "2024-03-03",
    },
    {
        "equipment_id": "CVD_ILD_01",
        "equipment_type": "CVD",
        "module": "Thin Film",
        "process_area": "MOL",
        "material": "Oxide",
        "vendor": "Applied Materials",
        "model": "Producer",
        "location": "FAB01-THINFILM-MOL",
        "status": "QUALIFIED",
        "installed_at": "2024-03-10",
    },
    {
        "equipment_id": "CMP_ILD_01",
        "equipment_type": "CMP",
        "module": "ILD CMP",
        "process_area": "MOL",
        "material": "Oxide",
        "vendor": "Applied Materials",
        "model": "Mirra",
        "location": "FAB01-CMP-ILD",
        "status": "QUALIFIED",
        "installed_at": "2024-03-20",
    },
    {
        "equipment_id": "PVD_WLINER_01",
        "equipment_type": "PVD",
        "module": "Barrier Liner",
        "process_area": "MOL",
        "material": "Ti/TiN",
        "vendor": "Applied Materials",
        "model": "Endura",
        "location": "FAB01-PVD-MOL",
        "status": "QUALIFIED",
        "installed_at": "2024-03-25",
    },
    {
        "equipment_id": "CVD_W_01",
        "equipment_type": "CVD",
        "module": "Thin Film",
        "process_area": "MOL",
        "material": "Tungsten",
        "vendor": "Applied Materials",
        "model": "Producer",
        "location": "FAB01-CVD-W",
        "status": "QUALIFIED",
        "installed_at": "2024-03-28",
    },
    {
        "equipment_id": "CMP_W_01",
        "equipment_type": "CMP",
        "module": "W CMP",
        "process_area": "MOL",
        "material": "Tungsten",
        "vendor": "Ebara",
        "model": "F-REX",
        "location": "FAB01-CMP-W",
        "status": "QUALIFIED",
        "installed_at": "2024-04-04",
    },
    {
        "equipment_id": "CVD_LOWK_01",
        "equipment_type": "CVD",
        "module": "Thin Film",
        "process_area": "BEOL",
        "material": "Low-k",
        "vendor": "Applied Materials",
        "model": "Producer",
        "location": "FAB01-LOWK",
        "status": "QUALIFIED",
        "installed_at": "2024-04-10",
    },
    {
        "equipment_id": "CMP_IMD_01",
        "equipment_type": "CMP",
        "module": "IMD CMP",
        "process_area": "BEOL",
        "material": "Low-k",
        "vendor": "Applied Materials",
        "model": "Mirra",
        "location": "FAB01-CMP-IMD",
        "status": "QUALIFIED",
        "installed_at": "2024-04-15",
    },
    {
        "equipment_id": "ETCH_CU_01",
        "equipment_type": "ETCH",
        "module": "Cu Etch",
        "process_area": "BEOL",
        "material": "Low-k",
        "vendor": "Lam",
        "model": "2300",
        "location": "FAB01-ETCH-BEOL",
        "status": "QUALIFIED",
        "installed_at": "2024-04-20",
    },
    {
        "equipment_id": "PVD_CUSEED_01",
        "equipment_type": "PVD",
        "module": "Barrier Seed",
        "process_area": "BEOL",
        "material": "Ta/Cu",
        "vendor": "Applied Materials",
        "model": "Endura",
        "location": "FAB01-PVD-BEOL",
        "status": "QUALIFIED",
        "installed_at": "2024-04-25",
    },
    {
        "equipment_id": "ECP_CU_01",
        "equipment_type": "ECP",
        "module": "Cu Plating",
        "process_area": "BEOL",
        "material": "Copper",
        "vendor": "Lam",
        "model": "SABRE",
        "location": "FAB01-ECP-CU",
        "status": "QUALIFIED",
        "installed_at": "2024-04-30",
    },
    {
        "equipment_id": "CMP_CU01",
        "equipment_type": "CMP",
        "module": "Cu CMP",
        "process_area": "BEOL",
        "material": "Copper",
        "vendor": "Applied Materials",
        "model": "Reflexion",
        "location": "FAB01-CMP-CU",
        "status": "QUALIFIED",
        "installed_at": "2024-05-05",
    },
    {
        "equipment_id": "CMP_CU02",
        "equipment_type": "CMP",
        "module": "Cu CMP",
        "process_area": "BEOL",
        "material": "Copper",
        "vendor": "Applied Materials",
        "model": "Reflexion",
        "location": "FAB01-CMP-CU",
        "status": "QUALIFIED",
        "installed_at": "2024-05-12",
    },
    {
        "equipment_id": "CMP_CU03",
        "equipment_type": "CMP",
        "module": "Cu CMP",
        "process_area": "BEOL",
        "material": "Copper",
        "vendor": "Applied Materials",
        "model": "Reflexion",
        "location": "FAB01-CMP-CU",
        "status": "QUALIFIED",
        "installed_at": "2024-05-19",
    },
    {
        "equipment_id": "KLA_INSPECT_01",
        "equipment_type": "KLA",
        "module": "Defect Inspection",
        "process_area": "BEOL",
        "material": "",
        "vendor": "KLA",
        "model": "DefectScan",
        "location": "FAB01-METRO",
        "status": "QUALIFIED",
        "installed_at": "2024-06-01",
    },
    {
        "equipment_id": "WAT_TEST_01",
        "equipment_type": "TEST",
        "module": "WAT",
        "process_area": "TEST",
        "material": "",
        "vendor": "Keysight",
        "model": "Parametric",
        "location": "FAB01-WAT",
        "status": "QUALIFIED",
        "installed_at": "2024-06-15",
    },
]


def cmp_heads(equipment_id: str, chamber_label: str) -> list[tuple[str, str, str]]:
    return [
        (equipment_id, f"{equipment_id}_CH{head_no:02d}", f"{chamber_label} Head {head_no:02d}")
        for head_no in range(1, 5)
    ]


CHAMBERS = [
    ("WET_FEOL_01", "WET_FEOL_01_CH01", "FEOL Wet Bench 01"),
    ("WET_MOL_01", "WET_MOL_01_CH01", "MOL Wet Bench 01"),
    ("WET_BEOL_01", "WET_BEOL_01_CH01", "BEOL Wet Bench 01"),
    ("FURNACE_DIFF_01", "FURNACE_DIFF_01_CH01", "Diffusion Tube 01"),
    ("LITHO_STI_01", "LITHO_STI_01_CH01", "Exposure Unit 01"),
    ("LITHO_CONTACT_01", "LITHO_CONTACT_01_CH01", "Contact Exposure Unit 01"),
    ("LITHO_CU_01", "LITHO_CU_01_CH01", "Cu Exposure Unit 01"),
    ("CVD_STI_01", "CVD_STI_01_CH01", "STI CVD Chamber 01"),
    ("ETCH_STI_01", "ETCH_STI_01_CH01", "STI Etch Chamber 01"),
    *cmp_heads("CMP_STI_01", "STI CMP"),
    ("ETCH_CONTACT_01", "ETCH_CONTACT_01_CH01", "Contact Etch Chamber 01"),
    ("CVD_ILD_01", "CVD_ILD_01_CH01", "ILD CVD Chamber 01"),
    ("CVD_ILD_01", "CVD_ILD_01_CH02", "ILD CVD Chamber 02"),
    *cmp_heads("CMP_ILD_01", "ILD CMP"),
    ("PVD_WLINER_01", "PVD_WLINER_01_CH01", "W Barrier Liner PVD 01"),
    ("CVD_W_01", "CVD_W_01_CH01", "W CVD Chamber 01"),
    ("CMP_W_01", "CMP_W_01_CH01", "W CMP Platen 01"),
    ("CVD_LOWK_01", "CVD_LOWK_01_CH01", "Low-k CVD Chamber 01"),
    *cmp_heads("CMP_IMD_01", "IMD CMP"),
    ("ETCH_CU_01", "ETCH_CU_01_CH01", "Cu Etch Chamber 01"),
    ("PVD_CUSEED_01", "PVD_CUSEED_01_CH01", "Cu Barrier Seed PVD 01"),
    ("ECP_CU_01", "ECP_CU_01_CH01", "Cu ECP Cell 01"),
    *cmp_heads("CMP_CU01", "Cu CMP"),
    *cmp_heads("CMP_CU02", "Cu CMP"),
    *cmp_heads("CMP_CU03", "Cu CMP"),
    ("KLA_INSPECT_01", "KLA_INSPECT_01_CH01", "Inspection Module 01"),
    ("WAT_TEST_01", "WAT_TEST_01_CH01", "Probe Station 01"),
]

RECIPES = [
    ("WET_CLEAN_40N", "R01", "40nm Pre STI Wet Clean", "Wet Clean", "WET_CLEAN", "ACTIVE"),
    ("DIFFUSION_OX_40N", "R02", "40nm Pad Oxide Diffusion", "Diffusion", "DIFFUSION_OX", "ACTIVE"),
    (
        "THINFILM_NITRIDE_40N",
        "R04",
        "40nm STI Nitride Deposition",
        "Thin Film",
        "THINFILM_NITRIDE",
        "ACTIVE",
    ),
    ("STI_LITHO_40N", "R01", "40nm STI Lithography", "STI Litho", "STI_LITHO", "ACTIVE"),
    ("STI_ETCH_40N", "R06", "40nm STI Trench Etch", "STI Etch", "STI_ETCH", "ACTIVE"),
    ("STI_OX_FILL_40N", "R08", "40nm STI Oxide Fill", "Thin Film", "STI_OX_FILL", "ACTIVE"),
    ("STI_CMP_40N", "R03", "40nm STI Oxide CMP", "STI CMP", "STI_CMP", "ACTIVE"),
    (
        "POST_STI_CMP_CLEAN_40N",
        "R01",
        "40nm Post STI CMP Clean",
        "Wet Clean",
        "POST_CMP_CLEAN",
        "ACTIVE",
    ),
    ("ILD_DEP_40N", "R01", "40nm ILD Oxide Deposition", "Thin Film", "ILD_DEP", "ACTIVE"),
    ("ILD_CMP_40N", "R02", "40nm ILD Oxide CMP", "ILD CMP", "ILD_CMP", "ACTIVE"),
    (
        "POST_ILD_CMP_CLEAN_40N",
        "R01",
        "40nm Post ILD CMP Clean",
        "Wet Clean",
        "POST_CMP_CLEAN",
        "ACTIVE",
    ),
    (
        "CONTACT_LITHO_40N",
        "R03",
        "40nm Contact Lithography",
        "Contact Litho",
        "CONTACT_LITHO",
        "ACTIVE",
    ),
    ("CONTACT_ETCH_40N", "R07", "40nm Contact Etch", "Contact Etch", "CONTACT_ETCH", "ACTIVE"),
    ("PRE_W_CLEAN_40N", "R01", "40nm Pre W Wet Clean", "Wet Clean", "PRE_METAL_CLEAN", "ACTIVE"),
    (
        "W_BARRIER_LINER_40N",
        "R04",
        "40nm Ti/TiN Barrier Liner",
        "Barrier Liner",
        "W_BARRIER",
        "ACTIVE",
    ),
    ("W_CVD_FILL_40N", "R05", "40nm Tungsten CVD Fill", "Thin Film", "W_CVD_FILL", "ACTIVE"),
    ("W_CMP_40N", "R09", "40nm Tungsten Plug CMP", "W CMP", "W_CMP", "ACTIVE"),
    (
        "POST_W_CMP_CLEAN_40N",
        "R01",
        "40nm Post W CMP Clean",
        "Wet Clean",
        "POST_CMP_CLEAN",
        "ACTIVE",
    ),
    ("LOWK_IMD_DEP_40N", "R01", "40nm Low-k IMD Deposition", "Thin Film", "LOWK_IMD_DEP", "ACTIVE"),
    ("IMD_CMP_40N", "R02", "40nm IMD CMP", "IMD CMP", "IMD_CMP", "ACTIVE"),
    (
        "POST_IMD_CMP_CLEAN_40N",
        "R01",
        "40nm Post IMD CMP Clean",
        "Wet Clean",
        "POST_CMP_CLEAN",
        "ACTIVE",
    ),
    ("CU_LITHO_40N", "R03", "40nm Cu Lithography", "Cu Litho", "CU_LITHO", "ACTIVE"),
    ("CU_ETCH_40N", "R04", "40nm Cu Trench Etch", "Cu Etch", "CU_ETCH", "ACTIVE"),
    ("PRE_CU_CLEAN_40N", "R01", "40nm Pre Cu Wet Clean", "Wet Clean", "PRE_METAL_CLEAN", "ACTIVE"),
    (
        "CU_BARRIER_SEED_40N",
        "R06",
        "40nm Cu Barrier Seed",
        "Barrier Seed",
        "CU_BARRIER_SEED",
        "ACTIVE",
    ),
    ("CU_ECP_FILL_40N", "R11", "40nm Cu ECP Fill", "Cu Plating", "CU_ECP", "ACTIVE"),
    ("CU_CMP_40N", "R18", "40nm Cu CMP Polish", "Cu CMP", "CU_CMP", "ACTIVE"),
    (
        "POST_CU_CMP_CLEAN_40N",
        "R01",
        "40nm Post Cu CMP Clean",
        "Wet Clean",
        "POST_CMP_CLEAN",
        "ACTIVE",
    ),
    (
        "KLA_CU_INSPECT",
        "R02",
        "Cu CMP Post Inspection",
        "Defect Inspection",
        "KLA_INSPECT",
        "ACTIVE",
    ),
    ("WAT_40N_SOC", "R05", "40N SOC WAT", "WAT", "WAT", "ACTIVE"),
]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_static_rows() -> dict[str, list[dict[str, Any]]]:
    operation_rows = [
        {
            "operation_no": item.operation_no,
            "operation_name": item.operation_name,
            "module": item.module,
            "process_area": item.process_area,
            "material": item.material or "",
            "canonical_equipment_type": item.canonical_equipment_type,
            "is_critical": str(item.is_critical).lower(),
        }
        for item in OPERATIONS
    ]
    route_rows = [
        {
            "route_id": "ROUTE_40N_SOC_A",
            "product_id": "40N_SOC",
            "operation_no": item.operation_no,
            "sequence_no": str(index * 10),
            "module": item.module,
            "operation_name": item.operation_name,
            "is_critical": str(item.is_critical).lower(),
        }
        for index, item in enumerate(OPERATIONS, start=1)
    ]
    equipment_rows = EQUIPMENT
    chamber_rows = [
        {
            "chamber_id": chamber_id,
            "equipment_id": equipment_id,
            "chamber_name": chamber_name,
            "chamber_type": "CHAMBER",
            "status": "QUALIFIED",
            "installed_at": "2024-05-01",
        }
        for equipment_id, chamber_id, chamber_name in CHAMBERS
    ]
    capability_rows = [
        capability("WET_FEOL_01", "WET_FEOL_01_CH01", "1000", "Wet Clean", "", "WET_CLEAN"),
        capability(
            "FURNACE_DIFF_01",
            "FURNACE_DIFF_01_CH01",
            "1100",
            "Diffusion",
            "Oxide",
            "DIFFUSION_OX",
        ),
        capability(
            "CVD_STI_01",
            "CVD_STI_01_CH01",
            "1200",
            "Thin Film",
            "Nitride",
            "THINFILM_NITRIDE",
        ),
        capability("LITHO_STI_01", "LITHO_STI_01_CH01", "1300", "STI Litho", "", "STI_LITHO"),
        capability("ETCH_STI_01", "ETCH_STI_01_CH01", "1400", "STI Etch", "Oxide", "STI_ETCH"),
        capability("CVD_STI_01", "CVD_STI_01_CH01", "1450", "Thin Film", "Oxide", "STI_OX_FILL"),
        *[
            capability(
                "CMP_STI_01", f"CMP_STI_01_CH{head_no:02d}", "1500", "STI CMP", "Oxide", "STI_CMP"
            )
            for head_no in range(1, 5)
        ],
        capability("WET_FEOL_01", "WET_FEOL_01_CH01", "1510", "Wet Clean", "", "POST_CMP_CLEAN"),
        capability("CVD_ILD_01", "CVD_ILD_01_CH01", "5000", "Thin Film", "Oxide", "ILD_DEP"),
        capability("CVD_ILD_01", "CVD_ILD_01_CH02", "5000", "Thin Film", "Oxide", "ILD_DEP"),
        *[
            capability(
                "CMP_ILD_01", f"CMP_ILD_01_CH{head_no:02d}", "5100", "ILD CMP", "Oxide", "ILD_CMP"
            )
            for head_no in range(1, 5)
        ],
        capability("WET_MOL_01", "WET_MOL_01_CH01", "5110", "Wet Clean", "", "POST_CMP_CLEAN"),
        capability(
            "LITHO_CONTACT_01",
            "LITHO_CONTACT_01_CH01",
            "5200",
            "Contact Litho",
            "",
            "CONTACT_LITHO",
        ),
        capability(
            "ETCH_CONTACT_01",
            "ETCH_CONTACT_01_CH01",
            "5210",
            "Contact Etch",
            "Oxide",
            "CONTACT_ETCH",
        ),
        capability("WET_MOL_01", "WET_MOL_01_CH01", "5220", "Wet Clean", "", "PRE_METAL_CLEAN"),
        capability(
            "PVD_WLINER_01", "PVD_WLINER_01_CH01", "5230", "Barrier Liner", "Ti/TiN", "W_BARRIER"
        ),
        capability("CVD_W_01", "CVD_W_01_CH01", "5240", "Thin Film", "Tungsten", "W_CVD_FILL"),
        capability("CMP_W_01", "CMP_W_01_CH01", "5300", "W CMP", "Tungsten", "W_CMP"),
        capability("WET_MOL_01", "WET_MOL_01_CH01", "5310", "Wet Clean", "", "POST_CMP_CLEAN"),
        capability("CVD_LOWK_01", "CVD_LOWK_01_CH01", "6000", "Thin Film", "Low-k", "LOWK_IMD_DEP"),
        *[
            capability(
                "CMP_IMD_01", f"CMP_IMD_01_CH{head_no:02d}", "6100", "IMD CMP", "Low-k", "IMD_CMP"
            )
            for head_no in range(1, 5)
        ],
        capability("WET_BEOL_01", "WET_BEOL_01_CH01", "6110", "Wet Clean", "", "POST_CMP_CLEAN"),
        capability("LITHO_CU_01", "LITHO_CU_01_CH01", "6200", "Cu Litho", "", "CU_LITHO"),
        capability("ETCH_CU_01", "ETCH_CU_01_CH01", "6210", "Cu Etch", "Low-k", "CU_ETCH"),
        capability("WET_BEOL_01", "WET_BEOL_01_CH01", "6220", "Wet Clean", "", "PRE_METAL_CLEAN"),
        capability(
            "PVD_CUSEED_01",
            "PVD_CUSEED_01_CH01",
            "6230",
            "Barrier Seed",
            "Ta/Cu",
            "CU_BARRIER_SEED",
        ),
        capability("ECP_CU_01", "ECP_CU_01_CH01", "6240", "Cu Plating", "Copper", "CU_ECP"),
        *[
            capability(
                equipment_id,
                f"{equipment_id}_CH{head_no:02d}",
                "6400",
                "Cu CMP",
                "Copper",
                "CU_CMP",
            )
            for equipment_id in ["CMP_CU01", "CMP_CU02", "CMP_CU03"]
            for head_no in range(1, 5)
        ],
        capability("WET_BEOL_01", "WET_BEOL_01_CH01", "6410", "Wet Clean", "", "POST_CMP_CLEAN"),
        capability(
            "KLA_INSPECT_01",
            "KLA_INSPECT_01_CH01",
            "6500",
            "Defect Inspection",
            "",
            "KLA_INSPECT",
        ),
        capability("WAT_TEST_01", "WAT_TEST_01_CH01", "9000", "WAT", "", "WAT"),
    ]
    recipe_rows = [
        {
            "recipe_id": recipe_id,
            "recipe_version": version,
            "recipe_name": name,
            "module": module,
            "recipe_family": family,
            "status": status,
            "owner": "process_eng",
            "released_at": "2026-01-01T00:00:00+00:00",
        }
        for recipe_id, version, name, module, family, status in RECIPES
    ]
    return {
        "operation_master": operation_rows,
        "process_route": route_rows,
        "equipment_master": equipment_rows,
        "chamber_master": chamber_rows,
        "equipment_capability": capability_rows,
        "recipe_master": recipe_rows,
    }


def capability(
    equipment_id: str,
    chamber_id: str,
    operation_no: str,
    module: str,
    material: str,
    recipe_family: str,
) -> dict[str, str]:
    return {
        "equipment_id": equipment_id,
        "chamber_id": chamber_id,
        "operation_no": operation_no,
        "module": module,
        "material": material,
        "recipe_family": recipe_family,
        "qualification_status": "QUALIFIED",
    }


def recipe_for_operation(operation_no: str) -> tuple[str, str]:
    mapping = {
        "1000": ("WET_CLEAN_40N", "R01"),
        "1100": ("DIFFUSION_OX_40N", "R02"),
        "1200": ("THINFILM_NITRIDE_40N", "R04"),
        "1300": ("STI_LITHO_40N", "R01"),
        "1400": ("STI_ETCH_40N", "R06"),
        "1450": ("STI_OX_FILL_40N", "R08"),
        "1500": ("STI_CMP_40N", "R03"),
        "1510": ("POST_STI_CMP_CLEAN_40N", "R01"),
        "5000": ("ILD_DEP_40N", "R01"),
        "5100": ("ILD_CMP_40N", "R02"),
        "5110": ("POST_ILD_CMP_CLEAN_40N", "R01"),
        "5200": ("CONTACT_LITHO_40N", "R03"),
        "5210": ("CONTACT_ETCH_40N", "R07"),
        "5220": ("PRE_W_CLEAN_40N", "R01"),
        "5230": ("W_BARRIER_LINER_40N", "R04"),
        "5240": ("W_CVD_FILL_40N", "R05"),
        "5300": ("W_CMP_40N", "R09"),
        "5310": ("POST_W_CMP_CLEAN_40N", "R01"),
        "6000": ("LOWK_IMD_DEP_40N", "R01"),
        "6100": ("IMD_CMP_40N", "R02"),
        "6110": ("POST_IMD_CMP_CLEAN_40N", "R01"),
        "6200": ("CU_LITHO_40N", "R03"),
        "6210": ("CU_ETCH_40N", "R04"),
        "6220": ("PRE_CU_CLEAN_40N", "R01"),
        "6230": ("CU_BARRIER_SEED_40N", "R06"),
        "6240": ("CU_ECP_FILL_40N", "R11"),
        "6400": ("CU_CMP_40N", "R18"),
        "6410": ("POST_CU_CMP_CLEAN_40N", "R01"),
        "6500": ("KLA_CU_INSPECT", "R02"),
        "9000": ("WAT_40N_SOC", "R05"),
    }
    return mapping[operation_no]


def equipment_for_operation(
    operation_no: str,
    lot_index: int,
    affected: bool,
    rng: random.Random,
) -> tuple[str, str]:
    if operation_no == "1000":
        return "WET_FEOL_01", "WET_FEOL_01_CH01"
    if operation_no == "1100":
        return "FURNACE_DIFF_01", "FURNACE_DIFF_01_CH01"
    if operation_no == "1200":
        return "CVD_STI_01", "CVD_STI_01_CH01"
    if operation_no == "1300":
        return "LITHO_STI_01", "LITHO_STI_01_CH01"
    if operation_no == "1400":
        return "ETCH_STI_01", "ETCH_STI_01_CH01"
    if operation_no == "1450":
        return "CVD_STI_01", "CVD_STI_01_CH01"
    if operation_no == "1500":
        return "CMP_STI_01", f"CMP_STI_01_CH{rng.randint(1, 4):02d}"
    if operation_no == "1510":
        return "WET_FEOL_01", "WET_FEOL_01_CH01"
    if operation_no == "5000":
        return "CVD_ILD_01", "CVD_ILD_01_CH01"
    if operation_no == "5100":
        return "CMP_ILD_01", f"CMP_ILD_01_CH{rng.randint(1, 4):02d}"
    if operation_no == "5110":
        return "WET_MOL_01", "WET_MOL_01_CH01"
    if operation_no == "5200":
        return "LITHO_CONTACT_01", "LITHO_CONTACT_01_CH01"
    if operation_no == "5210":
        return "ETCH_CONTACT_01", "ETCH_CONTACT_01_CH01"
    if operation_no == "5220":
        return "WET_MOL_01", "WET_MOL_01_CH01"
    if operation_no == "5230":
        return "PVD_WLINER_01", "PVD_WLINER_01_CH01"
    if operation_no == "5240":
        return "CVD_W_01", "CVD_W_01_CH01"
    if operation_no == "5300":
        return "CMP_W_01", "CMP_W_01_CH01"
    if operation_no == "5310":
        return "WET_MOL_01", "WET_MOL_01_CH01"
    if operation_no == "6000":
        return "CVD_LOWK_01", "CVD_LOWK_01_CH01"
    if operation_no == "6100":
        return "CMP_IMD_01", f"CMP_IMD_01_CH{rng.randint(1, 4):02d}"
    if operation_no == "6110":
        return "WET_BEOL_01", "WET_BEOL_01_CH01"
    if operation_no == "6200":
        return "LITHO_CU_01", "LITHO_CU_01_CH01"
    if operation_no == "6210":
        return "ETCH_CU_01", "ETCH_CU_01_CH01"
    if operation_no == "6220":
        return "WET_BEOL_01", "WET_BEOL_01_CH01"
    if operation_no == "6230":
        return "PVD_CUSEED_01", "PVD_CUSEED_01_CH01"
    if operation_no == "6240":
        return "ECP_CU_01", "ECP_CU_01_CH01"
    if operation_no == "6400":
        if affected:
            return "CMP_CU03", "CMP_CU03_CH02"
        return rng.choice(
            [
                (equipment_id, f"{equipment_id}_CH{head_no:02d}")
                for equipment_id in ["CMP_CU01", "CMP_CU02", "CMP_CU03"]
                for head_no in range(1, 5)
                if (equipment_id, head_no) != ("CMP_CU03", 2)
            ]
        )
    if operation_no == "6410":
        return "WET_BEOL_01", "WET_BEOL_01_CH01"
    if operation_no == "6500":
        return "KLA_INSPECT_01", "KLA_INSPECT_01_CH01"
    if operation_no == "9000":
        return "WAT_TEST_01", "WAT_TEST_01_CH01"
    raise ValueError(f"unsupported operation: {operation_no}")


def build_dynamic_rows(seed: int) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    base_time = datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    normal_lots = [f"LOT_N_{index:03d}" for index in range(1, 31)]
    affected_lots = [f"LOT_A_{index:03d}" for index in range(1, 21)]
    all_lots = normal_lots + affected_lots

    rows: dict[str, list[dict[str, Any]]] = {
        "lot_master": [],
        "wafer_master": [],
        "process_history": [],
        "recipe_history": [],
        "hold_history": [],
        "fdc_feature": [],
        "ooc_event": [],
        "defect_summary": [],
        "wat_result": [],
        "rca_case": [],
        "knowledge_document": [],
    }
    expected_evidence: list[dict[str, Any]] = []

    for lot_index, lot_id in enumerate(all_lots, start=1):
        affected = lot_id in affected_lots
        lot_start = base_time + timedelta(hours=lot_index * 3)
        rows["lot_master"].append(
            {
                "lot_id": lot_id,
                "product_id": "40N_SOC",
                "technology": "40nm",
                "route_id": "ROUTE_40N_SOC_A",
                "wafer_qty": "5",
                "lot_type": "PRODUCTION",
                "priority": "5" if not affected else "8",
                "status": "COMPLETE",
                "current_operation_no": "9000",
                "created_at": iso(lot_start - timedelta(hours=12)),
                "started_at": iso(lot_start),
                "finished_at": iso(lot_start + timedelta(hours=(len(OPERATIONS) + 1) * 3)),
            }
        )

        wafer_ids = [f"{lot_id}_W{wafer_no:02d}" for wafer_no in range(1, 6)]
        for wafer_no, wafer_id in enumerate(wafer_ids, start=1):
            rows["wafer_master"].append(
                {
                    "wafer_id": wafer_id,
                    "lot_id": lot_id,
                    "wafer_no": str(wafer_no),
                    "slot": str(wafer_no),
                    "status": "COMPLETE",
                }
            )

        cu_cmp_equipment_id = ""
        cu_cmp_chamber_id = ""
        operation_windows: dict[str, tuple[datetime, datetime]] = {}
        for op_index, operation in enumerate(OPERATIONS, start=1):
            started_at = lot_start + timedelta(hours=op_index * 3)
            ended_at = started_at + timedelta(minutes=45)
            operation_windows[operation.operation_no] = (started_at, ended_at)
            equipment_id, chamber_id = equipment_for_operation(
                operation.operation_no,
                lot_index,
                affected,
                rng,
            )
            if operation.operation_no == "6400":
                cu_cmp_equipment_id = equipment_id
                cu_cmp_chamber_id = chamber_id
            recipe_id, recipe_version = recipe_for_operation(operation.operation_no)
            wafer_id = wafer_ids[0]
            rows["process_history"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "route_id": "ROUTE_40N_SOC_A",
                    "operation_no": operation.operation_no,
                    "operation_name": operation.operation_name,
                    "module": operation.module,
                    "equipment_id": equipment_id,
                    "chamber_id": chamber_id,
                    "recipe_id": recipe_id,
                    "recipe_version": recipe_version,
                    "started_at": iso(started_at),
                    "ended_at": iso(ended_at),
                    "operator_id": "op_cmp" if operation.operation_no == "6400" else "op_auto",
                    "process_result": "PASS",
                }
            )
            rows["recipe_history"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "operation_no": operation.operation_no,
                    "equipment_id": equipment_id,
                    "chamber_id": chamber_id,
                    "recipe_id": recipe_id,
                    "recipe_version": recipe_version,
                    "executed_at": iso(started_at),
                }
            )

        rows["fdc_feature"].extend(
            build_fdc_features(
                lot_id,
                wafer_ids[0],
                affected,
                operation_windows["6400"][0],
                cu_cmp_equipment_id,
                cu_cmp_chamber_id,
            )
        )
        if affected:
            rows["ooc_event"].extend(build_ooc_events(lot_id, operation_windows["6400"][0]))
            rows["hold_history"].append(
                {
                    "hold_id": f"HOLD_{lot_id}",
                    "lot_id": lot_id,
                    "wafer_id": wafer_ids[0],
                    "hold_type": "ENGINEERING",
                    "hold_code": "YIELD_REVIEW",
                    "hold_reason": "Yield excursion review",
                    "hold_comment": (
                        "Scratch defect and leakage fail increased after Cu CMP; "
                        "suspect CMP_CU03_CH02 slurry delivery degradation."
                    ),
                    "created_by": "yield_eng",
                    "created_at": iso(operation_windows["9000"][1] + timedelta(hours=1)),
                    "released_by": "",
                    "released_at": "",
                    "release_comment": "",
                }
            )

        rows["defect_summary"].append(
            build_defect_row(lot_id, wafer_ids[0], affected, operation_windows["6500"][1])
        )
        rows["wat_result"].append(
            build_wat_row(lot_id, wafer_ids[0], affected, operation_windows["9000"][1])
        )

    rows["rca_case"].append(
        {
            "case_id": "RCA_CMP_2025_032",
            "title": "Cu CMP slurry delivery degradation caused scratch and leakage fail",
            "technology": "40nm",
            "module": "Cu CMP",
            "equipment_type": "CMP",
            "symptom": "Scratch defect increase, endpoint time shift, leakage fail increase",
            "root_cause": "Slurry pump degradation reduced slurry flow on Cu CMP chamber",
            "solution": "Inspect slurry pump, replace filter, calibrate flow controller, run qual wafers",
            "confidence": "0.91",
            "created_at": "2025-09-12T00:00:00+00:00",
        }
    )
    rows["knowledge_document"].append(
        {
            "document_id": "DOC_RCA_CMP_2025_032",
            "case_id": "RCA_CMP_2025_032",
            "document_type": "RCA_CASE",
            "title": "Historical Cu CMP slurry delivery RCA",
            "content": (
                "Historical RCA case: Cu CMP slurry delivery degradation produced "
                "slurry flow decline, endpoint time increase, scratch defect growth, "
                "and WAT leakage failures. Corrective action was slurry pump/filter "
                "replacement and chamber requalification."
            ),
            "tags": "Cu CMP;slurry_flow;scratch;leakage;CMP_CU03",
            "created_at": "2025-09-12T00:00:00+00:00",
        }
    )

    expected_evidence.extend(
        [
            {
                "evidence_id": "EV_MES_COMMON_CHAMBER",
                "source_table": "process_history",
                "summary": "Affected lots concentrate on operation 6400 Cu CMP and CMP_CU03_CH02.",
            },
            {
                "evidence_id": "EV_FDC_SLURRY_FLOW",
                "source_table": "fdc_feature",
                "summary": "Affected lots show slurry_flow observed around 132 ml/min vs 150 baseline.",
            },
            {
                "evidence_id": "EV_FDC_ENDPOINT_TIME",
                "source_table": "fdc_feature",
                "summary": "Affected lots show endpoint_time increase around 105 s vs 90 s baseline.",
            },
            {
                "evidence_id": "EV_OOC_EVENTS",
                "source_table": "ooc_event",
                "summary": "High-severity OOC events exist on CMP_CU03_CH02.",
            },
            {
                "evidence_id": "EV_HOLD_COMMENT",
                "source_table": "hold_history",
                "summary": "Hold comments mention scratch, leakage, and CMP_CU03_CH02 slurry delivery.",
            },
            {
                "evidence_id": "EV_DEFECT_SCRATCH",
                "source_table": "defect_summary",
                "summary": "Affected lots show scratch defect increase with edge dominant pattern.",
            },
            {
                "evidence_id": "EV_WAT_LEAKAGE",
                "source_table": "wat_result",
                "summary": "Affected lots show leakage fail increase.",
            },
            {
                "evidence_id": "EV_KNOWLEDGE_MATCH",
                "source_table": "rca_case",
                "summary": "Historical RCA case matches Cu CMP slurry delivery degradation.",
            },
        ]
    )

    rows["ground_truth"] = [
        {
            "seed": seed,
            "product_id": "40N_SOC",
            "time_window": {"start": "2026-07-01", "end": "2026-07-31"},
            "root_cause": "CMP_CU03_CH02 slurry delivery degradation",
            "affected_lots": affected_lots,
            "normal_lots": normal_lots,
            "affected_operation": "6400",
            "affected_equipment": "CMP_CU03",
            "affected_chamber": "CMP_CU03_CH02",
            "expected_confidence_range": [0.85, 0.95],
            "expected_evidence": expected_evidence,
        }
    ]
    return rows


def build_fdc_features(
    lot_id: str,
    wafer_id: str,
    affected: bool,
    cu_cmp_started_at: datetime,
    equipment_id: str,
    chamber_id: str,
) -> list[dict[str, str]]:
    measured_at = iso(cu_cmp_started_at + timedelta(minutes=15))
    if affected:
        slurry_observed = "132.0"
        endpoint_observed = "105.0"
        severity = "HIGH"
        ooc = "true"
    else:
        slurry_observed = "149.0"
        endpoint_observed = "90.5"
        severity = "NORMAL"
        ooc = "false"
    return [
        {
            "lot_id": lot_id,
            "wafer_id": wafer_id,
            "operation_no": "6400",
            "equipment_id": equipment_id,
            "chamber_id": chamber_id,
            "recipe_id": "CU_CMP_40N",
            "recipe_version": "R18",
            "parameter_name": "slurry_flow",
            "baseline_value": "150.0",
            "observed_value": slurry_observed,
            "delta_percent": "-12.0" if affected else "-0.7",
            "unit": "ml/min",
            "trend_slope": "-0.8" if affected else "0.0",
            "ooc_flag": ooc,
            "severity": severity,
            "measured_at": measured_at,
        },
        {
            "lot_id": lot_id,
            "wafer_id": wafer_id,
            "operation_no": "6400",
            "equipment_id": equipment_id,
            "chamber_id": chamber_id,
            "recipe_id": "CU_CMP_40N",
            "recipe_version": "R18",
            "parameter_name": "endpoint_time",
            "baseline_value": "90.0",
            "observed_value": endpoint_observed,
            "delta_percent": "16.7" if affected else "0.6",
            "unit": "s",
            "trend_slope": "0.6" if affected else "0.0",
            "ooc_flag": ooc,
            "severity": severity,
            "measured_at": measured_at,
        },
    ]


def build_ooc_events(lot_id: str, cu_cmp_started_at: datetime) -> list[dict[str, str]]:
    triggered_at = iso(cu_cmp_started_at + timedelta(minutes=20))
    return [
        {
            "feature_id": "",
            "equipment_id": "CMP_CU03",
            "chamber_id": "CMP_CU03_CH02",
            "operation_no": "6400",
            "parameter_name": "slurry_flow",
            "alarm_type": "MEAN_SHIFT",
            "severity": "HIGH",
            "triggered_at": triggered_at,
            "description": f"{lot_id}: slurry_flow below control limit on CMP_CU03_CH02",
        }
    ]


def build_defect_row(
    lot_id: str,
    wafer_id: str,
    affected: bool,
    inspected_at: datetime,
) -> dict[str, str]:
    if affected:
        defect_count = "320"
        density = "1.28"
        pattern = "edge_dominant"
    else:
        defect_count = "48"
        density = "0.19"
        pattern = "random"
    return {
        "lot_id": lot_id,
        "wafer_id": wafer_id,
        "inspection_operation_no": "6500",
        "defect_type": "scratch",
        "defect_count": defect_count,
        "defect_density": density,
        "pattern_type": pattern,
        "location_region": "edge" if affected else "full_wafer",
        "inspected_at": iso(inspected_at),
    }


def build_wat_row(
    lot_id: str,
    wafer_id: str,
    affected: bool,
    tested_at: datetime,
) -> dict[str, str]:
    return {
        "lot_id": lot_id,
        "wafer_id": wafer_id,
        "test_item": "WAT_LEAKAGE",
        "parameter_name": "iddq_leakage",
        "measured_value": "14.8" if affected else "2.1",
        "spec_low": "0.0",
        "spec_high": "5.0",
        "pass_fail": "false" if affected else "true",
        "fail_mode": "leakage" if affected else "",
        "tested_at": iso(tested_at),
    }


def generate_dataset(output_dir: Path, seed: int) -> None:
    static_rows = build_static_rows()
    dynamic_rows = build_dynamic_rows(seed)
    all_rows = {
        **static_rows,
        **{key: value for key, value in dynamic_rows.items() if key != "ground_truth"},
    }

    for table_name, rows in all_rows.items():
        write_csv(output_dir / f"{table_name}.csv", rows)

    write_json(output_dir / "ground_truth.json", dynamic_rows["ground_truth"][0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the golden synthetic Fab dataset.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    generate_dataset(args.output_dir, args.seed)
    print(f"Generated golden dataset at {args.output_dir} with seed {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
