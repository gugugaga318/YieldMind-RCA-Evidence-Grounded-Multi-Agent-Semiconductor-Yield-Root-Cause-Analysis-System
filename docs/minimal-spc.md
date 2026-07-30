# Minimal SPC Analytics

## Scope

Step 17 adds a deterministic `perform_basic_spc_analysis` Tool over the existing
`fdc_feature` summary records. Synthetic data generation remains offline, Agents
do not access repositories directly, and React does not calculate SPC.

This step does not implement a full SPC platform, Raw FDC trace processing,
real-time alerting, user-configurable chart rules, or automatic process control.

## Inputs

The Tool accepts structured inputs:

```text
lot_ids
operation_no
equipment_id
chamber_id
minimum_baseline_samples (default 20)
sigma_multiplier (default 3)
same_side_run_length (default 8)
trend_run_length (default 6)
```

## Baseline Selection

Reference records must precede the target window, be normal, not marked OOC,
remain outside the investigated Lot population, and be compatible with the
target operation and recipe. This prevents future-data leakage. The Tool uses
the narrowest population with enough samples:

```text
same chamber
  -> same equipment
  -> operation/recipe peer group
```

The selected level is returned as `baseline_scope`. When no level has enough
samples, the Tool does not fabricate limits. It returns
`WARN_SPC_BASELINE_INSUFFICIENT` and traceable `EV_SPC_BASELINE_STATUS` evidence.

## Calculations

For each parameter, the Tool calculates:

```text
center line = reference sample mean
sigma = reference sample standard deviation
LCL/UCL = center line +/- 3 sigma
target mean and mean z-score
points outside the control limits
maximum consecutive points on one side of the center line
monotonic trend windows
```

The MVP rules are:

```text
POINT_BEYOND_3_SIGMA
RUN_SAME_SIDE (8 points by default)
MONOTONIC_TREND (6 points by default)
```

These rules are evidence signals, not an autonomous production disposition.
RCA support still requires MES, FDC, Defect/WAT, and knowledge evidence to pass
the existing deterministic validation gates.

## Outputs

Each calculated parameter emits `EV_SPC_<PARAMETER>` evidence containing the
baseline scope, control limits, target statistics, triggered rules, and bounded
point-level traceability. FDC Agent includes the results in its `AgentFinding`,
and Report Generator renders them under `Minimal SPC Analysis`.
