# Retrieval Evaluation and Synthetic Knowledge Corpus

## Purpose

The retrieval evaluation suite is intentionally independent from
`data/evaluation/scenarios.json`:

- `scenarios.json` evaluates the end-to-end RCA outcome: conclusion, evidence,
  abstention, and traceability.
- `retrieval_ground_truth.json` evaluates only the Retriever: whether the right
  governed knowledge assets are ranked for a query.

A good retrieval score does not prove that the RCA conclusion is correct, and a
correct RCA result can still hide a weak Retriever. Keeping both test sets makes
those failures observable.

## Synthetic V1 Corpus

The committed offline corpus contains:

- 36 confirmed Synthetic RCA Cases;
- 12 confirmed independent SOPs;
- 12 confirmed independent Engineering Notes;
- 3 deliberately unapproved leakage sentinels;
- 96 answerable English, Chinese, and mixed-language queries;
- 18 no-answer queries;
- 114 total queries.

All incidents, identifiers, measurements, and conclusions are synthetic. Public
material may be used only to check generic semiconductor terminology; the
corpus does not copy source text. Built-in confirmed assets use publication
policy `BUILTIN_SYNTHETIC_SEED` and do not fabricate engineer approvals.

Canonical facts are maintained in:

```text
data/knowledge/synthetic_v1/canonical_facts.json
```

The generator produces:

```text
data/knowledge/synthetic_v1/rca_case.csv
data/knowledge/synthetic_v1/knowledge_document.csv
data/knowledge/synthetic_v1/corpus.json
data/knowledge/synthetic_v1/generation_manifest.json
data/evaluation/retrieval_ground_truth.json
```

`rca_case.csv` and `knowledge_document.csv` preserve compatibility with the
current `CsvFabRepository`. SOPs and Engineering Notes have an empty `case_id`
because they are independent knowledge assets.

## Query Leakage Prevention

Document generation and query generation are separate contracts:

1. The document writer may see the complete reviewed Canonical Fact.
2. The query writer receives only an opaque query key, language, question kind,
   module, equipment type, and observable context.
3. It never receives root cause, corrective action, procedure steps,
   interpretation, generated document text, Case ID, Document ID, or title.
4. Python builds qrels from reviewed Canonical Fact links. Qwen cannot assign or
   change the correct answer.
5. Generated queries are rejected if they copy an ID, a complete root-cause
   phrase, or another forbidden answer term.

This prevents an artificially high score caused by putting the answer directly
inside the benchmark query.

## Metrics

Logical knowledge assets are evaluated after aggregation. RCA results use
`case_id`; independent SOP and Engineering Note results use `document_id`.
Duplicate logical IDs are a contract error rather than being silently removed.

The relevance grades are:

- `3`: exact answer;
- `2`: strongly relevant and directly usable;
- `1`: useful background;
- `0`: irrelevant or an explicit Hard Negative.

For V1, grades 2 and 3 count as relevant for Recall and MRR. Grade 1 still
contributes to graded nDCG.

- Recall@5: macro-average fraction of relevant assets in the final Top 5.
- Candidate Recall@20: macro-average fraction in the candidate Top 20.
- MRR@10: reciprocal rank of the first relevant final result.
- nDCG@10: graded ranking quality using gain `2^grade - 1`.
- Cross-language Recall@5: Recall@5 over explicitly tagged cross-language
  queries.
- Hard-negative accuracy: a relevant result must appear in the Top 10 before
  every explicit Hard Negative.
- No-answer accuracy: a no-answer query must produce no final hit.
- No-answer false-positive rate: fraction of no-answer queries with a final hit.
- Unapproved knowledge leakage: any non-`CONFIRMED` asset in the candidate or
  final ranking.

Quality numbers are a baseline, not a predeclared release threshold. The V1 hard
release gate is unapproved knowledge leakage equal to zero.

## Reproduce the Committed Corpus

The default command is deterministic and makes no network or paid model call:

```powershell
.\.venv\Scripts\python.exe scripts\generate_synthetic_knowledge_corpus.py
```

The manifest records the Canonical Fact SHA-256, provider, model, prompts,
publication policy, counts, and whether root cause was exposed to the query
writer.

## Optional Qwen Enrichment

Qwen document and query writing is an explicit offline operation. It uses
separate prompts, makes at most 17 calls with the default batch size for this
corpus, disables hidden HTTP retry, and refuses to run without both the API key
and an explicit paid-call confirmation.

```powershell
$previousApiKey = [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Process")
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    .\.venv\Scripts\python.exe scripts\generate_synthetic_knowledge_corpus.py `
        --provider qwen `
        --confirm-paid-qwen `
        --batch-size 10 `
        --max-paid-calls 20
} finally {
    if ($null -eq $previousApiKey) {
        Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DASHSCOPE_API_KEY = $previousApiKey
    }
    Remove-Variable qwenSecret, previousApiKey -ErrorAction SilentlyContinue
}
```

The validated generated text replaces only the generated corpus snapshot. Qrels
remain deterministic Python output. Raw model responses, prompts, and API keys
are not written to the corpus.

## Run the KeywordRetriever Baseline

```powershell
.\.venv\Scripts\python.exe scripts\run_retrieval_evaluation.py
```

Outputs:

```text
outputs/retrieval_evaluation/results.json
outputs/retrieval_evaluation/report.md
```

The deterministic V1 baseline is:

| Metric | KeywordRetriever |
|---|---:|
| Recall@5 | 41.15% |
| Candidate Recall@20 | 55.21% |
| MRR@10 | 0.4018 |
| nDCG@10 | 0.3775 |
| Cross-language Recall@5 | 10.42% |
| Hard-negative accuracy | 63.89% |
| No-answer accuracy | 0.00% |
| No-answer false-positive rate | 100.00% |
| Unapproved hit count | 0 |

These results expose the current architecture honestly:

- `KeywordRetriever` returns only Case-level `KnowledgeAsset` objects, so
  independent SOP and Engineering Note Recall@5 is 0%.
- Whitespace token matching is weak for Chinese and mixed-language queries.
- Case confidence is mixed into the legacy score.
- The Retriever has no calibrated abstention output and therefore answers every
  no-answer query.

Long Task 1 does not change the online Retriever, Tool Layer, Evidence score, or
RCA Workflow. Later tasks can compare BM25, Vector, RRF, and Reranker variants
against this exact baseline before any production cutover.
