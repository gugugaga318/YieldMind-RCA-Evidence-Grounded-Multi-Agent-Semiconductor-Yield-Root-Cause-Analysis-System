# Batch 23.0 Async Job Contract and PostgreSQL Queue

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
`409 job_not_completed` until a report exists. The `events_url` and
`cancel_url` fields reserve the stable URLs; Batch 23.1 and 23.2 activate those
routes.

## Next batches

- Batch 23.1: leased Worker, heartbeat, retry/backoff, cancellation, and stale
  lease recovery using `FOR UPDATE SKIP LOCKED`.
- Batch 23.2: SSE Agent Trace plus frontend submission, reconnect, progress,
  cancellation, terminal result, and error UX.
