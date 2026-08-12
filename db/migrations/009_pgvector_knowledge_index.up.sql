-- pgvector persistence for the approval-gated Knowledge Active Index.
-- The corpus is intentionally small, so Long Task 4 uses exact distance scans
-- and does not create IVFFlat or HNSW indexes prematurely.

CREATE EXTENSION IF NOT EXISTS vector;

DROP VIEW active_knowledge_chunk;

ALTER TABLE knowledge_chunk
    ADD COLUMN embedding vector(1024),
    ADD COLUMN embedding_model text,
    ADD COLUMN embedding_revision text,
    ADD COLUMN embedding_input_sha256 text CHECK (
        embedding_input_sha256 IS NULL OR length(embedding_input_sha256) = 64
    ),
    ADD COLUMN embedded_at timestamptz,
    ADD CONSTRAINT knowledge_chunk_embedding_metadata_consistent CHECK (
        (embedding IS NULL AND embedding_model IS NULL
            AND embedding_revision IS NULL AND embedding_input_sha256 IS NULL
            AND embedded_at IS NULL)
        OR
        (embedding IS NOT NULL AND embedding_model IS NOT NULL
            AND embedding_revision IS NOT NULL AND embedding_input_sha256 IS NOT NULL
            AND embedded_at IS NOT NULL AND embedding_status = 'completed')
    );

CREATE VIEW active_knowledge_chunk AS
SELECT kc.*
FROM knowledge_chunk kc
JOIN knowledge_document kd ON kd.document_id = kc.document_id
WHERE kc.validation_status = 'CONFIRMED'
  AND kd.validation_status = 'CONFIRMED';

CREATE INDEX idx_knowledge_chunk_embedding_model
    ON knowledge_chunk(embedding_model, embedding_revision)
    WHERE embedding IS NOT NULL AND validation_status = 'CONFIRMED';

INSERT INTO schema_migrations(version) VALUES ('009_pgvector_knowledge_index');
