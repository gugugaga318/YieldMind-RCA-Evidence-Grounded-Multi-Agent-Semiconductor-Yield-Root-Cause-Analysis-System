-- Roll back the Batch 23.0 asynchronous RCA job queue contract.

DELETE FROM schema_migrations WHERE version = '011_async_job_queue';

DROP TABLE IF EXISTS rca_worker_heartbeat;
DROP TABLE IF EXISTS rca_job_event;
DROP TABLE IF EXISTS rca_job_attempt;

DROP INDEX IF EXISTS idx_rca_job_state_lease_expiry;
DROP INDEX IF EXISTS idx_rca_job_state_queue_claim;
DROP INDEX IF EXISTS idx_rca_job_state_idempotency_key;

ALTER TABLE rca_job_state
    DROP CONSTRAINT IF EXISTS rca_job_state_status_check;

UPDATE rca_job_state
SET status = CASE
        WHEN status IN ('queued', 'retry_wait') THEN 'pending'
        WHEN status = 'cancel_requested' THEN 'running'
        WHEN status = 'cancelled' THEN 'skipped'
        ELSE status
    END,
    state = jsonb_set(
        state,
        '{job,status}',
        to_jsonb(
            CASE
                WHEN status IN ('queued', 'retry_wait') THEN 'pending'
                WHEN status = 'cancel_requested' THEN 'running'
                WHEN status = 'cancelled' THEN 'skipped'
                ELSE status
            END
        ),
        true
    );

ALTER TABLE rca_job_state
    DROP CONSTRAINT IF EXISTS rca_job_state_request_hash_check,
    DROP CONSTRAINT IF EXISTS rca_job_state_idempotency_key_check,
    DROP CONSTRAINT IF EXISTS rca_job_state_attempt_count_check,
    DROP CONSTRAINT IF EXISTS rca_job_state_priority_check,
    DROP CONSTRAINT IF EXISTS rca_job_state_max_attempts_check,
    DROP CONSTRAINT IF EXISTS rca_job_state_version_check,
    DROP COLUMN IF EXISTS request,
    DROP COLUMN IF EXISTS request_hash,
    DROP COLUMN IF EXISTS idempotency_key,
    DROP COLUMN IF EXISTS runtime_config,
    DROP COLUMN IF EXISTS priority,
    DROP COLUMN IF EXISTS attempt_count,
    DROP COLUMN IF EXISTS max_attempts,
    DROP COLUMN IF EXISTS next_attempt_at,
    DROP COLUMN IF EXISTS lease_owner,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS heartbeat_at,
    DROP COLUMN IF EXISTS cancel_requested_at,
    DROP COLUMN IF EXISTS started_at,
    DROP COLUMN IF EXISTS completed_at,
    DROP COLUMN IF EXISTS error,
    DROP COLUMN IF EXISTS checkpoint,
    DROP COLUMN IF EXISTS version,
    ADD CONSTRAINT rca_job_state_status_check CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'skipped')
    );
