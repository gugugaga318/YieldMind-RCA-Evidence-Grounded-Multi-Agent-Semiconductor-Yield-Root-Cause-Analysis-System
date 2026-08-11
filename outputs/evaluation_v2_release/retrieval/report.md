# Evaluation V2 Retrieval Release Report

> Synthetic benchmark only; these are not production-Fab accuracy claims.

- Data-quality gate: **PASS**
- Governance gate: **PASS**
- Retrieval-quality gate: **FAIL**
- Selected retriever: `chunk_keyword`
- Causal Scope enabled: `True`
- Reranker enabled: `False`

## Fair test-partition comparison

| Retriever | Recall@5 | nDCG@10 | Hard-negative pairwise | No-answer | Leakage |
|---|---:|---:|---:|---:|---:|
| Chunk-Keyword | 77.38% | 0.6445 | 59.52% | 0.00% | 0 |
| BM25-only | 79.76% | 0.5939 | 57.14% | 0.00% | 0 |
| Vector-only | 54.76% | 0.4808 | 45.24% | 100.00% | 0 |
| Hybrid-RRF | 79.76% | 0.6222 | 57.14% | 100.00% | 0 |

## Causal Scope ablation

| Scope | Same-module Recall@5 | Cross-module Recall@5 |
|---|---:|---:|
| Legacy observed-Module hard filter | 100.00% | 16.67% |
| Four-lane causal wide recall | 100.00% | 71.67% |

## Failed test Queries

- `Q_V2_IF_V2_008_RCA` via `Chunk-Keyword`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_008_GUIDE` via `Chunk-Keyword`: hard_negative_pairwise
- `Q_V2_IF_V2_010_RCA` via `Chunk-Keyword`: hard_negative_pairwise
- `Q_V2_IF_V2_010_GUIDE` via `Chunk-Keyword`: hard_negative_pairwise
- `Q_V2_IF_V2_011_GUIDE` via `Chunk-Keyword`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_012_RCA` via `Chunk-Keyword`: recall_at_5
- `Q_V2_IF_V2_012_GUIDE` via `Chunk-Keyword`: hard_negative_pairwise
- `Q_V2_IF_V2_013_RCA` via `Chunk-Keyword`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_013_GUIDE` via `Chunk-Keyword`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_NA_02_NO_ANSWER` via `Chunk-Keyword`: no_answer
- `Q_V2_IF_V2_NA_04_NO_ANSWER` via `Chunk-Keyword`: no_answer
- `Q_V2_IF_V2_008_RCA` via `BM25-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_008_GUIDE` via `BM25-only`: hard_negative_pairwise
- `Q_V2_IF_V2_009_GUIDE` via `BM25-only`: hard_negative_pairwise
- `Q_V2_IF_V2_010_RCA` via `BM25-only`: hard_negative_pairwise
- `Q_V2_IF_V2_010_GUIDE` via `BM25-only`: hard_negative_pairwise
- `Q_V2_IF_V2_011_GUIDE` via `BM25-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_012_GUIDE` via `BM25-only`: hard_negative_pairwise
- `Q_V2_IF_V2_013_RCA` via `BM25-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_013_GUIDE` via `BM25-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_NA_02_NO_ANSWER` via `BM25-only`: no_answer
- `Q_V2_IF_V2_NA_04_NO_ANSWER` via `BM25-only`: no_answer
- `Q_V2_IF_V2_008_RCA` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_009_GUIDE` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_010_RCA` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_010_GUIDE` via `Vector-only`: hard_negative_pairwise
- `Q_V2_IF_V2_011_GUIDE` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_012_RCA` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_012_GUIDE` via `Vector-only`: hard_negative_pairwise
- `Q_V2_IF_V2_013_RCA` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_013_GUIDE` via `Vector-only`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_008_RCA` via `Hybrid-RRF`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_008_GUIDE` via `Hybrid-RRF`: hard_negative_pairwise
- `Q_V2_IF_V2_010_RCA` via `Hybrid-RRF`: hard_negative_pairwise
- `Q_V2_IF_V2_010_GUIDE` via `Hybrid-RRF`: hard_negative_pairwise
- `Q_V2_IF_V2_011_GUIDE` via `Hybrid-RRF`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_012_GUIDE` via `Hybrid-RRF`: hard_negative_pairwise
- `Q_V2_IF_V2_013_RCA` via `Hybrid-RRF`: recall_at_5, hard_negative_pairwise
- `Q_V2_IF_V2_013_GUIDE` via `Hybrid-RRF`: recall_at_5, hard_negative_pairwise

The calibration partition selected abstention thresholds. All headline metrics, Scope promotion checks, and failed cases above use only the disjoint test partition. Python owns qrels, approval visibility, Scope, and release decisions.
