# Advanced SPC Evidence for RCA

## Scope

Step 20 adds deterministic SPC analytics as an evidence provider for the Yield
RCA workflow. It is not a production SPC application and does not monitor the
Fab, acknowledge alarms, or execute MES Hold and Release commands.

Implemented charts and calculations:

- I-MR
- Xbar-S
- Xbar-R
- p-chart
- Nelson Rules 1-8
- Cp/Cpk and Pp/Ppk when specification limits exist

Capability values are marked informational when the analysis sequence is not
statistically stable.

## Baseline Contract

Every reference baseline is versioned and matched using the complete context:

```text
Product + Operation + Equipment + Chamber/Head
+ Recipe Version + Parameter
```

The Tool does not fall back from one chamber to another or mix STI, ILD, W, and
Cu CMP populations. Datasets without `spc_baseline_profile` continue using the
existing Minimal SPC compatibility path.

## OOC and Hold Semantics

```text
SPC rule violation
  -> one OOC Event
  -> one Trigger Lot
  -> optional Trigger Wafer
  -> one Trigger Hold
  -> one Excursion
       -> zero or more Impact Lots
       -> one containment Hold per Impact Lot
```

An Impact Lot is not another owner of the same OOC. It is a separately held Lot
inside the excursion exposure window.

## Offline Dataset

Generate the Step 20 dataset explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\generate_synthetic_spc_data.py
```

The command writes existing seed artifacts under `data/seeds/spc_case`. The
generator is never imported or called by FastAPI.

The dataset contains:

- June baseline Lots `LOT_A_076` through `LOT_A_105`
- July Cu CMP analysis Lots `LOT_A_011` through `LOT_A_015`
- lot-level Trigger Lot `LOT_A_015` (no Wafer-level trigger)
- Trigger Hold `HOLD_CU_OOC_001`
- Impact Lots `LOT_A_011` through `LOT_A_014`, each with an independent Hold
- versioned I-MR, Xbar-S, and p-chart profiles

## Run

Local launcher:

```powershell
.\scripts\start_demo.ps1 -Dataset spc_case
```

Docker Compose:

```powershell
# Set YIELD_RCA_DATASET=spc_case in .env first.
docker compose up -d db
docker compose --profile tools run --rm seed
docker compose up --build -d backend frontend
```

The Dashboard SPC panel shows control and specification limits, highlighted
violation samples, baseline identity and window, Trigger Hold, and independent
Impact Holds. React does not calculate SPC.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_spc_engine.py `
  tests/contract/test_spc_dataset_contracts.py `
  tests/integration/test_spc_workflow.py -q
```
