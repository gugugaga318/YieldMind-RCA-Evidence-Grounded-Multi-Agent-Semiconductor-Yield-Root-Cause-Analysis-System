import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { AgentTask, PlannerDecision, RCAState } from "../types";
import { WorkflowTimeline } from "./WorkflowTimeline";

vi.mock("./AgentDecisionTrace", () => ({
  AgentDecisionTrace: ({ state }: { state: RCAState }) => (
    <section aria-label="Mock agent decision trace">
      <span>{state.planner_decisions?.length ?? 0} planner decisions</span>
      <span>{state.action_history?.length ?? 0} action records</span>
    </section>
  ),
}));

function stateFixture(): RCAState {
  return {
    job: {
      job_id: "JOB_TIMELINE",
      user_query: "Investigate LOT_A_001 scratch in Cu CMP.",
      investigation_mode: "lot",
      source_lot_id: "LOT_A_001",
      product_id: "40N_SOC",
      time_window: {},
      status: "completed",
      created_at: "2026-07-31T00:00:00+00:00",
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
    findings: [],
    hypotheses: [],
    warnings: [],
    report: null,
    llm_usage: [],
    execution_metadata: { orchestration_mode: "fixed" },
  };
}

function actionRecord(actionId: string, kind = "inspect_defect_pattern") {
  return {
    action: {
      action_id: actionId,
      kind,
      agent: "defect_wat",
      reason: "Inspect the known scratch before selecting another action.",
      inputs: { lot_id: "LOT_A_001" },
      scope: { lot_id: "LOT_A_001", module: "CU_CMP" },
      required_evidence_ids: [],
      max_attempts: 1,
    },
    status: "completed" as const,
    produced_finding_ids: [`FINDING_${actionId}`],
    produced_evidence_ids: [`EV_${actionId}`],
    decision_summary: "The source Lot has a scratch signature.",
  };
}

function stopDecision(decisionId = "DECISION_STOP"): PlannerDecision {
  return {
    decision_id: decisionId,
    goal_id: "GOAL_LOT_01",
    decision_type: "stop",
    reason: "The requested investigation boundary has been reached.",
    goal_status: "satisfied",
    proposed_conclusion_level: "supported",
    next_action: null,
    target_question_ids: [],
    new_questions: [],
    question_updates: [],
    stop_reason: "goal_satisfied",
  };
}

const fixedTasks: AgentTask[] = [
  {
    task_id: "TASK_MES",
    agent: "mes",
    objective: "Resolve the Lot context.",
    depends_on: [],
    status: "completed",
    inputs: {},
    finding_kind: "specialist_observation",
  },
];

describe("WorkflowTimeline orchestration routing", () => {
  it("routes a complete llm_react trace to AgentDecisionTrace", () => {
    const state = stateFixture();
    state.execution_metadata = {
      orchestration_requested_mode: "llm_react",
      orchestration_mode: "llm_react",
    };
    state.planner_decisions = [stopDecision()];
    state.action_history = [actionRecord("ACTION_DEFECT")];

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={[]} state={state} />,
    );

    expect(html).toContain('aria-label="Mock agent decision trace"');
    expect(html).toContain("1 planner decisions");
  });

  it("routes an immediate Qwen stop even when no action was executed", () => {
    const state = stateFixture();
    state.execution_metadata = {
      orchestration_requested_mode: "llm_react",
      orchestration_mode: "llm_react",
    };
    state.planner_decisions = [stopDecision("DECISION_IMMEDIATE_STOP")];
    state.action_history = [];

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={[]} state={state} />,
    );

    expect(html).toContain('aria-label="Mock agent decision trace"');
    expect(html).toContain("0 action records");
    expect(html).not.toContain("Agent Workflow");
  });

  it("keeps a mid-loop Qwen prefix and controlled fallback tail in AgentDecisionTrace", () => {
    const state = stateFixture();
    state.execution_metadata = {
      orchestration_requested_mode: "llm_react",
      orchestration_mode: "controlled_react",
      orchestration_fallback_reason: "qwen_next_action_output_invalid",
      orchestration_fallback_stage: "next_action_planning",
      orchestration_fallback_after_action_count: 1,
    };
    state.planner_decisions = [stopDecision("DECISION_QWEN_PREFIX")];
    state.action_history = [
      actionRecord("ACTION_QWEN"),
      actionRecord("ACTION_CONTROLLED_TAIL", "find_shared_exposure"),
    ];

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={[]} state={state} />,
    );

    expect(html).toContain('aria-label="Mock agent decision trace"');
    expect(html).toContain("2 action records");
    expect(html).not.toContain("Controlled ReAct Investigation Path");
  });

  it("uses ControlledTimeline for intent fallback before any Qwen decision", () => {
    const state = stateFixture();
    state.execution_metadata = {
      orchestration_requested_mode: "llm_react",
      orchestration_mode: "controlled_react",
      orchestration_fallback_reason: "qwen_intent_output_invalid",
      orchestration_fallback_stage: "intent_planning",
      orchestration_fallback_after_action_count: 0,
    };
    state.action_history = [actionRecord("ACTION_CONTROLLED")];
    state.planner_decisions = [];

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={[]} state={state} />,
    );

    expect(html).toContain("Controlled ReAct Investigation Path");
    expect(html).toContain("Observe → Act → Re-plan");
    expect(html).not.toContain('aria-label="Mock agent decision trace"');
  });

  it("keeps a native controlled_react action history on ControlledTimeline", () => {
    const state = stateFixture();
    state.execution_metadata = { orchestration_mode: "controlled_react" };
    state.action_history = [actionRecord("ACTION_CONTROLLED")];

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={[]} state={state} />,
    );

    expect(html).toContain("Controlled ReAct Investigation Path");
    expect(html).toContain("Observe → Act → Re-plan");
  });

  it("keeps the fixed task workflow as the compatibility baseline", () => {
    const state = stateFixture();

    const html = renderToStaticMarkup(
      <WorkflowTimeline tasks={fixedTasks} state={state} />,
    );

    expect(html).toContain("Agent Workflow");
    expect(html).toContain("1/1 complete");
    expect(html).toContain("Resolve the Lot context.");
    expect(html).not.toContain("Controlled ReAct Investigation Path");
    expect(html).not.toContain('aria-label="Mock agent decision trace"');
  });
});
