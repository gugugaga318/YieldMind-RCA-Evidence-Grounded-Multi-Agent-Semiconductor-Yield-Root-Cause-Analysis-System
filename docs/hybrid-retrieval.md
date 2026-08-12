# BM25, Vector, and RRF Hybrid Retrieval

## Long Task 3 Boundary

Long Task 3 adds and evaluates three new retrieval paths without changing the
online default Retriever:

```text
Approved Active Index
  ├─ BM25 candidates
  └─ exact Vector candidates
          ↓
        RRF fusion
          ↓
  logical Case / SOP / Engineering Note results
```

The existing RCA `KeywordRetriever` remains the Case-only compatibility
baseline. The current online `DocumentChunkKeywordRetriever` is evaluated as a
second, fair baseline because it already supports RCA Cases, SOPs, Engineering
Notes, and metadata scope. It remains the online default until Long Task 4
evaluates pgvector, an optional Cross-Encoder reranker, score calibration, and
the feature-flagged online cutover.

Python remains authoritative for:

- `CONFIRMED` Active-Index visibility;
- Question-to-document-type compatibility;
- module, equipment, operation, defect, and tag scope;
- Chunk-to-logical-asset aggregation;
- RRF calculation and evaluation qrels.

An LLM cannot publish knowledge, change scope, or override relevance judgments.

## Implementations

### Lexical retrieval

`PythonBM25CandidateSource` implements exact Okapi BM25 for the small local
corpus. It tokenizes English terms plus Chinese unigrams and bigrams, then ranks
only approved in-scope Chunks.

`PostgresBM25CandidateSource` is the production candidate implementation. Core
PostgreSQL does not provide native Okapi BM25, so it is accurately labeled
BM25-style: migration `008_hybrid_retrieval` creates a generated `tsvector`, a
GIN index, and candidate ranking uses `ts_rank_cd`.

### Vector retrieval

`SentenceTransformerEmbeddingBackend` defaults to `BAAI/bge-m3` and loads the
model lazily. The evaluation CLI pins the Hugging Face model revision so a
future update to the same model name cannot silently change measured scores.
`device=auto` chooses CUDA when PyTorch reports a usable GPU and falls back to
CPU otherwise. `ExactVectorCandidateSource` computes cosine similarity over all
in-scope Chunks. Exact search is appropriate for the current 60-asset corpus;
pgvector storage is Long Task 4.

`DeterministicHashEmbeddingBackend` exists only for fast, offline CI. It proves
the retrieval and evaluation contracts without downloading a model, but its
numbers are not semantic-model results and must not be quoted as such.

The ablation runner pre-encodes all Ground-Truth queries in batches and shares
that cache between Vector-only and Hybrid-RRF. This changes evaluation runtime,
not ranking semantics.

### Fusion and score provenance

`HybridDocumentChunkRetriever` runs lexical and Vector candidate generation
independently and combines ranks with Reciprocal Rank Fusion:

```text
RRF(document) = Σ weight / (rrf_k + rank)
```

Every Knowledge Hit keeps separate `lexical`, `vector`, and `fusion` score
components plus the final retrieval strategy. These are ranking signals, not
RCA confidence and not root-cause evidence strength.

## Install the real Embedding runtime

The model runtime is optional so the normal backend image and unit test suite do
not acquire a multi-gigabyte ML dependency.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[retrieval]"
```

For NVIDIA GPU execution, install the PyTorch build matching the local CUDA
driver first, then install the retrieval extra. Confirm the actual device in the
generated report instead of assuming CUDA was selected.

## Run the ablation

Fast deterministic architecture check:

```powershell
.\.venv\Scripts\python.exe scripts\run_hybrid_retrieval_evaluation.py `
  --embedding-backend deterministic-hash `
  --device cpu
```

Real multilingual Embedding evaluation:

```powershell
.\.venv\Scripts\python.exe scripts\run_hybrid_retrieval_evaluation.py `
  --embedding-backend sentence-transformers `
  --embedding-model BAAI/bge-m3 `
  --embedding-revision 5617a9f61b028005a4858fdac845db406aefb181 `
  --device auto `
  --batch-size 32
```

Both commands use the same
`data/evaluation/retrieval_ground_truth.json`. Outputs are written to:

```text
outputs/hybrid_retrieval_evaluation/results.json
outputs/hybrid_retrieval_evaluation/report.md
```

The report compares Legacy Case Keyword, current Chunk Keyword, BM25-only,
Vector-only, and Hybrid-RRF on Recall@5, Candidate Recall@20, MRR@10, nDCG@10,
cross-language Recall@5, Hard-Negative accuracy, No-Answer accuracy, and
unapproved knowledge leakage. Keeping both Keyword baselines prevents document
coverage improvements from being misreported as ranking-algorithm gains.
Quality values are measured results rather than prewritten targets.

## Measured Synthetic V1 Result

The pinned `BAAI/bge-m3` revision
`5617a9f61b028005a4858fdac845db406aefb181` was evaluated on CUDA over all 114
queries:

| Retriever | Recall@5 | MRR@10 | nDCG@10 | Cross-language Recall@5 | No-answer accuracy |
|---|---:|---:|---:|---:|---:|
| Legacy Case Keyword | 41.15% | 0.4018 | 0.3775 | 10.42% | 0.00% |
| Current Chunk Keyword | 96.88% | 0.9948 | 0.9153 | 95.83% | 100.00% |
| BM25-only | 97.40% | 1.0000 | 0.9206 | 96.88% | 100.00% |
| Vector-only | 97.92% | 1.0000 | 0.9217 | 97.92% | 100.00% |
| Hybrid-RRF | 97.92% | 1.0000 | 0.9219 | 97.92% | 100.00% |

Against the fair current-online Chunk Keyword baseline, Hybrid improves
Recall@5 by 1.04 percentage points, cross-language Recall@5 by 2.09 points, and
nDCG@10 by 0.0066. Hard-negative accuracy and No-Answer accuracy remain 100%,
and every Retriever has zero unapproved hits.

The much larger Legacy-to-Hybrid gap is not an algorithm-only gain: the legacy
Retriever is Case-only and cannot return independent SOP or Engineering Note
assets. These are Synthetic benchmark results with strict metadata scope, not a
claim about production-fab accuracy. A reranker should remain feature-flagged
unless Long Task 4 measures an additional gain.
