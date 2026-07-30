DROP TABLE IF EXISTS knowledge_index_update;

ALTER TABLE memory_candidate
    DROP COLUMN IF EXISTS index_error,
    DROP COLUMN IF EXISTS index_attempts,
    DROP COLUMN IF EXISTS index_status,
    DROP COLUMN IF EXISTS reasoning_engine,
    DROP COLUMN IF EXISTS knowledge_provenance,
    DROP COLUMN IF EXISTS evidence_snapshot;

DELETE FROM schema_migrations WHERE version = '006_memory_snapshot_index_update';
