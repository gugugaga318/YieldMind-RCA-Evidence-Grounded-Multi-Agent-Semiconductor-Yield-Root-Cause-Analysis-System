import type { Evidence, RCAState, TriageHypothesis, TriageDimension } from "../types";
import { assessmentLabels, comparisonLabels, readable, scopeText, uncertaintyLabels } from "../triagePresentation";

function EvidenceList({ ids, evidence }: { ids: string[]; evidence: Map<string, Evidence> }) {
  if (!ids.length) return <p>None cited; this does not prove absence.</p>;
  return <ul className="triage-evidence">{ids.map(id => {
    const item = evidence.get(id);
    return <li key={id}><code>{id}</code><p>{item?.observation || item?.summary || "Referenced evidence unavailable in this view."}</p></li>;
  })}</ul>;
}

function HypothesisCard({ title, hypothesis, absent, evidence }: {
  title: string; hypothesis: TriageHypothesis | null; absent: string; evidence: Map<string, Evidence>;
}) {
  return <article className="triage-hypothesis">
    <h3>{title}</h3>
    {hypothesis ? <>
      <p className="triage-statement">{hypothesis.engineering_statement}</p>
      <p>{hypothesis.causal_explanation}</p>
      <p className="triage-scope">{scopeText(hypothesis.scope)}</p>
      <details open><summary>Supporting Evidence ({hypothesis.supporting_evidence_ids.length})</summary>
        <EvidenceList ids={hypothesis.supporting_evidence_ids} evidence={evidence} /></details>
      <details><summary>Contradictory Evidence ({hypothesis.contradicting_evidence_ids.length})</summary>
        <EvidenceList ids={hypothesis.contradicting_evidence_ids} evidence={evidence} /></details>
    </> : <p>{absent}</p>}
  </article>;
}

