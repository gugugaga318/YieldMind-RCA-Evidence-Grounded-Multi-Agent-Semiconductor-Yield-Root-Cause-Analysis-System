# Step 14 Evaluation and Optimization

## Scope

Step 14 adds a deterministic offline evaluation layer after the MVP workflow.
It does not run from FastAPI, generate data at API startup, train a model, or
change the React Dashboard.

The suite projects ten read-only scenario variants over
`data/seeds/multi_case`. This avoids copying the complete per-Wafer Fab dataset
while preserving the same Repository, Tool, Specialist Agent, Supervisor, RCA,
and Report execution path.

## Scenarios

| Scenario | Expected behavior |
| --- | --- |
| CMP slurry flow decline | Supported Cu CMP slurry-delivery root cause |
| Recipe version change | Supported R19 recipe-change root cause |
| Single chamber abnormality | Supported `CVD_ILD_01_CH02` root cause |
| Scratch defect and WAT fail | Inconclusive without causal process evidence |
| MES commonality, normal FDC | Inconclusive |
| FDC drift, no yield impact | Inconclusive; no false-positive RCA |
| Conflicting evidence | Inconclusive with conflict Warning |
| Missing data | Inconclusive with missing-FDC Warning |
| High historical match | Inconclusive when current causal evidence is absent |
| Inconclusive root cause | Correct abstention for an isolated anomaly |

The machine-readable definitions and expected evidence/Warnings are in
`data/evaluation/scenarios.json`.

## Metrics

- **Top-1 root cause accuracy**: exact root-cause match on scenarios with a
  supported ground truth.
- **Top-3 recall**: supported ground truth appears in the RCA Agent's first
  three traceable candidates.
- **Inconclusive handling rate**: expected-inconclusive scenarios are correctly
  rejected instead of receiving an unsupported root cause.
- **Evidence traceability**: every Agent, hypothesis, Warning, candidate, and
  report citation resolves to an Evidence record in `RCAState`.
- **Hallucinated citation rate**: unknown Evidence-ID citation occurrences
  divided by all structured and Markdown citation occurrences.
- **Confidence calibration**: ECE and Brier score for supported root-cause
  predictions. Inconclusive confidence is excluded because it represents
  evidence sufficiency, not the probability that abstention is correct.
- **Tool latency**: call count, mean, P50, P95, and maximum latency across all
  Tools invoked by the scenarios, plus the same summary for each Tool name.
- **End-to-end latency**: mean, P50, P95, and maximum workflow runtime per
  scenario, including planning, Tools, Agents, reasoning, and report rendering.

The report also retains scenario pass rate, false-positive rate, impact
Lot/Wafer scope accuracy, and required Warning recall as regression guards.

The deterministic offline acceptance limits are Top-1/Top-3/inconclusive/
traceability at 100%, hallucinated citation rate at 0%, calibration ECE at or
below 0.15, Tool P95 at or below 1500 ms, and end-to-end P95 at or below
3000 ms. These latency limits are local regression limits, not production SLOs;
they must be re-baselined against PostgreSQL and the target deployment host.
The current calibration result has only three supported samples and is a
regression signal, not a statistically reliable production calibration claim.

An expected `inconclusive` result is counted as correct. A historical case
match is supporting context only and cannot override missing or conflicting
current evidence.

## Run

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py
```

The command exits non-zero when any acceptance threshold fails and writes:

```text
outputs/evaluation/results.json
outputs/evaluation/report.md
```

Run only the Step 14 tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_evaluation_contracts.py tests/integration/test_evaluation_suite.py -q
```

## Optimizations Added

1. MES compares a source Lot's recipe versions with prior Lots on the same
   route and emits `EV_MES_RECIPE_CHANGE`.
2. Recipe RCA uses MES, physical/electrical impact, and historical evidence;
   FDC abnormality is not mandatory for this decision path.
3. Slurry-flow decline combined with increasing estimated removal rate is
   treated as conflicting physics. RCA emits
   `WARN_RCA_CONFLICTING_EVIDENCE` and returns `inconclusive`.
4. An empty Defect/WAT/Metrology result now emits traceable negative evidence
   `EV_QUALITY_NO_IMPACT` instead of producing an AgentFinding without
   evidence.
5. Impact-window expansion now requires an abnormal signal on the source Lot;
   old Chamber history alone cannot expand the current impact population.
