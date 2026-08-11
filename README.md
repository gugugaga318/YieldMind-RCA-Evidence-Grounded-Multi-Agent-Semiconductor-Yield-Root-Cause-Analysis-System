# Semiconductor Yield RCA Multi-Agent System

This project is an MVP-first industrial AI Agent system for semiconductor yield root cause analysis (RCA).

The system simulates how a Yield Engineer investigates a yield excursion by combining MES genealogy, FDC feature summaries, defect/WAT evidence, historical RCA cases, and structured multi-agent reasoning.

## Project Goal

Build a planner-driven Multi-Agent RCA platform that can:

- Detect and scope a yield excursion.
- Start an RCA investigation from a known abnormal `lot_id`.
- Identify affected lots and wafer history.
- Identify impact Lots from shared operation/equipment/chamber exposure during an OOC window.
- Analyze process, equipment, chamber, recipe, and hold-comment commonality.
- Check FDC feature drift and OOC events.
- Calculate Minimal SPC control limits and bounded rule violations from FDC feature summaries.
- Correlate defect and WAT symptoms.
- Retrieve similar historical RCA cases.
- Generate an evidence-backed RCA report.
- Generate evidence-backed containment, corrective, Recipe, preventive, and Fab-level
  improvement recommendations.

The first milestone is not a full industrial system. The MVP goal is to run one complete golden case end to end.

## Canonical Design Source

The project design is defined in:

- [DESIGN_DOC_FULL.md](DESIGN_DOC_FULL.md)
- [docs/architecture/system-overview.md](docs/architecture/system-overview.md)
- [docs/architecture/agent-architecture.md](docs/architecture/agent-architecture.md)
- [docs/architecture/data-architecture.md](docs/architecture/data-architecture.md)
- [docs/autonomous-qwen-react-spec.md](docs/autonomous-qwen-react-spec.md)

Implementation work must follow these documents unless an ADR explicitly changes the design.

## MVP Scope

MVP includes:

- Offline synthetic golden dataset generation and database seed.
- PostgreSQL schema for MES, FDC feature summary, Defect/WAT, and knowledge metadata.
- Tool Layer for engineering queries.
- MES, FDC, Defect/WAT, and Knowledge specialist agents.
- Planner, Supervisor, RCA Reasoning Agent, and Report Generator.
- Improvement Agent with report integration and controlled memory candidates.
- Dual-engineer approval before confirmed knowledge publication.
- Pure Python end-to-end workflow before API or frontend work.
- Basic FastAPI wrapper after the Python workflow is stable.
- Basic React dashboard after the API is stable.
- Product/time-window and Lot-driven investigation modes.

MVP explicitly excludes:

- Raw FDC sensor stream processing.
- Vision Agent and image analysis.
- Real-time Fab data integration.
- Full SPC platform.
- Large-scale industrial performance targets.
- Batch RCA accuracy benchmarking.

## Non-Negotiable Architecture Rules

1. Synthetic Fab data generation is an offline seed workflow, not FastAPI runtime behavior.
2. Agents must not directly access the database or repositories.
3. Agents must use the Tool Layer.
4. RCA conclusions must cite traceable evidence.
5. React displays RCA results; it does not compute SPC or RCA logic.
6. FastAPI wraps the core Python workflow; it does not generate synthetic data.
7. Only engineer-confirmed memory may become high-weight historical evidence.

## MVP Execution Order

Recommended order:

```text
Step 0  Project baseline and docs
Step 1  Python core package
Step 2  Domain models / RCAState / DTOs
Step 3  PostgreSQL schema
Step 4  Golden synthetic dataset + seed
Step 5  Repository + Tool Layer
Step 6  Specialist agents
Step 7  Planner agent
Step 8  RCA reasoning agent
Step 9  Report generator
Step 10 Supervisor + pure Python workflow
Step 11 FastAPI backend
Step 12 React dashboard
Step 13 MVP demo
Step 14 Evaluation and optimization
Step 15 Docker Compose and runtime configuration
Step 16 Qwen Hybrid Agents, observability, and audit foundation
Step 17 Minimal SPC Analytics Tool and report integration
Step 18 Improvement Agent and report integration
Step 19 Dual-engineer memory approval and confirmed knowledge publication
```

## Run the Golden RCA Workflow

The Step 10 pure Python workflow runs against the existing offline golden seed dataset. It does not generate or modify Synthetic Fab data.

From the repository root:

```powershell
python scripts\run_golden_rca.py --no-print-report
```

The command executes:

