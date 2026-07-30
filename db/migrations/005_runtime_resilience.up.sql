-- Durable RCA job state for restart-safe and multi-worker API operation.

CREATE TABLE rca_job_state (
    job_id text PRIMARY KEY,
    status text NOT NULL CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'skipped')
    ),
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_rca_job_state_status_updated
    ON rca_job_state(status, updated_at DESC);

INSERT INTO schema_migrations(version) VALUES ('005_runtime_resilience');
