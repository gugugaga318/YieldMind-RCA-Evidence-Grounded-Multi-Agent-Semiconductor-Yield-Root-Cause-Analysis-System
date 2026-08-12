# Step 15 Docker Compose Deployment

## Scope

Step 15 provides a reproducible local deployment for PostgreSQL, FastAPI, and
the built React dashboard. Step 16 adds local observability and audit
foundations, but not production orchestration, automatic Synthetic Fab
generation, or authentication.

The service boundary is:

```text
Browser
  -> Nginx / React
  -> /api reverse proxy
  -> FastAPI
  -> PostgreSQL
```

Synthetic data remains an offline input:

```text
offline generator (host command, optional)
  -> data/seeds/<dataset>
  -> explicit Compose seed command
  -> PostgreSQL
```

Neither `docker compose up` nor FastAPI startup runs a generator, migration, or
seed or Embedding-index operation.

## Prerequisites

- Docker Engine with Docker Compose v2 or later
- Ports 8000 and 5173 available, or alternate ports configured in `.env`
- Existing seed files under `data/seeds/golden_case` or `data/seeds/multi_case`

## Configure

From the repository root:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace `POSTGRES_PASSWORD`. Use a URL-safe local password
because the same value is embedded in the PostgreSQL connection URL. Do not
commit `.env`.

Select `YIELD_RCA_AGENT_MODE=deterministic`, `fake`, or `llm`. Real Qwen mode
requires `DASHSCOPE_API_KEY`; this secret is passed at runtime and is not a
Docker build argument.

Select the orchestration path independently:

```text
YIELD_RCA_ORCHESTRATION_MODE=fixed
```

Supported values are `fixed`, `controlled_react`, and `llm_react`. The default
remains `fixed` for compatibility. Autonomous Batch 20.9 planning requires
`YIELD_RCA_ORCHESTRATION_MODE=llm_react` together with
`YIELD_RCA_AGENT_MODE=fake` for a no-cost validation or `llm` for real Qwen.
The Compose backend explicitly receives both variables.

Select the imported dataset with:

```text
YIELD_RCA_DATASET=multi_case
```

Supported bundled values are `golden_case`, `multi_case`, and `spc_case`.

## Initialize Data Explicitly

The `seed` service is behind the `tools` profile and is never part of normal
runtime startup. Run it explicitly before the first RCA query:

```powershell
docker compose up -d db
docker compose --profile tools run --rm --build seed
```

The seed command applies the current schema migration with `--reset-schema`
and imports the selected existing CSV files. Re-running it deletes and
recreates the application schema, so it is intended only for the local demo
database.

This reset also deletes Step 19 memory candidates and engineer approvals. A
deployed environment must apply forward migrations without using the demo seed
reset when approval history must be retained.

Migration `009_pgvector_knowledge_index` enables pgvector and adds a
`vector(1024)` field for the pinned `BAAI/bge-m3` model. The corpus is small,
so online search uses exact cosine distance and intentionally creates no
IVFFlat or HNSW index.

To enable Hybrid retrieval, update `.env` and run the explicit index tool:

```text
YIELD_RCA_BACKEND_TARGET=retrieval-runtime
YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE=hybrid
YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED=0
```

```powershell
docker compose --profile tools run --rm knowledge-index
docker compose up --build -d backend worker frontend
```

The indexer reads only `active_knowledge_chunk`, so staged or rejected uploads
cannot be embedded. It re-embeds a Chunk only when its text hash, model, or
model revision changes. FastAPI never runs this command during startup.

The optional Cross-Encoder needs the same `retrieval-runtime` image and:

```text
YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED=1
```

It remains disabled unless the pinned offline evaluation shows an nDCG gain
without Recall@5, hard-negative, no-answer, or approval-leakage regression.
Without a model-matched calibration artifact, `calibrated_relevance` is
deliberately `null`; the service does not label a sigmoid as calibration.

Evaluation V2 causal Scope is a separate, compatibility-safe switch:

```text
YIELD_RCA_CAUSAL_SCOPE_ENABLED=1
YIELD_RCA_CAUSAL_SCOPE_CANDIDATE_BUDGET=20
YIELD_RCA_CAUSAL_SCOPE_LANE_MINIMUM=1
```

Evaluation V2 promoted this switch, so `.env.example` and Compose now default it
to `1`. Set it explicitly to `0` to replay the legacy observed-Module hard-filter
baseline. When enabled, historical RCA retrieval treats the observed Module as a ranking hint
and creates bounded candidates from `same_step`, `upstream_route`,
`shared_resource`, and `global_semantic`. A user-selected explicit Module limit
and the existing approval, document-type, time, and permission boundaries remain
hard constraints. Missing route or resource context is reported as unavailable;
it is never inferred by the LLM.