```text
Planner
  -> Supervisor
  -> MES / FDC / Defect-WAT / Knowledge Agents
  -> RCA Reasoning
  -> Report Generator
```

Generated artifacts:

```text
outputs/golden_rca_run/rca_state.json
outputs/golden_rca_run/rca_report.md
```

## Lot-Driven RCA

Lot-driven RCA starts from one known abnormal Lot and reuses the same Specialist,
reasoning, and reporting workflow:

```text
abnormal lot_id
  -> resolve product, route, WAT, and genealogy context
  -> derive the relevant OOC excursion window
  -> find Lots with matching operation/equipment/chamber time overlap
  -> run FDC, Defect/WAT, and Knowledge analysis
  -> produce an evidence-backed root cause and Markdown report
```

For the golden dataset, `LOT_A_001` resolves to 19 additional impact Lots. The
source Lot is excluded from `impact_lots` and included in the total exposed
population. The impact list is derived from `process_history` and `ooc_event`;
it is not hardcoded in the API or frontend.

Omit `--no-print-report` to also print the Markdown report in the terminal. To run against an already seeded PostgreSQL database, pass `--database-url`.

## Current Step

This repository has completed Step 19: controlled RCA memory persistence,
dual-engineer approval, and confirmed knowledge publication.

The complete demo generates the golden dataset offline, seeds PostgreSQL,
starts the FastAPI and React services, submits the golden RCA query, and exposes
the traceable evidence chain and Markdown report. Synthetic Fab generation
remains offline-only, and the frontend performs no SPC or RCA computation.

Step 15 adds reproducible PostgreSQL, FastAPI, and Nginx/React containers.
Database initialization remains an explicit offline seed command and is not
part of normal Compose or API startup.

Step 16 adds explicit `deterministic`, `fake`, and `llm` modes. Planner,
Specialist interpretation, and RCA candidate ranking can use DashScope
`qwen-plus`; Tool execution, evidence validation, conflict gates, and final
support thresholds remain deterministic. See
[ADR-005](docs/adr/ADR-005-qwen-hybrid-agent-observability.md) and
[docs/observability.md](docs/observability.md).

Step 17 adds deterministic baseline selection, sample standard deviation,
3-sigma control limits, point/run/trend rules, traceable `EV_SPC_*` evidence,
and a Minimal SPC section in the Markdown report. It does not implement Raw FDC,
real-time monitoring, configurable control-chart administration, or a full SPC
platform. See [docs/minimal-spc.md](docs/minimal-spc.md).

Step 18 executes Improvement Agent after RCA Reasoning. It distinguishes an
event conclusion from a Fab-level conclusion, emits five recommendation layers,
and includes every recommendation in the Markdown report with evidence IDs.
Inconclusive RCA cannot produce root-cause-specific or Fab-level actions. Step
18 does not write long-term memory. See
[docs/improvement-agent.md](docs/improvement-agent.md).

Step 19 creates a pending memory candidate only for supported RCA conclusions.
Event-level and Fab-level candidates require two different engineers. A
candidate containing Recipe recommendations also requires one Process Engineer
among those two approvers. Only published `CONFIRMED` cases are available as
high-weight historical knowledge. See
[docs/memory-approval.md](docs/memory-approval.md).

## Run the FastAPI Backend

Install the project in a Python 3.11+ virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Start the API from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn yield_rca_api.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

Create and inspect a golden RCA job from PowerShell:

