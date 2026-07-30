import { Check, Database, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

import { decideMemoryCandidate } from "../api";
import type { EngineerRole, MemoryCandidate } from "../types";

interface MemoryApprovalPanelProps {
  candidate: MemoryCandidate;
  onChange: (candidate: MemoryCandidate) => void;
}

const ROLES: Array<{ value: EngineerRole; label: string }> = [
  { value: "yield_engineer", label: "Yield Engineer" },
  { value: "process_engineer", label: "Process Engineer" },
  { value: "equipment_engineer", label: "Equipment Engineer" },
  { value: "quality_engineer", label: "Quality Engineer" },
];

export function MemoryApprovalPanel({ candidate, onChange }: MemoryApprovalPanelProps) {
  const [engineerId, setEngineerId] = useState("");
  const [role, setRole] = useState<EngineerRole>("yield_engineer");
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const terminal = candidate.status !== "pending_approval";

  async function submit(decision: "approve" | "reject") {
    if (!engineerId.trim()) {
      setError("Engineer ID is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await decideMemoryCandidate(candidate.candidate_id, {
        engineer_id: engineerId.trim().toUpperCase(),
        engineer_role: role,
        decision,
        comment: comment.trim(),
      });
      onChange(response.candidate);
      setEngineerId("");
      setComment("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approval request failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="memory-section" aria-labelledby="memory-heading">
      <div className="section-heading-row memory-heading-row">
        <div>
          <span className="section-kicker">Controlled knowledge publication</span>
          <h2 id="memory-heading">Engineering memory approval</h2>
        </div>
        <span className={`memory-status memory-status-${candidate.status}`}>
          {candidate.status.replace("_", " ")}
        </span>
      </div>

      <div className="memory-summary-grid">
        <div>
          <span>Scope</span>
          <strong>{candidate.scope_level === "fab" ? "Fab-level" : "Event-level"}</strong>
        </div>
        <div>
          <span>Approvals</span>
          <strong>
            {candidate.approval_count} / {candidate.required_approval_count}
          </strong>
        </div>
        <div>
          <span>Process review</span>
          <strong>
            {candidate.requires_process_engineer_approval
              ? candidate.has_process_engineer_approval
                ? "Complete"
                : "Required"
              : "Not required"}
          </strong>
        </div>
      </div>

      <p className="memory-root-cause">{candidate.root_cause}</p>

      {candidate.approvals.length > 0 && (
        <div className="approval-list">
          {candidate.approvals.map((approval) => (
            <div key={approval.approval_id}>
              {approval.decision === "approve" ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                <X size={15} aria-hidden="true" />
              )}
              <strong>{approval.engineer_id}</strong>
              <span>{approval.engineer_role.replace("_", " ")}</span>
              <small>{approval.decision}</small>
            </div>
          ))}
        </div>
      )}

      {!terminal && (
        <div className="approval-form">
          <label>
            Engineer ID
            <input
              value={engineerId}
              onChange={(event) => setEngineerId(event.target.value)}
              placeholder="YE001"
            />
          </label>
          <label>
            Role
            <select value={role} onChange={(event) => setRole(event.target.value as EngineerRole)}>
              {ROLES.map((item) => (
                <option value={item.value} key={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="approval-comment">
            Review comment
            <input
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Evidence and proposed actions reviewed"
            />
          </label>
          <div className="approval-buttons">
            <button type="button" onClick={() => submit("approve")} disabled={submitting}>
              <ShieldCheck size={16} aria-hidden="true" />
              Approve
            </button>
            <button
              type="button"
              className="reject-button"
              onClick={() => submit("reject")}
              disabled={submitting}
            >
              <X size={16} aria-hidden="true" />
              Reject
            </button>
          </div>
        </div>
      )}

      {candidate.published_case_id && (
        <div className="published-memory">
          <Database size={16} aria-hidden="true" />
          <span>Published as confirmed knowledge</span>
          <code>{candidate.published_case_id}</code>
        </div>
      )}
      {error && <div className="memory-error">{error}</div>}
    </section>
  );
}
