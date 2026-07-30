-- Roll back Step 19 controlled memory publication.

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
        DELETE FROM schema_migrations WHERE version = '003_memory_approval';
    END IF;
END
$$;

DROP INDEX IF EXISTS idx_rca_case_validation_status;
DROP INDEX IF EXISTS idx_memory_approval_candidate_time;
DROP INDEX IF EXISTS idx_memory_candidate_status_created;

DO $$
BEGIN
    IF to_regclass('public.audit_event') IS NOT NULL THEN
        -- These actions belong to the Step 19 feature being rolled back. They
        -- must be removed before restoring the narrower Step 16 constraint.
        DELETE FROM audit_event
        WHERE action IN (
            'MEMORY_CANDIDATE_CREATED',
            'MEMORY_APPROVAL_RECORDED',
            'MEMORY_CANDIDATE_PUBLISHED',
            'MEMORY_CANDIDATE_REJECTED'
        );
        ALTER TABLE audit_event DROP CONSTRAINT IF EXISTS audit_event_action_check;
        ALTER TABLE audit_event ADD CONSTRAINT audit_event_action_check CHECK (action IN (
            'RCA_JOB_CREATED',
            'RCA_JOB_COMPLETED',
            'RCA_JOB_FAILED',
            'RCA_REPORT_VIEWED'
        ));
    END IF;
END
$$;

ALTER TABLE IF EXISTS knowledge_document DROP COLUMN IF EXISTS validation_status;
ALTER TABLE IF EXISTS rca_case
    DROP COLUMN IF EXISTS approved_at,
    DROP COLUMN IF EXISTS approval_count,
    DROP COLUMN IF EXISTS source_candidate_id,
    DROP COLUMN IF EXISTS validation_status;

DROP TABLE IF EXISTS memory_approval;
DROP TABLE IF EXISTS memory_candidate;
