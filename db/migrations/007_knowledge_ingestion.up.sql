-- Governed Knowledge Asset ingestion, chunk staging, and fail-closed Active Index.

CREATE TABLE knowledge_ingestion_candidate (
    candidate_id text PRIMARY KEY,
    filename text NOT NULL,
    source_format text NOT NULL CHECK (source_format IN ('markdown', 'text', 'pdf')),
    document_type text NOT NULL CHECK (
        document_type IN ('RCA_CASE', 'SOP', 'ENGINEERING_NOTE')
    ),
    case_id text REFERENCES rca_case(case_id) ON DELETE RESTRICT,
    title text NOT NULL,
    parsed_content text NOT NULL,
    content_sha256 text NOT NULL CHECK (length(content_sha256) = 64),
    module text NOT NULL,
    equipment_type text NOT NULL DEFAULT '',
    operation text NOT NULL DEFAULT '',
    defect_type text NOT NULL DEFAULT '',
    tags text[] NOT NULL DEFAULT ARRAY[]::text[],
    status text NOT NULL CHECK (
        status IN ('pending_approval', 'published', 'rejected')
    ),
    publication_policy text NOT NULL DEFAULT 'DUAL_ENGINEER_APPROVAL',
    published_document_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK (document_type <> 'RCA_CASE' OR case_id IS NOT NULL),
    CHECK (status <> 'published' OR published_document_id IS NOT NULL),
    CHECK (status <> 'published' OR published_at IS NOT NULL)
);

CREATE TABLE knowledge_ingestion_chunk (
    chunk_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES knowledge_ingestion_candidate(candidate_id)
        ON DELETE CASCADE,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    section_type text NOT NULL,
    heading text NOT NULL DEFAULT '',
    content text NOT NULL,
    token_count integer NOT NULL CHECK (token_count > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_status text NOT NULL CHECK (validation_status = 'STAGED'),
    embedding_status text NOT NULL DEFAULT 'not_requested' CHECK (
        embedding_status IN ('not_requested', 'pending', 'completed', 'failed')
    ),
    UNIQUE (candidate_id, chunk_index)
);

CREATE TABLE knowledge_ingestion_approval (
    approval_id text PRIMARY KEY,
    candidate_id text NOT NULL REFERENCES knowledge_ingestion_candidate(candidate_id)
        ON DELETE CASCADE,
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

ALTER TABLE knowledge_document
    ADD COLUMN module text NOT NULL DEFAULT '',
    ADD COLUMN equipment_type text NOT NULL DEFAULT '',
    ADD COLUMN operation text NOT NULL DEFAULT '',
    ADD COLUMN defect_type text NOT NULL DEFAULT '',
    ADD COLUMN source_format text NOT NULL DEFAULT 'synthetic' CHECK (
        source_format IN ('markdown', 'text', 'pdf', 'synthetic')
    ),
    ADD COLUMN content_sha256 text NOT NULL DEFAULT '',
    ADD COLUMN publication_policy text NOT NULL DEFAULT 'LEGACY_CONFIRMED',
    ADD COLUMN source_ingestion_candidate_id text UNIQUE
        REFERENCES knowledge_ingestion_candidate(candidate_id) ON DELETE SET NULL;

CREATE TABLE knowledge_chunk (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES knowledge_document(document_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    section_type text NOT NULL,
    heading text NOT NULL DEFAULT '',
    content text NOT NULL,
    token_count integer NOT NULL CHECK (token_count > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_status text NOT NULL CHECK (validation_status = 'CONFIRMED'),
    embedding_status text NOT NULL DEFAULT 'not_requested' CHECK (
        embedding_status IN ('not_requested', 'pending', 'completed', 'failed')
    ),
    UNIQUE (document_id, chunk_index)
);

CREATE VIEW active_knowledge_chunk AS
SELECT kc.*
FROM knowledge_chunk kc
JOIN knowledge_document kd ON kd.document_id = kc.document_id
WHERE kc.validation_status = 'CONFIRMED'
  AND kd.validation_status = 'CONFIRMED';

CREATE UNIQUE INDEX idx_knowledge_pending_content_sha256
    ON knowledge_ingestion_candidate(content_sha256)
    WHERE status = 'pending_approval';
CREATE UNIQUE INDEX idx_knowledge_active_content_sha256
    ON knowledge_document(content_sha256)
    WHERE validation_status = 'CONFIRMED' AND content_sha256 <> '';
CREATE INDEX idx_knowledge_ingestion_status_created
    ON knowledge_ingestion_candidate(status, created_at);
CREATE INDEX idx_knowledge_ingestion_approval_candidate_time
    ON knowledge_ingestion_approval(candidate_id, decided_at);
CREATE INDEX idx_knowledge_chunk_document
    ON knowledge_chunk(document_id, chunk_index);
CREATE INDEX idx_knowledge_document_filters
    ON knowledge_document(document_type, module, equipment_type, operation, defect_type);

INSERT INTO schema_migrations(version) VALUES ('007_knowledge_ingestion');
