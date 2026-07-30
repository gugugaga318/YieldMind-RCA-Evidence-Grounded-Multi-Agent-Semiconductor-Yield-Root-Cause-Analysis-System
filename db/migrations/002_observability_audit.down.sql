-- Roll back Step 16 observability and audit storage.

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
        DELETE FROM schema_migrations WHERE version = '002_observability_audit';
    END IF;
END
$$;
DROP INDEX IF EXISTS idx_llm_usage_event_job;
DROP INDEX IF EXISTS idx_audit_event_correlation;
DROP INDEX IF EXISTS idx_audit_event_job_time;
DROP TABLE IF EXISTS llm_usage_event;
DROP TABLE IF EXISTS audit_event;
