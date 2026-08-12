# Batch 23.0-23.1 Async Job Contract and Leased PostgreSQL Worker

## Scope

Batch 23.0 separates HTTP acceptance from RCA execution. It implements the
durable Job contract and PostgreSQL queue, but deliberately does not implement
the Worker, retry scheduler, cancellation endpoint, SSE transport, or frontend
asynchronous UX.

The default path is:

```text
POST /rca/jobs
  -> normalize the business request
  -> calculate a stable SHA-256 request hash
  -> persist one queued RCAState and runtime configuration snapshot
  -> return 202 Accepted
```

`workflow.run()` is not called by this HTTP path. The old synchronous behavior
is available only through the explicit `create_app(execute_jobs_inline=True)`
test adapter so the fixed Workflow remains a regression baseline.

## Job lifecycle

New Jobs use these states:

```text
queued -> running -> completed
                  -> failed
                  -> retry_wait -> running
                  -> cancel_requested -> cancelled
queued -> cancelled
retry_wait -> cancelled
```

`completed`, `failed`, and `cancelled` are immutable terminal states. Python
validates every persisted transition. Legacy `pending` and `skipped` values
remain readable but are not emitted for new queued Jobs.

## Idempotency

`Idempotency-Key` is optional and limited to 200 non-blank characters.

- Same key and same normalized request: return the original Job.
- Same key and different normalized request: return
  `409 idempotency_conflict`.
- No key: create a new Job on every submission.

The hash covers `investigation_mode`, resolved `user_query`, and normalized
`lot_id`, serialized as stable sorted JSON. It does not include headers,
credentials, or process-local configuration.

## PostgreSQL ownership

Migration `011_async_job_queue` makes PostgreSQL the durable state source. It
extends `rca_job_state` with the request envelope, idempotency identity, runtime
configuration, retry counters, lease fields, timestamps, structured error and
checkpoint fields, and a version. It also creates:

- `rca_job_attempt` for one record per execution attempt;
- `rca_job_event` for ordered public execution events;
- `rca_worker_heartbeat` for later Worker liveness and recovery.

The schema intentionally stores no DashScope API key, Authorization header, or
hidden Chain-of-Thought. Later public events may contain bounded decisions,
action reasons, evidence summaries, and stop reasons only.

## HTTP contract

`POST /rca/jobs` returns `202` and:

```json
{
  "job_id": "RCA_...",
  "status": "queued",
  "state_url": "/rca/jobs/RCA_...",
  "events_url": "/rca/jobs/RCA_.../events",
  "report_url": "/rca/jobs/RCA_.../report",
  "cancel_url": "/rca/jobs/RCA_.../cancel",
  "created_at": "..."
}
```

`GET /rca/jobs/{job_id}` supports partial queued state and returns non-secret
queue metadata. `GET /rca/jobs/{job_id}/report` returns structured
`409 job_not_completed` until a report exists. The `cancel_url` is active in
Batch 23.1. `events_url` reserves the stable URL that Batch 23.2 activates as
SSE.

## Batch 23.1 execution

Batch 23.1 adds a separate Worker process. Each loop:

1. records the Worker heartbeat;
2. recovers a bounded set of expired leases;
3. claims one eligible Job with `FOR UPDATE SKIP LOCKED`;
4. changes it to `running`, increments `attempt_count`, and creates an Attempt;
5. renews the lease from a heartbeat thread while the bounded Workflow runs;
6. commits the complete `RCAState` only if the same Worker still owns a valid
   lease;
7. appends a structured public Job Event and terminal/retry Attempt result.

Multiple Worker processes can run against the same database. Row locking and
lease ownership prevent concurrent claims and prevent a late Worker from
overwriting a recovered Job.

Retry is deliberately narrow:

- provider timeout/transport failure, HTTP 429, and provider HTTP 5xx can retry;
- authentication, authorization, billing, structured-output validation, known
  scope/data errors, and other Workflow errors fail without retry;
- retry uses bounded exponential backoff and stops at the persisted
  `max_attempts` value, currently three.

Cancellation is cooperative. A queued or waiting Job becomes `cancelled`
immediately. A running Job becomes `cancel_requested`; the active LLM/Tool call
is not forcibly killed, but its eventual Workflow result is discarded at the
commit boundary and the Job becomes `cancelled`. This protects Evidence and
Report integrity.

The Worker writes checkpoints only at safe attempt boundaries. It does not
resume inside a partially executed Agent chain; a retry re-runs the bounded
Workflow attempt from its immutable request and runtime configuration.
Before execution, the Worker verifies that Agent mode, provider, model,
orchestration mode, and dataset match that immutable snapshot. A mismatch is a
terminal configuration error and never runs the Workflow against the wrong
runtime.

## Operation

The normal Compose runtime starts API and Worker as separate processes:

```powershell
docker compose up --build -d backend worker frontend
docker compose logs -f backend worker
```

For a host-side diagnostic Worker, set the same database and runtime variables
used by the API, then run:

```powershell
$env:YIELD_RCA_DATABASE_URL = "postgresql://..."
& .\.venv\Scripts\python.exe scripts\run_rca_worker.py

# Claim at most one eligible Job, then exit.
& .\.venv\Scripts\python.exe scripts\run_rca_worker.py --once
```

Cancel with the stable URL returned by Job creation:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/rca/jobs/<job-id>/cancel"
```

An existing PostgreSQL volume must have migration `011_async_job_queue`
applied before the new backend or Worker will accept traffic.
The repository's documented seed command reapplies all migrations but resets
the demo schema, so back up any local data that must be preserved before using
that reset path.

## Remaining batch

- Batch 23.2: SSE Agent Trace plus frontend submission, reconnect, progress,
  cancellation, terminal result, and error UX.
