import { Check, CircleAlert, GitCompareArrows, ShieldCheck } from "lucide-react";

import { authoritativeRcaDiagnosisFor } from "../selectors";
import type { CausalMatrixClaim, RCAState, RcaCandidateTrace } from "../types";

interface RcaDiagnosisTraceProps {
  state: RCAState;
}

const CLAIM_ORDER = [
  "equipment",
  "chamber",
  "operation",
  "parameter",
  "outcome",
  "mechanism",
  "control",
  "contradiction",
  "temporal",
  "scope",
];

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function StatusPill({ status }: { status: string }) {
  const Icon = status === "supported" ? Check : CircleAlert;
  return (
    <span className={`causal-status causal-status-${status}`}>
      <Icon size={12} aria-hidden="true" />
      {label(status)}
    </span>
  );
}

function EvidenceIds({ ids }: { ids: string[] }) {
  if (ids.length === 0) return <span className="causal-muted">No Evidence IDs</span>;
  return (
    <div className="causal-evidence-ids">
      {ids.map((id) => <code key={id}>{id}</code>)}
    </div>
  );
}

function Matrix({ candidate }: { candidate: RcaCandidateTrace }) {
  const matrix = candidate.causal_evidence_matrix;
  if (!matrix) return <p className="causal-muted">Matrix unavailable for this candidate.</p>;
  const claims = CLAIM_ORDER.flatMap((claim) => {
    const item = matrix.claims[claim];
    return item ? [item] : [];
  });
  for (const [claim, item] of Object.entries(matrix.claims)) {
    if (!CLAIM_ORDER.includes(claim)) claims.push(item);
  }
  return (
    <div className="causal-matrix" aria-label="Causal evidence matrix">
      {claims.map((claim) => <ClaimRow claim={claim} key={claim.claim} />)}
      {matrix.invalid_evidence_ids.length > 0 && (
        <div className="causal-invalid-evidence">
          <strong>Invalid Evidence references</strong>
          <EvidenceIds ids={matrix.invalid_evidence_ids} />
        </div>
      )}
    </div>
  );
}

function ClaimRow({ claim }: { claim: CausalMatrixClaim }) {
  return (
    <div className="causal-claim-row">
      <div className="causal-claim-heading">
        <strong>{label(claim.claim)}</strong>
        <StatusPill status={claim.status} />
      </div>
      <p>{claim.reason || "No diagnostic explanation available."}</p>
      <EvidenceIds ids={claim.evidence_ids} />
    </div>
  );
}

