-- Step 16 observability and append-only audit foundation.

CREATE TABLE audit_event (
    event_id text PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL CHECK (action IN (
        'RCA_JOB_CREATED',
        'RCA_JOB_COMPLETED',
        'RCA_JOB_FAILED',
        'RCA_REPORT_VIEWED'
    )),
    job_id text NOT NULL,
    correlation_id text NOT NULL,
    actor text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('success', 'failed')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE llm_usage_event (
    call_id text PRIMARY KEY,
    job_id text NOT NULL,
    correlation_id text NOT NULL,
    agent text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    prompt_version text NOT NULL,
    prompt_tokens integer NOT NULL CHECK (prompt_tokens >= 0),
    completion_tokens integer NOT NULL CHECK (completion_tokens >= 0),
    total_tokens integer NOT NULL CHECK (total_tokens >= 0),
    cached_tokens integer NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0),
    reasoning_tokens integer NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
    latency_ms double precision NOT NULL CHECK (latency_ms >= 0),
    status text NOT NULL CHECK (status IN ('success', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_event_job_time ON audit_event(job_id, occurred_at);
CREATE INDEX idx_audit_event_correlation ON audit_event(correlation_id);
CREATE INDEX idx_llm_usage_event_job ON llm_usage_event(job_id, created_at);

INSERT INTO schema_migrations(version) VALUES ('002_observability_audit');
