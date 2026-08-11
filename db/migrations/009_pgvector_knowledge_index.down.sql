DELETE FROM schema_migrations WHERE version = '009_pgvector_knowledge_index';

DROP VIEW IF EXISTS active_knowledge_chunk;
DROP INDEX IF EXISTS idx_knowledge_chunk_embedding_model;

ALTER TABLE IF EXISTS knowledge_chunk
    DROP CONSTRAINT IF EXISTS knowledge_chunk_embedding_metadata_consistent,
    DROP COLUMN IF EXISTS embedded_at,
    DROP COLUMN IF EXISTS embedding_input_sha256,
    DROP COLUMN IF EXISTS embedding_revision,
    DROP COLUMN IF EXISTS embedding_model,
    DROP COLUMN IF EXISTS embedding;

DO $migration$
BEGIN
    IF to_regclass('public.knowledge_chunk') IS NOT NULL
       AND to_regclass('public.knowledge_document') IS NOT NULL THEN
        EXECUTE 'CREATE VIEW active_knowledge_chunk AS
            SELECT kc.*
            FROM knowledge_chunk kc
            JOIN knowledge_document kd ON kd.document_id = kc.document_id
            WHERE kc.validation_status = ''CONFIRMED''
              AND kd.validation_status = ''CONFIRMED''';
    END IF;
END
$migration$;

-- The vector extension may be shared by other schemas and is intentionally
-- retained during downgrade.
