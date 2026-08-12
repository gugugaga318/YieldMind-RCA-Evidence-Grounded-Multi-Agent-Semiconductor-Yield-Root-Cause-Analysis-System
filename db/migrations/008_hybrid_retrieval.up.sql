-- PostgreSQL full-text candidate index for Long Task 3 hybrid retrieval.
-- Core PostgreSQL provides ts_rank_cd rather than native Okapi BM25, so this
-- migration is explicitly a BM25-style lexical candidate layer.

ALTER TABLE knowledge_chunk
    ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'simple',
            COALESCE(heading, '') || ' ' || COALESCE(content, '')
        )
    ) STORED;

CREATE INDEX idx_knowledge_chunk_search_vector
    ON knowledge_chunk USING gin(search_vector);

INSERT INTO schema_migrations(version) VALUES ('008_hybrid_retrieval');
