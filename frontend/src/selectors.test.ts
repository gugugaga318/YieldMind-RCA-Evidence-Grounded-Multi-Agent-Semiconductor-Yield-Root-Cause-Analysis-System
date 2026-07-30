import { describe, expect, it } from "vitest";

import {
  buildRCAJobRequest,
  formatAgentName,
  getDefaultLotId,
  getEvidenceChain,
  getTargetChamber,
  getYieldTrend,
} from "./selectors";
import type { RCAState } from "./types";

function stateFixture(): RCAState {
  return {
    job: {
      job_id: "RCA_TEST",
      user_query: "Analyze yield drop.",
      investigation_mode: "product_window",
      source_lot_id: null,
      product_id: "40N_SOC",
      time_window: {},
      status: "completed",
      created_at: "2026-07-20T00:00:00+00:00",
    },
    task_plan: null,
    current_task_id: null,
    completed_task_ids: [],
    affected_lots: ["LOT_A_001"],
    impact_lots: [],
    affected_wafers: [],
    impact_wafers: [],
    scope_level: "lot",
    impact_criteria: {},
    evidence: [],
    findings: [
      {
        finding_id: "MES_FINDING",
        task_id: "task_mes",
        agent: "mes",
        finding_kind: "specialist_observation",
        summary: "MES finding",
        confidence: 0.95,
        evidence_ids: ["EV_MES"],
        warnings: [],
        details: {
          target_commonality: { chamber_id: "CMP_CU03_CH02" },
          yield_trend: [
            {
              date: "2026-07-05",
              lot_count: 7,
              pass_count: 7,
              fail_count: 0,
              pass_rate: 100,
            },
          ],
        },
      },
      {
        finding_id: "RCA_FINDING",
        task_id: "task_rca",
        agent: "rca_reasoning",
        finding_kind: "hypothesis_ranking",
        summary: "RCA finding",
        confidence: 0.95,
        evidence_ids: ["EV_MES"],
        warnings: [],
        details: {
          evidence_chain: [
            {
              stage: "mes",
              claim: "Affected lots share one chamber.",
              confidence: 0.95,
              evidence_ids: ["EV_MES"],
            },
          ],
        },
      },
    ],
    hypotheses: [],
    warnings: [],
    report: null,
    llm_usage: [],
    execution_metadata: {
      agent_mode: "deterministic",
      total_tokens: 0,
      tool_call_count: 6,
    },
  };
}

describe("RCAState display selectors", () => {
  it("submits both the Lot ID and investigation request for controlled investigations", () => {
    expect(
      buildRCAJobRequest(
        "lot",
        "  Investigate scratch in Cu CMP.  ",
        " lot_a_001 ",
      ),
    ).toEqual({
      investigation_mode: "lot",
      lot_id: "LOT_A_001",
      user_query: "Investigate scratch in Cu CMP.",
    });
  });

  it("keeps product-window requests free of a stale Lot ID", () => {
    expect(buildRCAJobRequest("product_window", " Analyze yield drop. ", "LOT_A_001")).toEqual({
      investigation_mode: "product_window",
      user_query: "Analyze yield drop.",
    });
  });

  it("selects the valid demo Lot for the active dataset", () => {
    expect(getDefaultLotId("golden_case")).toBe("LOT_A_001");
    expect(getDefaultLotId("multi_case")).toBe("LOT_A_015");
    expect(getDefaultLotId("spc_case")).toBe("LOT_A_015");
  });

  it("reads backend-provided yield points without recalculating them", () => {
    expect(getYieldTrend(stateFixture())).toEqual([
      {
        date: "2026-07-05",
        lot_count: 7,
        pass_count: 7,
        fail_count: 0,
        pass_rate: 100,
      },
    ]);
  });

  it("reads target chamber and evidence chain from agent findings", () => {
    const state = stateFixture();
    expect(getTargetChamber(state)).toBe("CMP_CU03_CH02");
    expect(getEvidenceChain(state)[0].evidence_ids).toEqual(["EV_MES"]);
  });

  it("prefers finding_kind over the first matching agent finding", () => {
    const state = stateFixture();
    state.findings.unshift({
      finding_id: "RCA_DRAFT",
      task_id: "task_rca_draft",
      agent: "rca_reasoning",
      finding_kind: "hypothesis_generation",
      summary: "Draft RCA finding",
      confidence: 0.4,
      evidence_ids: ["EV_DRAFT"],
      warnings: [],
      details: {
        evidence_chain: [
          {
            stage: "mes",
            claim: "Draft claim.",
            confidence: 0.4,
            evidence_ids: ["EV_DRAFT"],
          },
        ],
      },
    });

    expect(getEvidenceChain(state)[0].evidence_ids).toEqual(["EV_MES"]);
  });

  it("returns empty display data when optional backend fields are absent", () => {
    const state = stateFixture();
    state.findings = [];
    expect(getYieldTrend(state)).toEqual([]);
    expect(getEvidenceChain(state)).toEqual([]);
    expect(getTargetChamber(state)).toBe("Not available");
  });

  it("formats the Improvement Agent timeline label", () => {
    expect(formatAgentName("improvement")).toBe("Improvement");
  });
});
