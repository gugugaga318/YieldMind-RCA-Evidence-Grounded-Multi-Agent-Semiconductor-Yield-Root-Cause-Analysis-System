# pgvector, Reranker, and Knowledge Agent Cutover

## Delivered architecture

Long Task 4 completes the governed retrieval path:

```text
CONFIRMED Active Index Chunk
  -> PostgreSQL FTS candidate branch
  -> pgvector exact-cosine candidate branch
  -> RRF logical-asset aggregation
  -> optional Cross-Encoder reranking
  -> optional model-matched Platt calibration
  -> typed Knowledge Agent result
```

Migration `009_pgvector_knowledge_index` stores a `vector(1024)` Embedding plus
the model, revision, input SHA-256, status, and timestamp. Only
`active_knowledge_chunk` is indexed or searched. The corpus is small, so the
production query uses exact `<=>` cosine distance and deliberately creates no
IVFFlat or HNSW index.

The embedding indexer is an explicit command; FastAPI startup never seeds,
migrates, or embeds data. Text hash, model, and revision make repeat runs
incremental. User-ingested knowledge still needs approvals from two different
engineers before it enters the Active Index.

## Runtime switches

The safe default remains:

```text
YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE=keyword
YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED=0
```

Hybrid is selected explicitly with
`YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE=hybrid`. CSV mode uses Python BM25 plus
exact in-memory vectors. PostgreSQL mode uses FTS plus exact pgvector search.
The Reranker has a separate Feature Flag and can optionally load a local model
snapshot through `YIELD_RCA_KNOWLEDGE_RERANKER_LOCAL_PATH`.

No model is loaded merely by constructing the default API. When Hybrid is
enabled, the Embedding backend resolves lazily. When the Reranker flag is off,
the Cross-Encoder is never constructed by the online request path.

## Score contracts

The API and frontend keep different meanings separate:

- `lexical`, `vector`, and `fusion` are retrieval-stage ranking scores.
- `reranker` is the sigmoid of the Cross-Encoder raw logit and is used for
  ordering, not as a statistical probability claim.
- `calibrated_relevance` is present only when a model/revision-matched Platt
  artifact is configured; otherwise it is `null`.
- `source_confidence` is an approval-governance weight, not relevance.
- none of these fields is RCA conclusion confidence.

The Knowledge Agent consumes typed logical-asset results. Historical RCA work
is restricted to `RCA_CASE`; SOP and Engineering Note assets remain available
through their independent Knowledge Lookup question kinds and cannot be
mistaken for root-cause evidence.

## Independent calibration and test sets

`retrieval_calibration_split.json` fixes 18 stratified calibration query IDs.
They cover the three question kinds, cross-language, hard-negative, and
no-answer slices. The remaining 96 queries form the untouched final test set.
The Platt artifact is trained only on candidate pairs from the 18 calibration
queries. Final Recall, MRR, nDCG, hard-negative, no-answer, and leakage metrics
use only the 96 disjoint test queries.

## Measured release decision

The pinned GPU evaluation used:

- Embedding: `BAAI/bge-m3` revision
  `5617a9f61b028005a4858fdac845db406aefb181`
- Reranker: `BAAI/bge-reranker-v2-m3` revision
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- GPU: NVIDIA GeForce RTX 4070 SUPER

| Retriever | Recall@5 | MRR@10 | nDCG@10 | Hard-negative | No-answer | Leakage |
|---|---:|---:|---:|---:|---:|---:|
| Hybrid-RRF | 98.17% | 1.0000 | 0.9205 | 100% | 100% | 0 |
| Hybrid + CrossEncoder | 98.17% | 1.0000 | 0.9202 | 100% | 100% | 0 |

The Reranker did not strictly improve nDCG, so the release gate keeps
`YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED=0`. This is a successful safety decision,
not a failed implementation: an optional model is not promoted without a
measured gain.

Additional gates passed:

- Historical Overreach Rate: 0% across 64 historical-only probes.
- Unapproved Knowledge leakage: 0.
- Independent Knowledge Lookup returns `root_cause_conclusion=null`.
- Independent lookup records exactly one Knowledge Agent trace and invokes no
  MES, FDC, Defect/WAT, or RCA Reasoning Agent.

The reproducible artifacts are under
`outputs/long_task_4_evaluation/`. Raw logit cache is local-only and excluded
from Git; the fixed calibration artifact, JSON results, and Markdown report are
versioned.
