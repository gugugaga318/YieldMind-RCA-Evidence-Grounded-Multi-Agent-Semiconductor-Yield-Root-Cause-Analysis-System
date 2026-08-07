# KeywordRetriever Retrieval Baseline

- Corpus: `synthetic-semiconductor-knowledge-v1`
- Recall relevance threshold: grade >= 2
- Evaluation completed: `PASS`
- Quality metrics: baseline only (no target numbers are predeclared)
- Unapproved knowledge leakage gate: `PASS`

## Metrics

| Metric | Result |
|---|---:|
| Recall@5 | 41.15% |
| Candidate Recall@20 | 55.21% |
| MRR@10 | 0.4018 |
| nDCG@10 | 0.3775 |
| Cross-language Recall@5 | 10.42% |
| Hard-negative accuracy | 63.89% |
| Hard-negative outrank rate | 36.11% |
| No-answer accuracy | 0.00% |
| No-answer false-positive rate | 100.00% |
| Unapproved hit count | 0 |

## Dataset Slices

- All queries: 114
- Answerable queries: 96
- No-answer queries: 18
- Cross-language queries: 48
- Hard-negative queries: 72

## By Question Kind

| Question kind | Queries | Recall@5 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|
| `engineering_note_lookup` | 12 | 0.00% | 0.0000 | 0.0000 |
| `historical_match` | 72 | 54.86% | 0.5357 | 0.5033 |
| `procedure_guidance` | 12 | 0.00% | 0.0000 | 0.0000 |

## Interpretation Boundary

`scenarios.json` still evaluates end-to-end RCA conclusions. This report only evaluates retrieval ranking. A retrieved item is relevant only when the Python qrels contract says so; an LLM cannot override that judgment.

The current KeywordRetriever has no calibrated abstention output, so every returned final hit counts as an answer. Its score mixes token matches and case confidence and is intentionally not reused as a hidden no-answer threshold.
