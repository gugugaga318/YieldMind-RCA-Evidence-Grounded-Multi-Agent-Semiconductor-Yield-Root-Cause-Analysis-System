-- Step 19 controlled memory candidate, dual approval, and publication.

CREATE TABLE memory_candidate (
    candidate_id text PRIMARY KEY,
    job_id text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('pending_approval', 'published', 'rejected')),
    scope_level text NOT NULL CHECK (scope_level IN ('event', 'fab')),
    source_lot_id text,
    product_id text,
    title text NOT NULL,
    incident_summary text NOT NULL,
    engineering_summary text NOT NULL,
    root_cause text NOT NULL,
    confidence numeric NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    recommendations jsonb NOT NULL,
    evidence_ids text[] NOT NULL,
    requires_process_engineer_approval boolean NOT NULL DEFAULT false,
    published_case_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK (status <> 'published' OR published_case_id IS NOT NULL),
    CHECK (status <> 'published' OR published_at IS NOT NULL)
);

CREATE TABLE memory_approval (
    approval_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES memory_candidate(candidate_id) ON DELETE CASCADE,
    engineer_id text NOT NULL,
    engineer_role text NOT NULL CHECK (engineer_role IN (
        'yield_engineer',
        'process_engineer',
        'equipment_engineer',
        'quality_engineer'
    )),
    decision text NOT NULL CHECK (decision IN ('approve', 'reject')),
    comment text NOT NULL DEFAULT '',
    decided_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, engineer_id)
);

ALTER TABLE rca_case
    ADD COLUMN validation_status text NOT NULL DEFAULT 'CONFIRMED'
        CHECK (validation_status IN ('CONFIRMED')),
    ADD COLUMN source_candidate_id text UNIQUE
        REFERENCES memory_candidate(candidate_id) ON DELETE SET NULL,
    ADD COLUMN approval_count integer NOT NULL DEFAULT 0 CHECK (approval_count >= 0),
    ADD COLUMN approved_at timestamptz;

ALTER TABLE knowledge_document
    ADD COLUMN validation_status text NOT NULL DEFAULT 'CONFIRMED'
        CHECK (validation_status IN ('CONFIRMED'));

ALTER TABLE audit_event DROP CONSTRAINT audit_event_action_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_action_check CHECK (action IN (
    'RCA_JOB_CREATED',
    'RCA_JOB_COMPLETED',
    'RCA_JOB_FAILED',
    'RCA_REPORT_VIEWED',
    'MEMORY_CANDIDATE_CREATED',
    'MEMORY_APPROVAL_RECORDED',
    'MEMORY_CANDIDATE_PUBLISHED',
    'MEMORY_CANDIDATE_REJECTED'
));

CREATE INDEX idx_memory_candidate_status_created
    ON memory_candidate(status, created_at);
CREATE INDEX idx_memory_approval_candidate_time
    ON memory_approval(candidate_id, decided_at);
CREATE INDEX idx_rca_case_validation_status
    ON rca_case(validation_status, approved_at);

INSERT INTO schema_migrations(version) VALUES ('003_memory_approval');
