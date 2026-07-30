# Multi-Case Reliability Validation

## Purpose

`data/seeds/multi_case` is a deterministic offline validation set. FastAPI
only reads the generated data and never runs the generator during startup or
request handling.

The dataset contains `LOT_A_001` through `LOT_A_075`. Every Lot has 25 Wafers,
and every Wafer follows the same complete 40N_SOC route through Thin Film,
ILD/W/Cu processing, CMP, inspection, and WAT. Lot IDs identify Product A and
sequence only; they do not encode the suspected module or failure state.

## Start

```powershell
.\scripts\stop_demo.ps1
.\scripts\start_demo.ps1 -Dataset multi_case
```

To use the generated CSV files without PostgreSQL:

```powershell
$env:YIELD_RCA_SEED_DIR="data/seeds/multi_case"
.\.venv\Scripts\python.exe -m uvicorn yield_rca_api.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

## Lot-Driven Cases

| Case | Source Lot | Expected result | Expected scope |
| --- | --- | --- | --- |
| Cu CMP slurry window | `LOT_A_015` | Supported: `CMP_CU03_CH02 slurry delivery degradation` | Impact Lots `LOT_A_011` through `LOT_A_014` |
| Isolated scratch | `LOT_A_038` | `inconclusive`, confidence no higher than 60% | Only `LOT_A_038_W07`; no impact Lots |
| ILD odd/even thickness | `LOT_A_063` | Supported: `CVD_ILD_01_CH02 deposition rate excursion` | 12 even Wafers in the source Lot |

### Cu CMP Checks

- Non-normal FDC features begin before the single threshold-crossing OOC.
- Slurry flow and estimated removal rate decrease while endpoint time rises.
- The one high-severity OOC triggers an equipment/Lot containment Hold.
- Earlier suspect Lots are found from the non-normal FDC window, not from one
  OOC row per Lot.
- Cu residue and leakage/short evidence support the under-polish mechanism.

### Isolated Scratch Checks

- Only Wafer 07 has one scratch record.
- The source Lot and neighboring Lots have normal CMP FDC and no matching OOC.
- KLA detection creates a Quality Hold after inspection.
- The system does not invent an equipment root cause or additional impact Lots.

### ILD Thickness Checks

- Odd Wafers use `CVD_ILD_01_CH01`; even Wafers use
  `CVD_ILD_01_CH02` in the source Lot.
- Pre-CMP and post-CMP metrology are low only on even Wafers.
- ILD CMP heads are balanced across odd/even Wafers, and CMP FDC is normal.
- The system attributes the split to the upstream deposition chamber rather
  than to CMP.

## Product-Window Regression

The Cu yield excursion also remains queryable in product-window mode:

```text
Analyze 40N_SOC yield drop from 2026-07-01 to 2026-07-07.
```

Expected affected Lots are `LOT_A_012` through `LOT_A_015`.
`LOT_A_011` is a passing suspect Lot inside the FDC drift window and is excluded
from the normal reference population.

## Automated Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_multi_case_dataset_contracts.py tests/integration/test_multi_case_workflow.py -q
```

The contracts validate naming, complete per-Wafer genealogy, equipment
capability, OOC/Hold timing, isolated defects, odd/even chamber routing,
metrology, impact scope, supported/inconclusive behavior, and evidence
traceability.
