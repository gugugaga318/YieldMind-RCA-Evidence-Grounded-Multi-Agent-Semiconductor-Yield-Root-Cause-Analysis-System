import {
  BookOpen,
  CheckCircle2,
  FileCheck2,
  SearchX,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  decideKnowledgeIngestion,
  ingestKnowledge,
  listKnowledgeIngestions,
} from "../api";
import type {
  EngineerRole,
  KnowledgeDocumentType,
  KnowledgeIngestionCandidate,
  KnowledgeLookupResult,
} from "../types";

interface KnowledgeWorkspaceProps {
  result: KnowledgeLookupResult | null;
}

export function KnowledgeWorkspace({ result }: KnowledgeWorkspaceProps) {
  const [candidates, setCandidates] = useState<KnowledgeIngestionCandidate[]>([]);
  const [ingestionError, setIngestionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [documentType, setDocumentType] =
    useState<KnowledgeDocumentType>("SOP");
  const [title, setTitle] = useState("");
  const [module, setModule] = useState("Cu CMP");
  const [caseId, setCaseId] = useState("");
  const [tags, setTags] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [engineerId, setEngineerId] = useState("");
  const [engineerRole, setEngineerRole] =
    useState<EngineerRole>("yield_engineer");

  async function refreshCandidates() {
    const response = await listKnowledgeIngestions();
    setCandidates(response.candidates);
  }

  useEffect(() => {
    refreshCandidates().catch((caught) => {
      setIngestionError(
        caught instanceof Error ? caught.message : "Failed to load ingestions",
      );
    });
  }, []);

  async function submitIngestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setIngestionError(null);
    try {
      const formData = new FormData();
      formData.set("file", file);
      formData.set("document_type", documentType);
      formData.set("title", title);
      formData.set("module", module);
      formData.set("tags", tags);
      if (documentType === "RCA_CASE") formData.set("case_id", caseId);
      const response = await ingestKnowledge(formData);
      setCandidates((current) => [response.candidate, ...current]);
      setTitle("");
      setTags("");
      setFile(null);
      const input = document.getElementById("knowledge-file") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (caught) {
      setIngestionError(
        caught instanceof Error ? caught.message : "Knowledge ingestion failed",
      );
    } finally {
      setBusy(false);
    }
  }

  async function decide(
    candidate: KnowledgeIngestionCandidate,
    decision: "approve" | "reject",
  ) {
    if (!engineerId.trim()) {
      setIngestionError("Enter an engineer ID before recording a decision.");
      return;
    }
    setBusy(true);
    setIngestionError(null);
    try {
      const response = await decideKnowledgeIngestion(candidate.candidate_id, {
        engineer_id: engineerId,
        engineer_role: engineerRole,
        decision,
        comment: "Reviewed in Knowledge governance workspace.",
      });
      setCandidates((current) =>
        current.map((item) =>
          item.candidate_id === candidate.candidate_id ? response.candidate : item,
        ),
      );
    } catch (caught) {
      setIngestionError(
        caught instanceof Error ? caught.message : "Approval decision failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="knowledge-workspace">
      <KnowledgeResults result={result} />

      <section className="knowledge-governance" aria-labelledby="knowledge-governance-heading">
        <div className="section-heading-row">
          <div>
            <span className="section-kicker">Approval-gated ingestion</span>
            <h2 id="knowledge-governance-heading">Knowledge Asset governance</h2>
          </div>
          <ShieldCheck size={22} aria-hidden="true" />
        </div>

        {ingestionError && <div className="error-banner">{ingestionError}</div>}

        <div className="knowledge-governance-grid">
          <form className="knowledge-ingestion-form" onSubmit={submitIngestion}>
            <h3>Stage a document</h3>
            <label htmlFor="knowledge-document-type">Document type</label>
            <select
              id="knowledge-document-type"
              value={documentType}
              onChange={(event) =>
                setDocumentType(event.target.value as KnowledgeDocumentType)
              }
              disabled={busy}
            >
              <option value="SOP">SOP</option>
              <option value="ENGINEERING_NOTE">Engineering Note</option>
              <option value="RCA_CASE">RCA Case attachment</option>
            </select>
            <label htmlFor="knowledge-title">Title</label>
            <input
              id="knowledge-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              required
              disabled={busy}
            />
            <label htmlFor="knowledge-ingestion-module">Module</label>
            <input
              id="knowledge-ingestion-module"
              value={module}
              onChange={(event) => setModule(event.target.value)}
              required
              disabled={busy}
            />
            {documentType === "RCA_CASE" && (
              <>
                <label htmlFor="knowledge-case-id">Existing Case ID</label>
                <input
                  id="knowledge-case-id"
                  value={caseId}
                  onChange={(event) => setCaseId(event.target.value.toUpperCase())}
                  required
                  disabled={busy}
                />
              </>
            )}
            <label htmlFor="knowledge-tags">Tags</label>
            <input
              id="knowledge-tags"
              value={tags}
              onChange={(event) => setTags(event.target.value)}
              placeholder="scratch, retaining ring"
              disabled={busy}
            />
            <label htmlFor="knowledge-file">Text PDF, Markdown, or TXT</label>
            <input
              id="knowledge-file"
              type="file"
              accept=".pdf,.md,.markdown,.txt,application/pdf,text/plain,text/markdown"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              required
              disabled={busy}
            />
            <button className="primary-button" type="submit" disabled={busy || !file}>
              <Upload size={16} aria-hidden="true" />
              Parse and stage
            </button>
            <small>
              Parsing and chunking do not publish content. Two different engineers must approve.
            </small>
          </form>

          <div className="knowledge-approval-queue">
            <div className="approval-identity-row">
              <div>
                <label htmlFor="knowledge-engineer-id">Reviewing engineer</label>
                <input
                  id="knowledge-engineer-id"
                  value={engineerId}
                  onChange={(event) => setEngineerId(event.target.value.toUpperCase())}
                  placeholder="YE001"
                  disabled={busy}
                />
              </div>
              <div>
                <label htmlFor="knowledge-engineer-role">Role</label>
                <select
                  id="knowledge-engineer-role"
                  value={engineerRole}
                  onChange={(event) => setEngineerRole(event.target.value as EngineerRole)}
                  disabled={busy}
                >
                  <option value="yield_engineer">Yield Engineer</option>
                  <option value="process_engineer">Process Engineer</option>
                  <option value="equipment_engineer">Equipment Engineer</option>
                  <option value="quality_engineer">Quality Engineer</option>
                </select>
              </div>
            </div>
            <h3>Review queue</h3>
            {candidates.length === 0 && (
              <p className="muted-copy">No user-ingested Knowledge candidates.</p>
            )}
            {candidates.map((candidate) => (
              <article className="knowledge-candidate-card" key={candidate.candidate_id}>
                <div className="candidate-card-heading">
                  <div>
                    <span>{candidate.document_type}</span>
                    <strong>{candidate.title}</strong>
                  </div>
                  <span className={`candidate-status candidate-${candidate.status}`}>
                    {candidate.status.replace("_", " ")}
                  </span>
                </div>
                <p>
                  {candidate.filename} · {candidate.module} · {candidate.chunk_count} chunks
                </p>
                <div className="approval-progress">
                  <FileCheck2 size={15} aria-hidden="true" />
                  {candidate.approval_count}/{candidate.required_approval_count} approvals
                  {candidate.approvals.map((approval) => (
                    <code key={approval.approval_id}>{approval.engineer_id}</code>
                  ))}
                </div>
                {candidate.status === "pending_approval" && (
                  <div className="candidate-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => decide(candidate, "approve")}
                      disabled={busy}
                    >
                      <CheckCircle2 size={15} aria-hidden="true" /> Approve
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => decide(candidate, "reject")}
                      disabled={busy}
                    >
                      <XCircle size={15} aria-hidden="true" /> Reject
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function KnowledgeResults({ result }: { result: KnowledgeLookupResult | null }) {
  if (!result) {
    return (
      <div className="empty-state knowledge-empty-state">
        <BookOpen size={30} aria-hidden="true" />
        <h2>Search approved engineering knowledge</h2>
        <p>Choose a reference type. Only the Knowledge Agent will run.</p>
      </div>
    );
  }

  return (
    <section className="knowledge-results" aria-labelledby="knowledge-results-heading">
      <div className="knowledge-boundary">
        <ShieldCheck size={18} aria-hidden="true" />
        <div>
          <strong>Reference-only boundary</strong>
          <span>{result.answer_boundary}</span>
        </div>
      </div>
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Knowledge Agent · {result.plan.action}</span>
          <h2 id="knowledge-results-heading">Approved reference results</h2>
        </div>
        <code>{result.lookup_id}</code>
      </div>
      <div className="knowledge-trace-card">
        <strong>Why this Action ran</strong>
        <p>{result.agent_trace[0]?.execution_reason}</p>
        <span>{result.agent_trace[0]?.stop_reason}</span>
      </div>
      {result.status === "no_match" ? (
        <div className="knowledge-no-match">
          <SearchX size={24} aria-hidden="true" />
          <strong>No approved in-scope reference</strong>
          <span>The Agent abstained and did not call RCA specialists.</span>
        </div>
      ) : (
        <div className="knowledge-hit-list">
          {result.hits.map((hit) => (
            <article className="knowledge-hit-card" key={hit.evidence_id}>
              <div className="knowledge-hit-rank">{hit.rank}</div>
              <div>
                <div className="knowledge-hit-heading">
                  <span>{hit.document.document_type}</span>
                  <code>{hit.document.evaluation_asset_id}</code>
                </div>
                <h3>{hit.document.title}</h3>
                <p>{hit.excerpt}</p>
                <div className="knowledge-hit-metadata">
                  <span>{hit.document.module || "All modules"}</span>
                  {hit.document.equipment_type && <span>{hit.document.equipment_type}</span>}
                  <span>strategy {hit.retrieval_strategy}</span>
                  <span>ranking score {hit.score.toFixed(3)}</span>
                  <code>{hit.evidence_id}</code>
                </div>
                <div className="knowledge-score-grid" aria-label="Retrieval score stages">
                  {Object.entries(hit.score_components).map(([name, score]) => (
                    <span key={name}>
                      <b>{name}</b>
                      {score?.toFixed(3)}
                    </span>
                  ))}
                  <span>
                    <b>calibrated relevance</b>
                    {hit.calibrated_relevance === null
                      ? "not calibrated"
                      : hit.calibrated_relevance.toFixed(3)}
                  </span>
                  <span>
                    <b>source confidence</b>
                    {hit.source_confidence === null
                      ? "unknown"
                      : hit.source_confidence.toFixed(3)}
                  </span>
                </div>
                <div className="knowledge-score-boundary">
                  Retrieval relevance and source governance only — neither value is RCA
                  conclusion confidence.
                </div>
                <small>{hit.relevance_reason}</small>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