```powershell
$body = @{
  user_query = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
} | ConvertTo-Json

$job = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/rca/jobs" `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000$($job.state_url)"
Invoke-RestMethod -Uri "http://127.0.0.1:8000$($job.report_url)"
```

Approve the generated memory candidate with two different engineers:

```powershell
$firstApproval = @{
  engineer_id = "YE001"
  engineer_role = "yield_engineer"
  decision = "approve"
  comment = "RCA evidence reviewed."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/memory/candidates/$($job.memory_candidate_id)/approvals" `
  -ContentType "application/json" `
  -Body $firstApproval

$secondApproval = @{
  engineer_id = "PE001"
  engineer_role = "process_engineer"
  decision = "approve"
  comment = "Recipe DOE gate reviewed; no direct production change."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/memory/candidates/$($job.memory_candidate_id)/approvals" `
  -ContentType "application/json" `
  -Body $secondApproval
```

Create a Lot-driven RCA job:

```powershell
$body = @{
  investigation_mode = "lot"
  lot_id = "LOT_A_001"
} | ConvertTo-Json

$job = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/rca/jobs" `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod -Uri "http://127.0.0.1:8000$($job.state_url)"
Invoke-RestMethod -Uri "http://127.0.0.1:8000$($job.report_url)"
```

Step 11 executes `POST /rca/jobs` synchronously and stores completed `RCAState` objects in process memory. Restarting the API clears those jobs. Durable job persistence and asynchronous workers are later production extensions.

By default, the API reads the existing files under `data/seeds/golden_case`. It does not invoke the Synthetic Fab generator. To query an already seeded PostgreSQL database instead, set:

```powershell
$env:YIELD_RCA_DATABASE_URL="postgresql://user:password@localhost:5432/yield_rca"
```

The supported endpoints are:

```text
POST /rca/jobs
GET  /rca/jobs/{job_id}
GET  /rca/jobs/{job_id}/report
GET  /rca/jobs/{job_id}/memory-candidate
GET  /memory/candidates/{candidate_id}
POST /memory/candidates/{candidate_id}/approvals
GET  /health
GET  /ready
GET  /metrics
```

## Run the React Dashboard

Install the frontend dependencies from the repository root:

```powershell
cd frontend
pnpm install
```

Keep the FastAPI backend running on `http://127.0.0.1:8000`, then start the
dashboard in a second terminal:

```powershell
cd frontend
pnpm dev
```

Open `http://127.0.0.1:5173`. The Vite development server proxies `/api`
requests to FastAPI. No Synthetic Fab data is generated when the dashboard or
API starts.

Frontend verification commands:

```powershell
cd frontend
pnpm run check
pnpm run build
```

To inspect the production build locally:

```powershell
cd frontend
pnpm preview
```

## Run the MVP Demo

The Step 13 demo uses the PostgreSQL-backed workflow and preserves the offline
Synthetic Fab boundary. Configure `YIELD_RCA_DATABASE_URL` or
`TEST_DATABASE_URL`, then run:

```powershell
.\scripts\start_demo.ps1
```

If the local PowerShell execution policy blocks repository scripts, run the
same launcher without changing the machine-wide policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_demo.ps1
```

Open `http://127.0.0.1:5173` and follow the seven-step checklist in
[docs/demo-runbook.md](docs/demo-runbook.md). Stop the recorded demo processes
with:

```powershell
.\scripts\stop_demo.ps1
```

The verified golden run returns 20 affected lots, 30 normal lots, a `5/5`
completed Agent workflow, nine evidence records, and the supported root cause
`CMP_CU03_CH02 slurry delivery degradation` at 95% confidence.

The verified Lot-driven run for `LOT_A_001` returns 19 additional impact Lots,
20 total exposed Lots, the same `5/5` Agent workflow, a traceable impact-scope
record, and the same supported root cause at 95% confidence.

## Multi-Case Reliability Dataset

The original `golden_case` remains the fixed regression baseline. A separate
offline dataset adds a Cu CMP equipment-window excursion, an isolated
single-Wafer scratch, and an upstream ILD deposition odd/even Wafer split:

```powershell
.\scripts\stop_demo.ps1
.\scripts\start_demo.ps1 -Dataset multi_case
```

Use the product-window queries and source Lot IDs in
[docs/multi-case-validation.md](docs/multi-case-validation.md). The combined
dataset is generated under `data/seeds/multi_case`; API startup does not invoke
either Synthetic Fab generator.

## Run Step 20 Advanced SPC Evidence

Step 20 adds deterministic SPC evidence to RCA without building a separate
production SPC system. It includes I-MR, Xbar-S, Xbar-R, p-chart, Nelson Rules
1-8, capability indices, versioned strict baselines, and explicit OOC / Trigger
Hold / Impact Hold relationships.

```powershell
.\scripts\stop_demo.ps1
.\scripts\start_demo.ps1 -Dataset spc_case
```

The offline dataset is stored under `data/seeds/spc_case`; FastAPI never
generates it. See [docs/advanced-spc.md](docs/advanced-spc.md) for the data
contract and verification commands.

## Run Step 14 Evaluation

After the MVP and multi-case workflow pass, run the deterministic offline
evaluation suite:

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py
```

The suite covers ten positive, negative, conflicting, missing-data, and
correct-abstention scenarios. Results are written to
`outputs/evaluation/results.json` and `outputs/evaluation/report.md`. See
[docs/evaluation.md](docs/evaluation.md) for scenario definitions, metrics, and
acceptance rules.

## Run Step 15 with Docker Compose

Copy `.env.example` to `.env`, replace the local PostgreSQL password, then run:

```powershell
docker compose up -d db
docker compose --profile tools run --rm seed
docker compose up --build -d backend frontend
```

Open the dashboard at `http://127.0.0.1:5173` and API documentation at
`http://127.0.0.1:8000/docs`. Normal Compose startup never generates Synthetic
Fab data or resets the database. See
[docs/deployment/docker-compose.md](docs/deployment/docker-compose.md) for the
full initialization, operation, and shutdown procedure.

## Run Step 16 Agent Modes

Validate the complete LLM path without a paid API call:

```powershell
$env:YIELD_RCA_AGENT_MODE="fake"
$env:YIELD_RCA_ORCHESTRATION_MODE="llm_react"
```

Run against Qwen after adding the key to the local `.env` file:

```text
YIELD_RCA_AGENT_MODE=llm
YIELD_RCA_ORCHESTRATION_MODE=llm_react
YIELD_RCA_LLM_MODEL=qwen-plus
DASHSCOPE_API_KEY=<local-secret>
```

Reapply the explicit local seed/migrations before restarting the containers:

```powershell
docker compose --profile tools run --rm seed
docker compose up --build -d backend frontend
```

The Dashboard displays Agent mode, model, prompt version, token usage, LLM
latency, and Tool call count. API keys are runtime-only and are never included
in logs or frontend responses.

### Batch 21.2 product-surface and semantic final evaluation

Run the repeatable Fake-Qwen autonomous matrix together with the preserved
Controlled ReAct path, fixed-workflow compatibility baseline, and semantic
negative cases:

```powershell
& .\.venv\Scripts\python.exe scripts\run_autonomous_qwen_evaluation.py
```

The command performs no paid network call. A passing run reports Autonomous
Fake `10/10`, Controlled ReAct `PASS`, Fixed Workflow `10/10`, and a passing
material-trace negative case. It writes stable, secret-free artifacts to:

```text
outputs/autonomous_qwen_react_evaluation/results.json
outputs/autonomous_qwen_react_evaluation/report.md
```

The optional real-Qwen status remains separate from deterministic acceptance.

### Optional paid Qwen smoke test

The real-Qwen integration smoke test runs a bounded `LOT_A_001` impact-scope
investigation through Intent Planner, Next-action Planner, Specialist V2, and
the final deterministic decision evaluation. It is skipped unless both a
non-empty `DASHSCOPE_API_KEY` and the explicit opt-in
`RUN_REAL_QWEN_TEST=1` are present. The test can make paid DashScope requests;
it disables HTTP retries and stops before a thirteenth LLM call.

Run it from the repository root without putting the key in shell history:

```powershell
$previousApiKey = [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Process")
$previousRunFlag = [Environment]::GetEnvironmentVariable("RUN_REAL_QWEN_TEST", "Process")
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    $env:RUN_REAL_QWEN_TEST = "1"
    & .\.venv\Scripts\python.exe tests\integration\test_qwen_optional.py -v
} finally {
    if ($null -eq $previousApiKey) {
        Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DASHSCOPE_API_KEY = $previousApiKey
    }
    if ($null -eq $previousRunFlag) {
        Remove-Item Env:RUN_REAL_QWEN_TEST -ErrorAction SilentlyContinue
    } else {
        $env:RUN_REAL_QWEN_TEST = $previousRunFlag
    }
    Remove-Variable qwenSecret, previousApiKey, previousRunFlag -ErrorAction SilentlyContinue
}
```

Without the key or opt-in flag, the same command reports the paid smoke test as
skipped rather than passed. Do not commit `.env`, test output containing raw
model responses, or any API credential.

### Real Qwen Intent Planner diagnosis

When an `llm_react` request hands off during Intent Planning, run the isolated
diagnostic before changing a Prompt or validation rule. It executes the same
Scratch + Cu CMP full-RCA request prefilled by the frontend (root cause plus
impact Lots) at the Intent Planner boundary, never enters Next Action
Planning or any Specialist/Tool path, and permits at most two paid calls per
run. Three runs therefore have a hard maximum of six paid calls.

```powershell
$previousApiKey = [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Process")
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    & .\.venv\Scripts\python.exe scripts\run_qwen_intent_diagnosis.py `
        --confirm-paid-qwen `
        --runs 3
} finally {
    if ($null -eq $previousApiKey) {
        Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DASHSCOPE_API_KEY = $previousApiKey
    }
    Remove-Variable qwenSecret, previousApiKey -ErrorAction SilentlyContinue
}
```

The runner distinguishes provider failure, JSON/output parsing, typed contract
validation, and semantic guard rejection. It aggregates stable reason codes and
field paths under `outputs/qwen_intent_diagnosis/`. Results contain only bounded
candidate shape summaries, safe invalid `question_kind` indexes/tokens, and
baseline differences; the API key, complete
Prompt, user-query payload, and raw Qwen response are never written.

### Repeated Qwen Planner-review reliability evaluation

The single impact-scope smoke test is intentionally cheap. After changing the
Planner output contract, use the stricter Scratch + Cu CMP reliability runner to
exercise observation, re-planning, QuestionUpdate review, and the final stop
three consecutive times. Every run has an independent hard limit of 20 paid LLM
calls, hidden Gateway HTTP retries are disabled, and the command refuses to
start without the explicit `--confirm-paid-qwen` flag. The Next-action Planner
may retry one transient transport, 408, 429, or 5xx failure through the capped
client, so that paid retry is visible inside the same 20-call boundary.

```powershell
$previousApiKey = [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Process")
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    & .\.venv\Scripts\python.exe scripts\run_qwen_reliability_evaluation.py `
        --confirm-paid-qwen `
        --runs 3 `
        --max-llm-calls-per-run 20
} finally {
    if ($null -eq $previousApiKey) {
        Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DASHSCOPE_API_KEY = $previousApiKey
    }
    Remove-Variable qwenSecret, previousApiKey -ErrorAction SilentlyContinue
}
```

Acceptance requires all three runs to remain on `llm_react`, start with defect
inspection, re-plan after the first observation, keep accepted updates as compact
terminal `QuestionUpdate` deltas, audit every accepted or rejected claim, respect
the call cap, and pass the existing Goal Success and Stop Correct checks. A
rejected ancillary update does not fail a run when its legal Agent action was
preserved; an invalid core Decision or Action still triggers controlled fallback
and fails the reliability boundary. The report counts accepted and rejected
updates by stable reason code without storing prompts or raw model responses.
Failure diagnostics distinguish transport/provider errors, invalid JSON or
response envelopes, and typed core Decision validation. A recovered transient
retry remains on `llm_react`; a second call failure keeps bounded provider
diagnostics and fails through the existing controlled compatibility handoff.

Secret-free summaries are written under
`outputs/qwen_question_update_reliability/`; the directory is ignored by Git.
Goal Success permits an explicitly unavailable optional Question only when at
least one Question is Evidence-backed and closed, no Question remains open, no
Evidence gap remains, and the existing Evidence/Hypothesis gate supports the
conclusion. `QuestionUpdateReview` is persisted in `RCAState`, exposed by the
API, and rendered in the Agent Trace. No rejected update is silently converted
into `closed` or `unavailable`.

### Evaluation V2 causal Scope and four release gates

Run the reviewed Retrieval V2 ablation with the pinned local `bge-m3` model,
then run deterministic/Controlled RCA references and combine the four gates:

```powershell
& .\.venv\Scripts\python.exe scripts\run_evaluation_v2_retrieval.py `
    --embedding-backend sentence-transformers `
    --device auto
