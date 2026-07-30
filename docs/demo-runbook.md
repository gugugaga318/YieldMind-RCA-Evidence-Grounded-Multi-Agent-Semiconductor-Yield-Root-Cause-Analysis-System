# MVP Demo Runbook

## Goal

Demonstrate one complete semiconductor Yield RCA workflow using the 40N_SOC
golden case and a PostgreSQL-backed FastAPI runtime.

The Synthetic Fab generator remains an offline seed tool. Neither FastAPI nor
React invokes it during startup or request handling.

## Prerequisites

- Python project environment installed at `.venv`
- PostgreSQL reachable through `YIELD_RCA_DATABASE_URL` or `TEST_DATABASE_URL`
- `pnpm` and frontend dependencies installed
- Ports `8000` and `5173` available

Example database configuration for the current PowerShell session:

```powershell
$env:YIELD_RCA_DATABASE_URL="postgresql://user:password@localhost:5432/yield_rca"
```

Do not commit a real database password to the repository.

## Start The Demo

From the repository root:

```powershell
.\scripts\start_demo.ps1
```

If the current PowerShell execution policy blocks local scripts:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

The command performs these steps in order:

```text
1. generate golden dataset offline
2. reset and seed PostgreSQL
3. build the React dashboard
4. start FastAPI with YIELD_RCA_DATABASE_URL
5. start the React production preview
```

To reuse already generated data or an existing frontend build:

```powershell
.\scripts\start_demo.ps1 -SkipGenerate -SkipBuild
```

## Demonstration Flow

1. Open `http://127.0.0.1:5173`.
2. Submit: `Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31.`
3. Confirm the RCA Job status is `completed`.
4. Confirm the Agent Workflow shows `5/5 complete`.
5. Inspect MES, FDC, Defect/WAT, and Knowledge items in the Evidence Chain.
6. Confirm the supported root cause is `CMP_CU03_CH02 slurry delivery degradation`.
7. Open the Report tab and review the Markdown report and Referenced Records.

## Lot-Driven Demonstration Flow

1. In the dashboard, select `Lot ID` investigation mode.
2. Enter `LOT_A_001` and run RCA.
3. Confirm the investigated Lot is `LOT_A_001`.
4. Confirm 19 additional impact Lots and 20 total exposed Lots are shown.
5. Confirm the shared exposure is operation `6400` on
   `CMP_CU03/CMP_CU03_CH02` during the OOC window.
6. Confirm the root cause remains
   `CMP_CU03_CH02 slurry delivery degradation` at 95% confidence.
7. Open the report and verify `Lot Investigation Scope` and the referenced
   `EV_MES_IMPACT_LOTS` / `EV_FDC_EXCURSION_WINDOW` records.

The impact population is calculated from the seeded database at request time;
the frontend does not infer impact Lots and the API does not generate data.

## Expected Signals

```text
Affected lots:       20
Normal reference:    30
Target chamber:      CMP_CU03_CH02
Agent progress:      5/5 complete
Evidence records:    9
Confidence:          95%
Root cause:          CMP_CU03_CH02 slurry delivery degradation
```

Lot-driven expected scope:

```text
Investigated Lot:    LOT_A_001
Impact Lots:         19 (LOT_A_002 through LOT_A_020)
Total exposed:       20
Operation:           6400
Target chamber:      CMP_CU03_CH02
Confidence:          95%
```

Every RCA conclusion and recommended action must resolve to an evidence ID.

## Verified MVP Run

The PostgreSQL-backed flow was verified end to end on 2026-07-21:

```text
Job status:          completed
Agent progress:      5/5 complete
Selected evidence:  EV_FDC_SLURRY_FLOW
Observed/baseline:   132.0 / 150.0 (-12.0%)
Root cause:          CMP_CU03_CH02 slurry delivery degradation
Confidence:          95%
Report records:      9 traceable evidence rows
Warnings:            none
```

## Stop The Demo

```powershell
.\scripts\stop_demo.ps1
```

Runtime logs and recorded process IDs are written under `outputs/demo/`.

## Troubleshooting

- Database connection failure: start PostgreSQL and verify the configured URL.
- Port already in use: stop the existing process or run `scripts\stop_demo.ps1`.
- API starts but the result is unexpected: verify the backend was launched with
  `YIELD_RCA_DATABASE_URL`; otherwise it intentionally falls back to CSV seeds.
- Dashboard cannot submit: confirm both `http://127.0.0.1:8000/docs` and
  `http://127.0.0.1:5173` respond.
