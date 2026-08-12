# Step 16 Observability and Audit

## Runtime Modes

Configure one mode in `.env`:

```text
YIELD_RCA_AGENT_MODE=deterministic | fake | llm
YIELD_RCA_LLM_MODEL=qwen-plus
```

Use `fake` first to verify the complete Planner, Specialist, and RCA LLM path.
For real calls, set `YIELD_RCA_AGENT_MODE=llm` and provide
`DASHSCOPE_API_KEY`. The service fails at configuration time when `llm` mode is
selected without a key; it does not silently fall back.

The deterministic Hypothesis Engine supplies every new RCA conclusion. Historical
snapshots remain readable, but Legacy reasoning is no longer executed or
configurable.

## Endpoints

```text
GET /health   process liveness
GET /ready    configured Agent mode and model
GET /metrics  Prometheus text exposition
```

Metrics cover RCA job counts and duration, Tool calls and duration, LLM calls,
tokens and duration, LLM errors, and inconclusive outcomes. `job_id` and
`lot_id` are intentionally excluded from metric labels to avoid high
cardinality.

## Audit Events

Step 16 adds PostgreSQL tables `audit_event` and `llm_usage_event`. The API
records:

```text
RCA_JOB_CREATED
RCA_JOB_COMPLETED
RCA_JOB_FAILED
RCA_REPORT_VIEWED
MEMORY_CANDIDATE_CREATED
MEMORY_APPROVAL_RECORDED
MEMORY_CANDIDATE_PUBLISHED
MEMORY_CANDIDATE_REJECTED
```

CSV/test mode uses an in-memory sink. PostgreSQL runtime uses the database sink.
Audit is best-effort: a telemetry outage is logged but never changes evidence,
confidence, root cause, or report content.

## Logging Safety

JSON logs carry bounded correlation fields such as `correlation_id`, `job_id`,
`agent`, `tool_request_id`, `lot_id`, `duration_ms`, and `outcome`. Logs must not
contain API keys, passwords, full database URLs, full raw prompts, or complete
serialized `RCAState` objects.

## Local Validation

```powershell
$env:YIELD_RCA_AGENT_MODE="fake"
docker compose --profile tools run --rm --build seed
docker compose up --build -d backend worker frontend
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:8000/metrics
```

The seed command is required after pulling Step 16 because it applies migration
`002_observability_audit` and `003_memory_approval`. It imports existing offline data and does not run a
Synthetic Fab generator.
