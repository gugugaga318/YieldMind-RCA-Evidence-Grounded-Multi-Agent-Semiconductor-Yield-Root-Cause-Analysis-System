# Hybrid Retrieval Upgrade Plan

## Agreed Target

The knowledge path will evolve from the current Case-only keyword lookup to a
governed two-stage retrieval pipeline:

```text
approved Knowledge Asset
  -> document-type-aware chunking and metadata
  -> BM25 candidates + Vector candidates
  -> RRF fusion
  -> optional Cross-Encoder reranking
  -> calibrated logical-asset results
  -> Knowledge Agent interpretation
```

Python remains authoritative for approval visibility, metadata scope, qrels,
Evidence relevance, and release gates. An LLM may generate or explain text, but
it cannot publish unapproved knowledge or override relevance rules.

## Long Tasks

### 1. Retrieval Evaluation and Synthetic Knowledge Corpus

- Independent retrieval ground truth and graded qrels.
- 36 RCA Cases, 12 SOPs, 12 Engineering Notes, and 114 queries.
- Canonical Facts with separate document/query generation payloads.
- Optional approval-gated paid Qwen enrichment.
- KeywordRetriever baseline and deterministic JSON/Markdown report.
- No online Retriever cutover.

### 2. Knowledge Asset and Chunk Contracts, Ingestion, and Lookup

- Introduce `KnowledgeChunk` and document-to-case aggregation contracts.
- Support text PDF, Markdown, and TXT; scanned PDF/OCR remains out of V1.
- Structure-aware chunking for RCA, SOP, and Engineering Note documents.
- Metadata extraction and filters for module, equipment, operation, and defect.
- Add independent `knowledge_lookup` Intent and the question kinds
  `historical_match`, `procedure_guidance`, and `engineering_note_lookup`.
- User-ingested documents may be parsed/chunked/embedded before approval but
  cannot enter the Active Index until two different engineers approve them.

### 3. BM25, Vector, RRF, and Ablation Evaluation

- PostgreSQL full-text BM25-style candidate retrieval.
- Multilingual Embedding backend with `device=auto` (CUDA first, CPU fallback).
- Exact vector search while the corpus is small.
- Reciprocal Rank Fusion with separate lexical, vector, and fusion scores.
- Keyword, BM25-only, Vector-only, and Hybrid ablation reports using the same
  ground truth.

### 4. pgvector, Reranker, Agent Cutover, and Final Evaluation

- pgvector storage and migrations without premature IVFFlat tuning.
- Feature-flagged multilingual Cross-Encoder reranker.
- Separate lexical, vector, fusion, reranker, calibrated relevance, and source
  confidence fields.
- Knowledge Agent consumes typed logical-asset results.
- Final retrieval and end-to-end RCA evaluation, including Historical Overreach
  Rate and unapproved knowledge leakage.

## Release Boundaries

- `data/evaluation/scenarios.json` remains the end-to-end RCA test set.
- `data/evaluation/retrieval_ground_truth.json` remains the Retriever test set.
- Unapproved knowledge leakage must always be zero.
- Synthetic Seed assets are explicitly labeled and do not pretend to have human
  approvals.
- User-ingested documents continue to require two different engineers.
- Reranker stays behind a Feature Flag and is enabled only if evaluation shows a
  real gain.
- Resume numbers must use measured results rather than prewritten improvements.
