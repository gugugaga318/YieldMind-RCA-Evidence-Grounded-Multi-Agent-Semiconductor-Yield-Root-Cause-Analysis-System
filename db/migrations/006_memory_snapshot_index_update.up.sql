-- Batch 17: durable evidence snapshots and approval-gated Keyword index audit.

ALTER TABLE memory_candidate
    ADD COLUMN evidence_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN knowledge_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN reasoning_engine text NOT NULL DEFAULT 'legacy',
    ADD COLUMN index_status text NOT NULL DEFAULT 'not_requested'
        CHECK (index_status IN ('not_requested', 'pending', 'completed', 'failed')),
    ADD COLUMN index_attempts integer NOT NULL DEFAULT 0 CHECK (index_attempts >= 0),
    ADD COLUMN index_error text;

CREATE TABLE knowledge_index_update (
    update_id text PRIMARY KEY,
    candidate_id text NOT NULL UNIQUE REFERENCES memory_candidate(candidate_id) ON DELETE CASCADE,
    case_id text NOT NULL REFERENCES rca_case(case_id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('pending', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX idx_knowledge_index_update_status
    ON knowledge_index_update(status, created_at);

INSERT INTO schema_migrations(version) VALUES ('006_memory_snapshot_index_update');
