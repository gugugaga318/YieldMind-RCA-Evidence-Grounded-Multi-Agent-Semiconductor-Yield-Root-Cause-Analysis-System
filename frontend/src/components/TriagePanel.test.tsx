import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import views from "../../../tests/fixtures/copilot_r3/formal005_views.json";
import { InvestigationView } from "../App";
import { comparisonLabels, uncertaintyLabels } from "../triagePresentation";
import type { RCAState } from "../types";
import { TriagePanel } from "./TriagePanel";

function fixture(provider: "deepseek" | "dashscope" = "deepseek"): RCAState {
  return structuredClone(views[provider].state) as unknown as RCAState;
}
function render(s: RCAState) { return renderToStaticMarkup(<TriagePanel state={s} />); }
function page(s: RCAState) { return renderToStaticMarkup(<InvestigationView state={s} memoryCandidate={null} onMemoryCandidateChange={() => {}} />); }
function text(html: string) { return html.replace(/<[^>]*>/g, " ").replaceAll("&amp;", "&").replaceAll("&#x27;", "'").replaceAll("&quot;", '"'); }
afterEach(() => vi.unstubAllGlobals());

describe("Finalizer-owned triage presentation", () => {
  it.each(["deepseek", "dashscope"] as const)("matches saved %s API/report fixture", provider => {
    const s = fixture(provider); const t = s.triage_result!; const html = render(s); const content = text(html);
    expect(content).toContain(t.primary!.engineering_statement);
    if (t.strongest_alternative) expect(content).toContain(t.strongest_alternative.engineering_statement);
    expect(content).toContain(views[provider].comparison_label);
    for (const gap of t.remaining_uncertainty) {
      expect(content).toContain(uncertaintyLabels[gap.kind]); expect(content).toContain(gap.description);
    }
    const a = t.next_investigation_action;
    if (a) { expect(content).toContain(a.requested_observation); a.expected_discriminating_outcomes.forEach(o => expect(content).toContain(o.expected_observation)); }
    else expect(content).toContain(t.no_next_action_reason!);
    expect(content).toContain("Internal RCA root cause: Not published");
    expect(html).not.toContain("confidence-track"); expect(html).not.toContain("python_matrix_score"); expect(html).not.toContain("82% likely");
  });
  it("gives both hypotheses equal visual treatment and explicit tied status", () => {
    const html = render(fixture()); expect(html.match(/class="triage-hypothesis"/g)).toHaveLength(2);
    expect(html).toContain("Tied — not distinguishable with current evidence."); expect(html).not.toContain("clearly more likely");
  });
  it.each(["not_distinguishable", "preference_not_established"] as const)("shows %s without a winner", status => {
    const s = fixture(); s.triage_result!.comparison.preference_status = status;
    expect(render(s)).toContain(comparisonLabels[status]);
  });
  it("distinguishes the two absent-alternative dispositions", () => {
    const s = fixture("dashscope"); expect(render(s)).toContain("Alternative not investigated.");
    s.triage_result!.comparison.alternative_disposition = "no_valid_alternative_identified";
    expect(render(s)).toContain("No valid alternative identified."); expect(render(s)).not.toContain("Alternative eliminated");
  });
  it("does not invent a primary with zero candidates", () => {
    const s = fixture("dashscope"); const t = s.triage_result!;
    t.primary = null; t.evidence_assessment = []; t.remaining_uncertainty.forEach(g => g.candidate_ids = []);
    t.comparison.ordering_basis = "none"; t.terminal_reason = "insufficient_candidate_evidence";
    expect(render(s)).toContain("No admitted primary hypothesis."); expect(render(s)).toContain(t.no_next_action_reason!);
  });
  it("not acquired means missing acquisition, never observed normal", () => {
    const s = fixture(); const t = s.triage_result!;
    t.remaining_uncertainty = [{...t.remaining_uncertainty[0],kind:"not_acquired",description:"Dose observation not acquired."}];
    expect(render(s)).toContain("Evidence not yet acquired"); expect(render(s)).not.toContain("No anomaly observed");
  });
  it("shows unsupported mechanism alongside plausible and unassessed checks", () => {
    const html = render(fixture()); expect(html).toContain("Mechanism not yet supported");
    expect(html).toContain("plausible"); expect(html).toContain("Not assessed");
  });
  it("engineer-only action is an approval-required handoff", () => {
    const html = render(fixture()); expect(html).toContain("recommendation for an engineer to perform");
    expect(html).toContain("Recommendation only; no automatic execution."); expect(html).toContain("external only");
    expect(html).toContain("Approval required</dt><dd>Yes"); expect(html).toContain("Observation supporting Primary");
    expect(html).toContain("Observation supporting Alternative"); expect(html).not.toContain("<button");
  });
  it("null action displays its recorded reason", () => {
    const s = fixture("dashscope"); expect(render(s)).toContain(s.triage_result!.no_next_action_reason!);
  });
  it("coexisting internal confirmation preserves suspected role labels", () => {
    const s = fixture(); const a = s.authoritative_rca_result!;
    a.root_cause = "Old internally confirmed cause"; a.conclusion_status = "supported"; a.confirmation_status = "supported";
    const html = render(s); expect(html).toContain("Primary Suspected Cause"); expect(html).toContain("Internally supported");
    expect(html).toContain("Old internally confirmed cause"); expect(html).toContain("Engineering sign-off: Not recorded"); expect(html).not.toContain("human confirmed");
  });
  it("puts triage before collapsed diagnostics without network calls", () => {
    const spy = vi.fn(() => {throw new Error("Network prohibited");}); vi.stubGlobal("fetch",spy);
    const html = page(fixture()); expect(html.indexOf('id="triage-heading"')).toBeLessThan(html.indexOf('class="triage-diagnostics"'));
    expect(html).toContain("Investigation diagnostics and raw evidence"); expect(html).not.toContain('id="root-cause-heading"'); expect(spy).not.toHaveBeenCalled();
  });
  it("does not backfill absent triage from ranked candidates", () => {
    const s = fixture(); delete s.triage_result;
    s.findings = [{finding_id:"F",agent:"rca_reasoning",summary:"Diagnostic",confidence:0.9,evidence_ids:[],warnings:[],details:{ranked_candidates:[{root_cause:"Wrong synthetic primary"}]}}];
    expect(render(s)).toBe(""); expect(page(s)).not.toContain('id="triage-heading"'); expect(page(s)).toContain('id="root-cause-heading"');
  });
  it("historical confirmed job retains its old view", () => {
    const s = fixture(); delete s.triage_result; s.authoritative_hypothesis_id = "HISTORICAL";
    s.hypotheses = [{hypothesis_id:"HISTORICAL",root_cause:"Historical confirmed cause",status:"supported",confidence:0.7,evidence_ids:[],rationale:"Historical evidence"}];
    s.authoritative_rca_result = {...s.authoritative_rca_result!,conclusion_status:"supported",confirmation_status:"supported",root_cause:"Historical confirmed cause",root_cause_candidate_id:"HISTORICAL",source_hypothesis_id:"HISTORICAL"};
    expect(page(s)).toContain("Historical confirmed cause"); expect(page(s)).not.toContain("Investigation Triage");
  });
  it("escapes authored prose instead of creating an HTML authority heading", () => {
    const s = fixture(); s.triage_result!.primary!.engineering_statement = "<script>Confirmed Root Cause</script>";
    expect(render(s)).toContain("&lt;script&gt;"); expect(render(s)).not.toContain("<script>");
  });
});