function CandidateCard({ candidate, index, authoritative }: {
  candidate: RcaCandidateTrace;
  index: number;
  authoritative: boolean;
}) {
  return (
    <article className={`causal-candidate ${authoritative ? "causal-candidate-authoritative" : ""}`}>
      <div className="causal-candidate-heading">
        <div>
          <span className="field-label">Candidate {String.fromCharCode(65 + index)}</span>
          <h3>{candidate.root_cause}</h3>
        </div>
        {authoritative && <span className="causal-authoritative">Authoritative</span>}
      </div>
      <div className="causal-candidate-meta">
        <StatusPill status={candidate.causal_matrix_status ?? candidate.status ?? "unavailable"} />
        {candidate.basis && <span>basis: {label(candidate.basis)}</span>}
        {candidate.mechanism_support_source && <span>mechanism: {label(candidate.mechanism_support_source)}</span>}
      </div>
      <Matrix candidate={candidate} />
      {candidate.rejection_reasons.length > 0 && (
        <div className="causal-rejected">
          <strong>Rejected / downgraded</strong>
          <ul>{candidate.rejection_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      )}
    </article>
  );
}

export function RcaDiagnosisTrace({ state }: RcaDiagnosisTraceProps) {
  const diagnosis = authoritativeRcaDiagnosisFor(state);
  if (!diagnosis) return null;
  const preferred = diagnosis.candidate_comparison.preferred_candidate_index;
  const preferredIndex = typeof preferred === "number" ? preferred : null;
  const comparisonExplanation = typeof diagnosis.candidate_comparison.comparison_explanation === "string"
    ? diagnosis.candidate_comparison.comparison_explanation
    : null;
  const comparisonUnresolved = diagnosis.candidate_comparison.unresolved === true;
  const gate = diagnosis.confirmation_gate;
  const gateChecks = gate.checks ? Object.entries(gate.checks) : [];
  const gaps = diagnosis.causal_evidence_gaps;
  const impactRows = diagnosis.impact_lot_gate.rows ?? [];

  return (
    <section className="rca-diagnosis-section" aria-labelledby="rca-diagnosis-heading">
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Python-owned validation trace</span>
          <h2 id="rca-diagnosis-heading">RCA Diagnosis</h2>
        </div>
        <div className="causal-diagnosis-status">
          <StatusPill status={diagnosis.conclusion_status} />
          <span>{diagnosis.ranked_candidates.length} candidate{diagnosis.ranked_candidates.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      {diagnosis.ranked_candidates.length > 0 ? (
        <div className="causal-candidate-grid">
          {diagnosis.ranked_candidates.map((candidate, index) => (
            <CandidateCard
              candidate={candidate}
              index={index}
              authoritative={preferredIndex === index || (preferredIndex === null && candidate.root_cause === diagnosis.root_cause)}
              key={`${candidate.root_cause}:${index}`}
            />
          ))}
        </div>
      ) : (
        <p className="empty-copy">No valid causal candidates were produced.</p>
      )}

      {comparisonExplanation && (
        <div className={`causal-comparison-note ${comparisonUnresolved ? "causal-comparison-unresolved" : ""}`}>
          <GitCompareArrows size={15} aria-hidden="true" />
          <div>
            <strong>Candidate comparison</strong>
            <span>{comparisonExplanation}</span>
          </div>
        </div>
      )}

      <div className="causal-diagnosis-grid">
        <section className="causal-subpanel" aria-labelledby="confirmation-gate-heading">
          <div className="causal-subheading">
            <ShieldCheck size={16} aria-hidden="true" />
            <h3 id="confirmation-gate-heading">Confirmation Gate</h3>
            <StatusPill status={gate.status ?? "unavailable"} />
          </div>
          {gateChecks.length > 0 && (
            <div className="causal-check-list">
              {gateChecks.map(([name, passed]) => (
                <div key={name} className={passed ? "causal-check-pass" : "causal-check-fail"}>
                  {passed ? <Check size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}
                  <span>{label(name)}</span>
                  <strong>{passed ? "pass" : "needs evidence"}</strong>
                </div>
              ))}
            </div>
          )}
          {(gate.reasons?.length ?? 0) > 0 && (
            <ul className="causal-reason-list">{gate.reasons?.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          )}
        </section>

        <section className="causal-subpanel" aria-labelledby="evidence-gaps-heading">
          <div className="causal-subheading">
            <GitCompareArrows size={16} aria-hidden="true" />
            <h3 id="evidence-gaps-heading">Evidence Gaps</h3>
            <span>{gaps.length}</span>
          </div>
          {gaps.length > 0 ? (
            <ul className="causal-gap-list">
              {gaps.map((gap) => (
                <li key={gap.gap_id}>
                  <div><strong>{label(gap.claim)}</strong><StatusPill status={gap.status} /></div>
                  <p>{gap.reason}</p>
                  <span>Allowed: {gap.allowed_actions.length > 0 ? gap.allowed_actions.map(label).join(", ") : "none"}</span>
                  <EvidenceIds ids={gap.evidence_ids} />
                </li>
              ))}
            </ul>
          ) : <p className="causal-muted">No unresolved causal Evidence gaps.</p>}
        </section>
      </div>

      {impactRows.length > 0 && (
        <section className="causal-impact-panel" aria-labelledby="impact-gate-heading">
          <div className="causal-subheading">
            <GitCompareArrows size={16} aria-hidden="true" />
            <h3 id="impact-gate-heading">Impact Lot Gate</h3>
            <span>{diagnosis.impact_lot_gate.confirmed_impact_lots?.length ?? 0} included</span>
          </div>
          <div className="causal-impact-list">
            {impactRows.map((row) => (
              <div className={row.included ? "causal-impact-included" : "causal-impact-excluded"} key={row.lot_id}>
                <strong>{row.lot_id}</strong>
                <StatusPill status={row.included ? "supported" : "incomplete"} />
                <span>{row.included ? row.included_reason : row.excluded_reason}</span>
                <EvidenceIds ids={row.supporting_evidence_ids} />
              </div>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
