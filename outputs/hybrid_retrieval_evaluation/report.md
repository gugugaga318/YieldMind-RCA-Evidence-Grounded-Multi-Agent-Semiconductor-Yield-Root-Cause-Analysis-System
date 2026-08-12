# Hybrid Retrieval Ablation

- Corpus: `synthetic-semiconductor-knowledge-v1`
- Embedding backend: `BAAI/bge-m3`
- Embedding revision: `5617a9f61b028005a4858fdac845db406aefb181`
- Requested device: `auto`
- Resolved device: `cuda`
- Runtime: `sentence-transformers 5.7.0` / `torch 2.13.0+cu130` / `CUDA 13.0`
- Quality metrics are measured comparisons, not predeclared release targets.
- Unapproved knowledge leakage must remain zero for every Retriever.

## Comparison

| Retriever | Recall@5 | Candidate Recall@20 | MRR@10 | nDCG@10 | Cross-language Recall@5 | Hard-negative accuracy | No-answer accuracy | Unapproved hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `Legacy-Case-Keyword` | 41.15% | 55.21% | 0.4018 | 0.3775 | 10.42% | 63.89% | 0.00% | 0 |
| `Chunk-Keyword` | 96.88% | 96.88% | 0.9948 | 0.9153 | 95.83% | 100.00% | 100.00% | 0 |
| `BM25-only` | 97.40% | 97.92% | 1.0000 | 0.9206 | 96.88% | 100.00% | 100.00% | 0 |
| `Vector-only` | 97.92% | 97.92% | 1.0000 | 0.9217 | 97.92% | 100.00% | 100.00% | 0 |
| `Hybrid-RRF` | 97.92% | 97.92% | 1.0000 | 0.9219 | 97.92% | 100.00% | 100.00% | 0 |

## Architecture Boundary

BM25 and Vector independently generate candidates. Hybrid combines their ranks with Reciprocal Rank Fusion; it does not ask an LLM to decide relevance. Document type, approval visibility, metadata scope, and qrels remain Python-owned.

The exact-vector implementation is intentional for this small corpus. pgvector storage, Cross-Encoder reranking, online Agent cutover, and calibrated relevance remain Long Task 4 work.

The Legacy Case Keyword row is a compatibility baseline, not a fair algorithm-only comparison because it cannot retrieve independent SOP or Engineering Note assets. Use Chunk Keyword as the current-online baseline when measuring BM25/Vector/Hybrid ranking gains. All values come from a Synthetic benchmark and do not claim production-fab accuracy.
