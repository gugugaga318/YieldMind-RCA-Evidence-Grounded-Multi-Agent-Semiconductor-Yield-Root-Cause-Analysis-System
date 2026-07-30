# Core Development Guide

## Scope

This repository has reached Step 19: controlled memory approval and publication.
The quality command structure covers the core and backend Python packages; the
frontend has its own TypeScript, unit-test, and build commands.

The current MVP still intentionally does not implement:

- Raw FDC sensor streams
- Vision Agent behavior
- real-time Fab integration
- a full SPC platform

## Python Runtime

Target runtime:

```text
Python >= 3.11
```

## Quality Commands

The Step 1 baseline commands use only the Python standard library so they can
run before optional development dependencies are installed.

Run from the repository root:

```powershell
python tools/quality.py lint
python tools/quality.py type-check
python tools/quality.py unit
python tools/quality.py contract
python tools/quality.py integration
python tools/quality.py performance
python tools/quality.py benchmark
python tools/quality.py test-all
```

If the system `python` command is not available in the Codex desktop
environment, use the bundled runtime reported by Codex. In this workspace it is:

```powershell
& "C:\Users\ybt\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" tools\quality.py test-all
```

The `pyproject.toml` also declares optional future development tools:

```text
ruff
mypy
pytest
```

Those tools can be introduced in later steps without changing the package
layout.

## Package Layout

```text
core/yield_rca_core/
backend/yield_rca_api/
tests/unit/
tests/contract/
tests/integration/
tests/performance/
tools/
```

## FastAPI Backend

The backend exposes the proven pure Python workflow and controlled memory APIs:

```text
POST /rca/jobs
GET  /rca/jobs/{job_id}
GET  /rca/jobs/{job_id}/report
GET  /rca/jobs/{job_id}/memory-candidate
GET  /memory/candidates/{candidate_id}
POST /memory/candidates/{candidate_id}/approvals
```

The backend reads existing seed files or an already seeded PostgreSQL database.
It never invokes the Synthetic Fab generator at startup or request time.

Start it from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn yield_rca_api.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

## React Dashboard

Step 12 adds a React, TypeScript, Vite, and ECharts dashboard under `frontend/`.
It provides:

- RCA job submission
- Agent Workflow Timeline
- backend-provided Yield Trend visualization
- Evidence Chain and referenced-record inspection
- Root Cause, confidence, and recommended actions
- rendered and downloadable Markdown report
- dual-engineer memory approval status and controls

The frontend is a presentation client. It calls the existing FastAPI endpoints
and does not implement SPC, evidence scoring, RCA reasoning, or Synthetic Fab
data generation.

Install dependencies and start the development server:

```powershell
cd frontend
pnpm install
pnpm dev
```

The dashboard is available at `http://127.0.0.1:5173` and proxies `/api` to the
FastAPI server at `http://127.0.0.1:8000`.

Run frontend checks and produce a production build:

```powershell
cd frontend
pnpm run check
pnpm run build
```

Preview the production output:

```powershell
cd frontend
pnpm preview
```

## Optional PostgreSQL Migration Test

Step 3 adds PostgreSQL DDL migrations. The repository includes an optional real
PostgreSQL integration test that is skipped unless both conditions are met:

```text
TEST_DATABASE_URL is set
psycopg is installed in the active Python environment
```

Example:

```powershell
$env:TEST_DATABASE_URL="postgresql://user:password@localhost:5432/yield_rca_test"
python tools/quality.py integration
```

The default `test-all` command still validates migration structure offline even
when a PostgreSQL server is not available.

## Golden Dataset Generation and Seed

Step 4 adds an offline golden Synthetic Fab dataset generator. It is not part of
FastAPI runtime.

Generate seed files:

```powershell
python scripts/generate_synthetic_fab_data.py
```

Generated files are written to:

```text
data/seeds/golden_case/
```

Seed PostgreSQL after setting `TEST_DATABASE_URL`:

```powershell
$env:TEST_DATABASE_URL="postgresql://yield_rca:yield_rca_password@localhost:5432/yield_rca_test"
python scripts/seed_database.py --reset-schema
```

The reset applies migrations `001` through `005`. It also deletes existing
RCA job states, memory candidates, and approvals. Use reset only for local/demo
data, not for a database whose RCA history or engineering approval history must
be retained.

If Docker Desktop is not running, real PostgreSQL seed tests are skipped by
default when `TEST_DATABASE_URL` is not set. Offline dataset contract tests still
run as part of `python tools/quality.py test-all`.
