DELETE FROM schema_migrations WHERE version = '008_hybrid_retrieval';

DROP INDEX IF EXISTS idx_knowledge_chunk_search_vector;

ALTER TABLE knowledge_chunk
    DROP COLUMN IF EXISTS search_vector;
