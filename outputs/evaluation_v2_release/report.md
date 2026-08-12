# Evaluation V2 Final Release Decision

> Synthetic benchmark only. This report is not a production-Fab accuracy claim.

- Release status: **NOT_READY**
- Data Quality gate: **PASS**
- Governance gate: **PASS**
- Retrieval Quality gate: **FAIL**
- RCA Quality gate: **BLOCKED**

## Selected runtime

- Retriever: `chunk_keyword`
- Causal Scope: `enabled`
- Reranker: `disabled`
- Reranker reason: Local bge-reranker-v2-m3 weights were unavailable and no strict measured nDCG uplift was established.

## Measured, interview-safe claims

- Four-lane causal Scope raised cross-Module Recall@5 from 16.67% to 71.67%, while same-Module Recall@5 remained 100.00%.
  Boundary: Reviewed Synthetic V2 retrieval Test partition only.
- Hybrid-RRF measured Recall@5 79.76%, nDCG@10 0.6222, hard-negative pairwise 57.14%, and in-scope No-answer 100.00%.
  Boundary: Hybrid failed its non-regression gate and is not the selected runtime.
- The deterministic fixed reference achieved 6/6 supported root causes, 100.00% Impact precision, 100.00% Impact recall, and 100.00% correct abstention.
  Boundary: Reviewed Synthetic V2 RCA Test partition; this is a deterministic reference, not a real-Qwen or production-Fab accuracy claim.

## Blocking decisions

- Hybrid-RRF is implemented but not promoted because it regressed the hard-negative pairwise metric against Chunk Keyword.
- The optional Reranker was not measured with an available local model, so it remains disabled behind its Feature Flag.
- The RCA gate remains BLOCKED until the explicitly paid, capped real-Qwen llm_react Test partition completes without fallback.
