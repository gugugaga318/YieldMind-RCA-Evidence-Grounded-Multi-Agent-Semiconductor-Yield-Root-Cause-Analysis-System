# Step 19 Memory Approval and Publication

## Boundary

Improvement Agent still does not write a database. After a supported RCA job
finishes, the FastAPI application service converts its traceable Improvement
finding into a `memory_candidate` with status `pending_approval`.

```text
Supported RCAState
  -> Improvement Agent output
  -> memory_candidate + minimal Evidence Snapshot
  -> two independent engineer decisions
  -> confirmed rca_case + knowledge_document + Keyword index update
```

An inconclusive RCA does not create a publishable candidate. Draft candidates
are not visible to Knowledge Agent and cannot become historical evidence.

## Approval Policy

- Event-level and Fab-level candidates both require two approvals.
- The approvers must have different `engineer_id` values.
- One engineer can submit only one decision for a candidate.
- A rejection immediately makes the candidate terminal and prevents publication.
- When Recipe Optimization recommendations exist, one of the two approvers must
  have role `process_engineer`.
- Approval does not modify a production Recipe. It only confirms the RCA memory
  and its engineering recommendations.

The API currently accepts engineer identity as structured request data. Trusted
identity, authorization, and role mapping remain part of the later Security and
Permissions phase.

## Persistence

Migration `003_memory_approval` adds:

```text
memory_candidate
memory_approval
rca_case.validation_status
rca_case.source_candidate_id
rca_case.approval_count
knowledge_document.validation_status
```

Migration `006_memory_snapshot_index_update` adds published-memory audit data:

```text
memory_candidate.evidence_snapshot
memory_candidate.knowledge_provenance
memory_candidate.reasoning_engine
memory_candidate.index_status / index_attempts / index_error
knowledge_index_update
```

The snapshot contains only cited Typed Evidence: observation, type, entity,
confidence, and source Tool/Agent provenance. It deliberately excludes raw MES,
FDC, defect, and WAT table payloads, so published knowledge remains auditable
without copying complete production data.

Publication inserts an RCA case and knowledge document in the same PostgreSQL
transaction that marks the candidate `published`. Published records have
`validation_status = CONFIRMED`. Knowledge retrieval filters out any record
that is not confirmed.

The Keyword Retriever reads confirmed PostgreSQL records, so this index update
is completed synchronously after approval. Its durable status remains available
for audit and future retries; vector/embedding indexing is a later phase and
is never invoked inside the approval transaction.

CSV runtime uses an in-memory Memory Store for UI and contract testing. It is
cleared when FastAPI restarts and cannot update static CSV knowledge. PostgreSQL
runtime persists candidates, approvals, and confirmed cases so future RCA jobs
can retrieve them.

## API

```text
GET  /rca/jobs/{job_id}/memory-candidate
GET  /memory/candidates/{candidate_id}
POST /memory/candidates/{candidate_id}/approvals
```

Approval request:

```json
{
  "engineer_id": "PE001",
  "engineer_role": "process_engineer",
  "decision": "approve",
  "comment": "Evidence and Recipe DOE gate reviewed."
}
```

The RCA job creation response includes `memory_candidate_id` and
`memory_candidate_url` when the RCA conclusion is eligible.

## Audit

Step 19 records:

```text
MEMORY_CANDIDATE_CREATED
MEMORY_APPROVAL_RECORDED
MEMORY_CANDIDATE_PUBLISHED
MEMORY_CANDIDATE_REJECTED
```

Audit data records decision metadata and engineer identity but does not include
database credentials, complete prompts, or full serialized RCA states.
