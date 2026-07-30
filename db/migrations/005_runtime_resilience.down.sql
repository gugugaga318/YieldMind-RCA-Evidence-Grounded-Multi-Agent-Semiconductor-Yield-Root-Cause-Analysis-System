-- Roll back durable RCA job state.

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
        DELETE FROM schema_migrations WHERE version = '005_runtime_resilience';
    END IF;
END
$$;

DROP INDEX IF EXISTS idx_rca_job_state_status_updated;
DROP TABLE IF EXISTS rca_job_state;
