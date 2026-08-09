# Long Task 4 Final Evaluation

- Overall: `PASS`
- Calibration queries: 18
- Untouched test queries: 96
- Reranker recommended: `NO`
- Selected online strategy: `hybrid_rrf`

## Retrieval comparison

| Retriever | Recall@5 | MRR@10 | nDCG@10 | Hard-negative | No-answer | Leakage |
|---|---:|---:|---:|---:|---:|---:|
| Hybrid-RRF | 98.17% | 1.0000 | 0.9205 | 100.00% | 100.00% | 0 |
| Hybrid + CrossEncoder | 98.17% | 1.0000 | 0.9202 | 100.00% | 100.00% | 0 |

## Release boundaries

- nDCG strictly improved: `False`
- Core metrics did not regress: `True`
- Unapproved knowledge leakage: `0`
- Historical Overreach Rate: 0.00%
- Independent Knowledge lookup uses only Knowledge Agent and returns no RCA conclusion: `True`
- Feature Flag decision honored: `True`

Calibration was fitted only on the fixed calibration IDs. All reported ranking metrics use the disjoint test IDs. Retrieval relevance, calibrated relevance, source confidence, and RCA conclusion confidence remain separate contracts.