& .\.venv\Scripts\python.exe scripts\run_evaluation_v2_rca.py
& .\.venv\Scripts\python.exe scripts\run_evaluation_v2_release.py
```

The measured runtime selection is Chunk Keyword + causal-wide Scope, with
Hybrid-RRF and the Reranker left behind Feature Flags. The final report uses
independent Data Quality, Governance, Retrieval Quality, and RCA Quality gates;
it does not collapse them into a misleading overall `PASS`. Without an
explicitly paid real-Qwen run, the RCA gate is `BLOCKED`, never replaced by a
Fake-LLM result.

To run the seven Test-partition scenarios with real Qwen, enter the key without
putting it in shell history and keep the per-scenario call cap:

```powershell
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    & .\.venv\Scripts\python.exe scripts\run_evaluation_v2_rca.py `
        --run-real-qwen `
        --confirm-paid-qwen `
        --max-qwen-calls-per-scenario 16
    & .\.venv\Scripts\python.exe scripts\run_evaluation_v2_release.py
} finally {
    Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    Remove-Variable qwenSecret -ErrorAction SilentlyContinue
}
```

See [docs/evaluation-v2-causal-scope-spec.md](docs/evaluation-v2-causal-scope-spec.md)
and [docs/retrieval-evaluation.md](docs/retrieval-evaluation.md) for the measured
results, failed cases, and Synthetic-only claim boundary.

## RCA Reasoning Engine

New RCA jobs use the evidence-bounded `hypothesis_v1` engine. It supplies the
official conclusion, ranked hypotheses, validation evidence, and report. The
historical snapshot DTOs remain readable, but the retired Legacy reasoning
engine is no longer configurable or executed.
