-- Batch 23.0: durable PostgreSQL queue contract for asynchronous RCA jobs.
-- Execution, leasing, retry, cancellation, and event streaming are activated
-- by later batches; this migration establishes their single source of truth.

ALTER TABLE rca_job_state
    DROP CONSTRAINT IF EXISTS rca_job_state_status_check;

UPDATE rca_job_state
SET status = 'queued',
    state = jsonb_set(state, '{job,status}', '"queued"'::jsonb, true)
WHERE status = 'pending';

ALTER TABLE rca_job_state
    ADD COLUMN request jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN request_hash text,
    ADD COLUMN idempotency_key text,
    ADD COLUMN runtime_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN priority integer NOT NULL DEFAULT 0,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
    ADD COLUMN next_attempt_at timestamptz,
    ADD COLUMN lease_owner text,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN heartbeat_at timestamptz,
    ADD COLUMN cancel_requested_at timestamptz,
    ADD COLUMN started_at timestamptz,
    ADD COLUMN completed_at timestamptz,
    ADD COLUMN error jsonb,
    ADD COLUMN checkpoint jsonb,
    ADD COLUMN version integer NOT NULL DEFAULT 1,
    ADD CONSTRAINT rca_job_state_status_check CHECK (
        status IN (
            'queued', 'running', 'retry_wait', 'cancel_requested',
            'completed', 'failed', 'cancelled', 'skipped'
        )
    ),
    ADD CONSTRAINT rca_job_state_request_hash_check CHECK (
        request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT rca_job_state_idempotency_key_check CHECK (
        idempotency_key IS NULL
        OR char_length(idempotency_key) BETWEEN 1 AND 200
    ),
    ADD CONSTRAINT rca_job_state_attempt_count_check CHECK (attempt_count >= 0),
    ADD CONSTRAINT rca_job_state_priority_check CHECK (priority >= 0),
    ADD CONSTRAINT rca_job_state_max_attempts_check CHECK (max_attempts >= 1),
    ADD CONSTRAINT rca_job_state_version_check CHECK (version >= 1);

UPDATE rca_job_state
SET request = jsonb_build_object(
        'investigation_mode',
        COALESCE(state #>> '{job,investigation_mode}', 'product_window'),
        'user_query',
        COALESCE(state #>> '{job,user_query}', ''),
        'lot_id',
        state #>> '{job,source_lot_id}'
    ),
    runtime_config = COALESCE(state -> 'execution_metadata', '{}'::jsonb);

CREATE UNIQUE INDEX idx_rca_job_state_idempotency_key
    ON rca_job_state(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_rca_job_state_queue_claim
    ON rca_job_state(status, priority DESC, next_attempt_at, created_at)
    WHERE status IN ('queued', 'retry_wait');

CREATE INDEX idx_rca_job_state_lease_expiry
    ON rca_job_state(lease_expires_at)
    WHERE status IN ('running', 'cancel_requested');

CREATE TABLE rca_job_attempt (
    attempt_id bigserial PRIMARY KEY,
    job_id text NOT NULL REFERENCES rca_job_state(job_id) ON DELETE CASCADE,
    attempt_number integer NOT NULL CHECK (attempt_number >= 1),
    worker_id text NOT NULL,
    status text NOT NULL CHECK (
        status IN ('running', 'completed', 'failed', 'cancelled', 'abandoned')
    ),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error jsonb,
    checkpoint jsonb,
    UNIQUE (job_id, attempt_number)
);

CREATE INDEX idx_rca_job_attempt_job_started
    ON rca_job_attempt(job_id, started_at DESC);

CREATE TABLE rca_job_event (
    event_id bigserial PRIMARY KEY,
    job_id text NOT NULL REFERENCES rca_job_state(job_id) ON DELETE CASCADE,
    sequence integer NOT NULL CHECK (sequence >= 1),
    event_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, sequence)
);

CREATE INDEX idx_rca_job_event_job_sequence
    ON rca_job_event(job_id, sequence);

CREATE TABLE rca_worker_heartbeat (
    worker_id text PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    active_lease_count integer NOT NULL DEFAULT 0 CHECK (active_lease_count >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO schema_migrations(version) VALUES ('011_async_job_queue');