/** Consume the authoritative value only. No ranking, candidate or GT fallback. */
export function TriagePanel({ state }: { state: RCAState }) {
  const t = state.triage_result;
  if (!t) return null;
  const evidence = new Map(state.evidence.map(e => [e.evidence_id, e]));
  const role = (id: string) => id === t.primary?.candidate_id ? "Primary" : id === t.strongest_alternative?.candidate_id ? "Alternative" : id;
  const groups = new Map<string, typeof t.remaining_uncertainty>();
  t.remaining_uncertainty.forEach(gap => groups.set(gap.kind, [...(groups.get(gap.kind) || []), gap]));
  const action = t.next_investigation_action;
  const authority = state.authoritative_rca_result;
  return <section className="triage-panel" aria-labelledby="triage-heading">
    <header className="triage-header"><span className="section-kicker">Engineering Investigation</span>
      <h2 id="triage-heading">Investigation Triage</h2>
      <p>Suspected hypotheses; engineering sign-off: Not recorded.</p>
      <p>Execution completed; scientific question remains unresolved.</p>
    </header>
    <section className="triage-comparison" aria-label="Hypothesis comparison">
      <h3>Comparison</h3><strong>{comparisonLabels[t.comparison.preference_status]}</strong>
      <p>{t.comparison.primary_ordering_reason}</p>
      {t.comparison.alternative_retention_reason && <p>{t.comparison.alternative_retention_reason}</p>}
      {t.comparison.discriminating_evidence_ids.length > 0 && <details><summary>Discriminating Evidence</summary>
        <EvidenceList ids={t.comparison.discriminating_evidence_ids} evidence={evidence} /></details>}
    </section>
    <div className="triage-hypotheses">
      <HypothesisCard title="Primary Suspected Cause" hypothesis={t.primary} absent="No admitted primary hypothesis." evidence={evidence} />
      <HypothesisCard title="Strongest Alternative" hypothesis={t.strongest_alternative}
        absent={t.comparison.alternative_disposition === "no_valid_alternative_identified" ? "No valid alternative identified." : "Alternative not investigated."}
        evidence={evidence} />
    </div>
    <section className="triage-uncertainty" aria-label="Remaining uncertainty"><h3>Remaining Uncertainty</h3>
      {[...groups].map(([kind, gaps]) => <details key={kind} open={kind !== "unavailable"}>
      <summary>{uncertaintyLabels[gaps[0].kind]} ({gaps.length})</summary>
      <ul>{gaps.map((gap,index) => <li key={index}>
        <p>{gap.candidate_ids.map(role).join(" / ")}{gap.candidate_ids.length ? ": " : ""}{gap.description}</p>
        <small>{scopeText(gap.scope)}</small>
        {gap.evidence_ids.length > 0 && <p className="triage-citations">{gap.evidence_ids.join(", ")}</p>}
      </li>)}</ul></details>)}
    </section>
    <section className="triage-next-action" aria-label="Recommended investigation"><h3>Recommended Next Investigation</h3>
      {action ? <>
        <p className="triage-action-notice">Recommendation only; no automatic execution.</p>
        {action.executor === "human_engineer" && <p>This investigation is a recommendation for an engineer to perform.</p>}
        <p>{action.requested_observation}</p>
        <p>Targets: {action.target_candidate_ids.map(role).join(" / ") || "No established hypotheses"}</p>
        <p className="triage-scope">{scopeText(action.scope)}</p>
        <dl className="triage-action-facts"><dt>Executor</dt><dd>{readable(action.executor)}</dd>
          <dt>Approval required</dt><dd>{action.approval_required ? "Yes" : "No"}</dd>
          <dt>Executability</dt><dd>{readable(action.executability)}</dd>
          <dt>Availability</dt><dd>{action.availability}</dd></dl>
        <p>{action.availability_reason}</p>
        <ul>{action.expected_discriminating_outcomes.map(outcome => <li key={outcome.candidate_id}>
          <strong>Observation supporting {role(outcome.candidate_id)}</strong>
          <p>{outcome.expected_observation}</p><small>{outcome.interpretation}</small>
        </li>)}</ul>
        <p>Remaining indistinguishability: {action.inconclusive_outcome}</p>
      </> : <p>{t.no_next_action_reason}</p>}
    </section>
    <details className="triage-assessment" open><summary>Evidence Assessment</summary>
      <p>Support describes the individual evidence check; engineering confirmation is separate.</p>
      {!t.evidence_assessment.length && <p>No admitted hypotheses to assess.</p>}
      {t.evidence_assessment.map(a => <div key={a.candidate_id}><h4>{role(a.candidate_id)}</h4>
        <div className="triage-table-wrap"><table><thead><tr><th>Dimension</th><th>Status</th><th>Observation</th><th>Explanation / Evidence</th></tr></thead>
          <tbody>{(Object.keys(assessmentLabels) as TriageDimension[]).map(key => {
            const claim = a[key];
            return <tr key={key}><th>{assessmentLabels[key]}</th><td><span className={`triage-status ${claim?.status || "unassessed"}`}>{claim ? readable(claim.status) : "Not assessed"}</span></td>
              <td>{claim ? readable(claim.observation_status) : "Unknown"}</td>
              <td>{claim?.explanation || "No recorded assessment"}{claim && <small>{claim.evidence_ids.join(", ")}</small>}</td></tr>;
          })}</tbody></table></div>
      </div>)}
    </details>
    <aside className="triage-confirmation" aria-label="Internal confirmation authority">
      <h3>Confirmation Status</h3><p>Engineering sign-off: Not recorded.</p>
      <p>Internal confirmation: {authority ? authority.confirmation_status === "supported" ? "Internally supported" : readable(authority.confirmation_status) : "Not recorded"}</p>
      <p>Internal RCA root cause: {authority?.root_cause || "Not published"}</p>
      {authority?.root_cause && <p>Confirmed RCA from internal authority; human sign-off remains not recorded.</p>}
    </aside>
  </section>;
}
