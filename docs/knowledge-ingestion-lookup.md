# Knowledge Ingestion and Independent Lookup

## Scope

Long Task 2 introduces governed Knowledge Assets without changing the legacy
RCA `KeywordRetriever` score or the Tool/Evidence relevance boundary. It adds a
separate `knowledge_lookup` product path for approved historical Cases, SOPs,
and Engineering Notes.

The independent path is deliberately not an RCA Job:

```text
User Knowledge question
  -> Question Kind / Action capability mapping (Python)
  -> Active Knowledge Chunk index (CONFIRMED only)
  -> Document/Case aggregation
  -> one Knowledge Agent trace
  -> engineering references + explicit answer boundary
```

It never calls MES, FDC, Defect-WAT, RCA Reasoning, or Improvement Agents. The
response always contains `root_cause_conclusion: null` and does not contain
Hypotheses, impact Lots, or an RCA Report.

## Question and Action Mapping

Python owns the one-to-one mapping. A client cannot combine a Question Kind
with an incompatible document type.

| Question Kind | Action | Active asset type |
|---|---|---|
| `historical_match` | `retrieve_historical_case` | `RCA_CASE` |
| `procedure_guidance` | `retrieve_procedure_guidance` | `SOP` |
| `engineering_note_lookup` | `retrieve_engineering_note` | `ENGINEERING_NOTE` |

Metadata filters for module, equipment type, operation, defect type, and tags
are fail-closed. Long Task 2 uses deterministic Chunk keyword ranking and calls
its numeric output a keyword score, not a confidence value. BM25, Vector, RRF,
and reranking remain Long Tasks 3 and 4.

## Ingestion and Approval Boundary

The V1 ingestion pipeline supports UTF-8 Markdown/TXT and text-bearing PDF.
Encrypted PDFs and PDFs with no extractable text return a stable error. Scanned
PDF/OCR is intentionally not supported.

```text
upload
  -> extension, MIME, magic-byte, size, page, and encoding checks
  -> extracted text + SHA-256 (uploaded binary is not persisted)
  -> document-type-aware Chunking + metadata
  -> knowledge_ingestion_* staging tables
  -> engineer 1 approves: still staging
  -> different engineer 2 approves
  -> one transaction publishes knowledge_document + knowledge_chunk
  -> active_knowledge_chunk becomes visible
```

Pending and Rejected content never enters `knowledge_document`. This is an
important defense because the legacy Case adapter retains a compatibility
fail-open default for old CSV rows without `validation_status`; the new Active
Index path itself always requires explicit `CONFIRMED` status.

User-uploaded `RCA_CASE` documents must bind to an existing `case_id`. SOPs and
Engineering Notes may be independent assets with `case_id = null`. The same
engineer cannot decide twice, rejection is terminal, and duplicate Pending or
Confirmed content is rejected by SHA-256.

Built-in Synthetic assets are loaded only when explicitly `CONFIRMED`. They use
`publication_policy=BUILTIN_SYNTHETIC_SEED`; no fake engineer approvals are
created. The three DRAFT leakage sentinels in the corpus are excluded.

## HTTP API

- `POST /knowledge/lookups`
- `POST /knowledge/ingestions` (`multipart/form-data`)
- `GET /knowledge/ingestions?status=pending_approval`
- `GET /knowledge/ingestions/{candidate_id}`
- `POST /knowledge/ingestions/{candidate_id}/approvals`

The React workspace exposes a third `Knowledge` mode with its own result,
ingestion, and approval panels. It does not render the RCA Timeline, Root Cause,
SPC, impact-Lot, or Report panels.

## PostgreSQL Objects

Migration `007_knowledge_ingestion` adds:

- `knowledge_ingestion_candidate`
- `knowledge_ingestion_chunk`
- `knowledge_ingestion_approval`
- `knowledge_chunk`
- `active_knowledge_chunk` view
- document metadata, source, hash, and publication-policy columns

The Seed command loads 60 explicitly confirmed Synthetic assets (36 RCA Cases,
12 SOPs, and 12 Engineering Notes) and generates their Active Chunks. Approved
RCA Memory publications now generate `knowledge_chunk` rows in the same
transaction, so an index marked complete is actually retrievable.

## Local Verification

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\unit\test_knowledge_ingestion_lookup.py `
  tests\integration\test_knowledge_api.py -q

$env:Path = "C:\Users\ybt\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;$env:Path"
& "C:\Users\ybt\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" check
```

For PostgreSQL, run the normal Seed profile after rebuilding the image. The
`/ready` endpoint now rejects a PostgreSQL runtime that does not have migration
007 and the Active Index view.
