import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RCAState } from "../types";
import { RcaDiagnosisTrace } from "./RcaDiagnosisTrace";

function stateFixture(): RCAState {
  return {
    job: {
      job_id: "JOB_RCA_TRACE",
      user_query: "Find the root cause.",
      investigation_mode: "lot",
      source_lot_id: "LOT_SOURCE",
      product_id: null,
      time_window: {},
      status: "completed",
      created_at: "2026-08-15T00:00:00+00:00",
    },
    task_plan: null,
    current_task_id: null,
    completed_task_ids: [],
    affected_lots: [],
    impact_lots: [],
    affected_wafers: [],
    impact_wafers: [],
    scope_level: "lot",
    impact_criteria: {},
    evidence: [],
    findings: [
      {
        finding_id: "RCA_AUTH",
        agent: "rca_reasoning",
        finding_kind: "hypothesis_ranking",
        summary: "RCA",
        confidence: 0.8,
        evidence_ids: [],
        warnings: [],
        details: {
          conclusion_status: "inconclusive",
          root_cause: "Backside pressure excursion",
          ranked_candidates: [
            {
              root_cause: "Backside pressure excursion",
              score: 0.8,
              basis: "qwen",
              status: "inconclusive",
              evidence_ids: ["EV_PROCESS"],
              supporting_evidence_ids: ["EV_PROCESS"],
              contradicting_evidence_ids: [],
              rejection_reasons: [],
              causal_matrix_status: "incomplete",
              causal_evidence_matrix: {
                root_cause: "Backside pressure excursion",
                status: "incomplete",
                invalid_evidence_ids: [],
                mechanism_support_source: "empirical_convergence",
                claims: {
                  parameter: {
                    claim: "parameter",
                    status: "supported",
                    evidence_ids: ["EV_PROCESS"],
                    reason: "Parameter is aligned.",
                    facts: {},
                    support_source: null,
                  },
                },
              },
            },
          ],
          causal_evidence_gaps: [
            {
              gap_id: "candidate_0.mechanism.incomplete",
              candidate_index: 0,
              claim: "mechanism",
              status: "incomplete",
              reason: "Mechanism evidence is incomplete.",
              question_kind: "process_mechanism",
              allowed_actions: ["retrieve_knowledge"],
              evidence_ids: [],
            },
          ],
          candidate_comparison: { preferred_candidate_index: 0 },
          confirmation_gate: {
            status: "inconclusive",
            checks: { parameter: true, mechanism: false },
            reasons: ["mechanism claim is incomplete."],
            unresolved_gaps: ["mechanism.incomplete"],
          },
          impact_lot_gate: {
            confirmed_impact_lots: ["LOT_IMPACT"],
            rows: [
              {
                lot_id: "LOT_IMPACT",
                included: true,
                included_reason: "Matching exposure and outcome Evidence.",
                excluded_reason: null,
                supporting_evidence_ids: ["EV_PROCESS"],
              },
            ],
          },
        },
      },
    ],
    hypotheses: [],
    warnings: [],
    report: null,
    llm_usage: [],
    execution_metadata: {},
    authoritative_rca_finding_id: "RCA_AUTH",
  };
}

describe("RcaDiagnosisTrace", () => {
  it("renders candidates, matrix states, gaps, gate, and impact-lot reasons", () => {
    const html = renderToStaticMarkup(<RcaDiagnosisTrace state={stateFixture()} />);
    expect(html).toContain("RCA Diagnosis");
    expect(html).toContain("Backside pressure excursion");
    expect(html).toContain("Mechanism evidence is incomplete");
    expect(html).toContain("Confirmation Gate");
    expect(html).toContain("LOT_IMPACT");
    expect(html).toContain("Matching exposure and outcome Evidence.");
  });

  it("does not render a diagnosis without an authoritative RCA finding", () => {
    const state = stateFixture();
    state.authoritative_rca_finding_id = "MISSING";
    const html = renderToStaticMarkup(<RcaDiagnosisTrace state={state} />);
    expect(html).toBe("");
  });
});
