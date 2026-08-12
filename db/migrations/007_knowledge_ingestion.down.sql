DELETE FROM schema_migrations WHERE version = '007_knowledge_ingestion';

DROP VIEW IF EXISTS active_knowledge_chunk;
DROP INDEX IF EXISTS idx_knowledge_document_filters;
DROP INDEX IF EXISTS idx_knowledge_chunk_document;
DROP INDEX IF EXISTS idx_knowledge_ingestion_approval_candidate_time;
DROP INDEX IF EXISTS idx_knowledge_ingestion_status_created;
DROP INDEX IF EXISTS idx_knowledge_active_content_sha256;
DROP INDEX IF EXISTS idx_knowledge_pending_content_sha256;
DROP TABLE IF EXISTS knowledge_chunk;

ALTER TABLE knowledge_document
    DROP COLUMN IF EXISTS source_ingestion_candidate_id,
    DROP COLUMN IF EXISTS publication_policy,
    DROP COLUMN IF EXISTS content_sha256,
    DROP COLUMN IF EXISTS source_format,
    DROP COLUMN IF EXISTS defect_type,
    DROP COLUMN IF EXISTS operation,
    DROP COLUMN IF EXISTS equipment_type,
    DROP COLUMN IF EXISTS module;

DROP TABLE IF EXISTS knowledge_ingestion_approval;
DROP TABLE IF EXISTS knowledge_ingestion_chunk;
DROP TABLE IF EXISTS knowledge_ingestion_candidate;