The measured release combination is:

```text
YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE=keyword
YIELD_RCA_CAUSAL_SCOPE_ENABLED=1
YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED=0
```

This is intentionally not Hybrid-RRF: on the reviewed Synthetic V2 Test split,
Hybrid-RRF regressed hard-negative pairwise ranking versus Chunk Keyword. The
Reranker also remains disabled because no local-model evaluation established a
strict nDCG uplift without primary-metric regression.

Migration `005_runtime_resilience` originally added durable `rca_job_state`
storage. Migration `011_async_job_queue` extends it with the normalized request,
SHA-256 request identity, optional idempotency key, runtime configuration
snapshot, retry counters, lease timestamps, checkpoint/error fields, and
optimistic version. It also creates `rca_job_attempt`, `rca_job_event`, and
`rca_worker_heartbeat`. The backend readiness probe now requires migration 011
and all queue tables before accepting traffic.

Generating or regenerating datasets remains a separate host-side operation:

```powershell
.\.venv\Scripts\python.exe scripts\generate_synthetic_multi_case_data.py
```

For the advanced SPC evidence demo, run
`scripts\generate_synthetic_spc_data.py` and select `YIELD_RCA_DATASET=spc_case`.

Generation is never invoked by Compose, Nginx, FastAPI, or React.

## Start Runtime Services

Build and start the runtime after seeding:

```powershell
docker compose up --build -d backend worker frontend
docker compose ps
```

Open:

```text
Dashboard: http://127.0.0.1:5173
API docs:  http://127.0.0.1:8000/docs
Health:    http://127.0.0.1:8000/health
Metrics:   http://127.0.0.1:8000/metrics
```

The browser calls `/api`; Nginx removes that prefix and forwards the request to
the internal `backend:8000` service. React does not connect directly to
PostgreSQL or execute RCA logic.

`POST /api/rca/jobs` now returns `202 Accepted` after persisting a `queued` Job;
the HTTP request no longer waits for Qwen or the RCA Workflow. An optional
`Idempotency-Key` header prevents duplicate submissions. Reusing a key with the
same normalized request returns the original Job; changing the request returns
`409 idempotency_conflict`.

Batch 23.1 runs a separate `worker` service. It claims with
`FOR UPDATE SKIP LOCKED`, renews leases, retries only transient provider or
transport failures, recovers expired leases, and honors
`POST /api/rca/jobs/{job_id}/cancel`. The API response's `events_url` is still
reserved for Batch 23.2 SSE; the frontend polling/streaming UX is also deferred
to that Batch. No API key, Authorization header, or hidden model reasoning is
stored in the queue.

When upgrading an existing local PostgreSQL volume, apply migration
`011_async_job_queue` before starting the new backend and Worker. The backend
readiness check intentionally returns `503` while that migration or its Queue
tables are missing. The demo `seed` tool applies every migration with
`--reset-schema`; back up data that must be preserved before using that reset
path.

PostgreSQL is intentionally not published on a host port. Backend and seed
containers reach it through the private Compose network at `db:5432`, avoiding
conflicts with an existing PostgreSQL installation on the host.

## Operate and Stop

Inspect service output:

```powershell
docker compose logs -f backend worker frontend db
```

Stop services while preserving PostgreSQL data:

```powershell
docker compose down
```

Delete the local database volume only when a complete reset is intended:

```powershell
docker compose down --volumes
```

## Image Boundaries

`docker/backend.Dockerfile` has separate targets:

- `runtime` contains the installed Core and FastAPI packages only.
- `retrieval-runtime` adds sentence-transformers for explicitly enabled Hybrid
  retrieval and Cross-Encoder reranking.
- `seed` additionally contains the migration, seed importer, and existing seed
  files.
- `knowledge-index` is an explicit tool image that embeds only approved Active
  Index Chunks.

The runtime image does not contain the Synthetic Fab generator or seed script.
The frontend image is a static Nginx image produced by a separate Node build
stage.

## Current Limitations

- RCA Job requests, state, attempts, and events are durable in PostgreSQL.
- Memory candidates and approvals are durable only in PostgreSQL mode; CSV mode
  uses a process-local demonstration store.
- Engineer identity is request data until the Security and Permissions phase
  connects approval to authenticated identity and role mapping.
- Compose runs separate API and leased Worker processes. Horizontal Worker
  scale shares the same PostgreSQL queue and lock boundary.
- The health check verifies the HTTP process, not a full RCA transaction.
- Credentials use local `.env` configuration rather than a secret manager.
- Ports bind only to `127.0.0.1`; shared deployment requires an authenticated
  ingress and the later security step.
- The current observability layer is a local foundation. External metric/log
  backends, alert rules, backups, TLS, resource limits, and rolling upgrades
  remain production extensions.
