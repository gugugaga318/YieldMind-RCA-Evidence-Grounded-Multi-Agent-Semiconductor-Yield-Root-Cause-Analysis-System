export type TaskStatus = "pending" | "running" | "completed" | "failed" | "skipped";
export type InvestigationMode = "product_window" | "lot";
export type OrchestrationMode = "fixed" | "controlled_react" | "llm_react";
export type EvidenceGapStatus = "open" | "closed" | "unavailable";
export type GoalStatus = "in_progress" | "satisfied" | "blocked" | "budget_exhausted";
export type ConclusionLevel =
  | "signal"
  | "candidate"
  | "supported"
  | "conflicted"
  | "inconclusive";
export type FindingKind =
  | "specialist_observation"
  | "knowledge_discovery"
  | "knowledge_validation"
  | "hypothesis_generation"
  | "hypothesis_ranking"
  | "improvement";

export interface CreateRCAJobRequest {
  investigation_mode: InvestigationMode;
  user_query?: string;
  lot_id?: string;
}

export interface RCAJobCreated {
  job_id: string;
  status: TaskStatus;
  created_at: string;
  investigation_mode: InvestigationMode;
  source_lot_id: string | null;
  state_url: string;
  report_url: string;
  memory_candidate_id: string | null;
  memory_candidate_url: string | null;
}

export interface RuntimeInfo {
  status: "ready";
  agent_mode: "deterministic" | "fake" | "llm";
  model: string;
  dataset: string;
  orchestration_mode: OrchestrationMode;
}

export interface AgentTask {
  task_id: string;
  agent: string;
  objective: string;
  depends_on: string[];
  status: TaskStatus;
  inputs: Record<string, unknown>;
  finding_kind: FindingKind;
}

export interface InvestigationAction {
  action_id: string;
  kind: string;
  agent: string;
  reason: string;
  inputs: Record<string, unknown>;
  scope: Record<string, unknown>;
  required_evidence_ids: string[];
  max_attempts: number;
}

export interface InvestigationQuestion {
  question_id: string;
  goal_id: string;
  question: string;
  rationale: string;
  scope: Record<string, unknown>;
  status: EvidenceGapStatus;
  answer: string | null;
  evidence_ids: string[];
  unavailable_reason: string | null;
}

export interface PlannerDecision {
  decision_id: string;
  goal_id: string;
  decision_type: "act" | "stop";
  reason: string;
  goal_status: GoalStatus;
  proposed_conclusion_level: ConclusionLevel;
  next_action: InvestigationAction | null;
  target_question_ids: string[];
  new_questions: InvestigationQuestion[];
  question_updates: InvestigationQuestion[];
  stop_reason:
    | "goal_satisfied"
    | "critical_contradiction"
    | "no_allowed_action"
    | "budget_exhausted"
    | "data_unavailable"
    | null;
}

export interface EvidenceEntity {
  entity_type: string;
  entity_id: string;
  attributes: Record<string, unknown>;
}

export interface Evidence {
  evidence_id: string;
  source_type: string;
  source_id: string;
  summary: string;
  source_table: string | null;
  source_field: string | null;
  timestamp: string | null;
  metadata: Record<string, unknown>;
  evidence_type?: string | null;
  source_agent?: string | null;
  source_tool?: string | null;
  observation?: string | null;
  entities?: EvidenceEntity[];
  confidence?: number | null;
  evidence_schema_version?: string | null;
}

export interface Warning {
  warning_id: string;
  message: string;
  severity: string;
  evidence_ids: string[];
}

export interface AgentFinding {
  finding_id: string;
  task_id?: string | null;
  agent: string;
  finding_kind?: FindingKind;
  summary: string;
  confidence: number;
  evidence_ids: string[];
  evidence?: Evidence[];
  details: Record<string, unknown>;
  warnings: Warning[];
}

export interface Hypothesis {
  hypothesis_id: string;
  root_cause: string;
  confidence: number;
  evidence_ids: string[];
  status: string;
  rationale: string;
}

export interface RCAReport {
  report_id: string;
  title: string;
  markdown: string;
  cited_evidence_ids: string[];
  created_at: string;
}

export interface LLMUsageEvent {
  call_id: string;
  agent: string;
  provider: string;
  model: string;
  prompt_version: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  latency_ms: number;
  status: "success" | "failed";
}

export interface ExecutionMetadata {
  agent_mode?: "deterministic" | "fake" | "llm";
  provider?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  total_tokens?: number;
  llm_call_count?: number;
  llm_latency_ms?: number;
  tool_call_count?: number;
  tool_latency_ms?: number;
  workflow_duration_ms?: number;
  orchestration_mode?: OrchestrationMode;
  orchestration_requested_mode?: OrchestrationMode;
  orchestration_fallback_reason?: string;
  orchestration_fallback_stage?: "intent_planning" | "next_action_planning";
  orchestration_fallback_after_action_count?: number;
  tool_latencies?: Array<{
    tool_name: string;
    tool_request_id: string;
    agent: string;
    outcome: "success" | "failed";
    duration_ms: number;
  }>;
}

export interface RCAState {
  job: {
    job_id: string;
    user_query: string;
    investigation_mode: InvestigationMode;
    source_lot_id: string | null;
    product_id: string | null;
    time_window: Record<string, string>;
    status: TaskStatus;
    created_at: string;
  };
  task_plan: {
    plan_id: string;
    objective: string;
    tasks: AgentTask[];
  } | null;
  current_task_id: string | null;
  completed_task_ids: string[];
  affected_lots: string[];
  impact_lots: string[];
  affected_wafers: string[];
  impact_wafers: string[];
  scope_level: "lot" | "wafer" | "mixed";
  impact_criteria: Record<string, unknown>;
  evidence: Evidence[];
  findings: AgentFinding[];
  hypotheses: Hypothesis[];
  warnings: Warning[];
  report: RCAReport | null;
  llm_usage: LLMUsageEvent[];
  execution_metadata: ExecutionMetadata;
  investigation_goal?: {
    goal_id: string;
    intent: string;
    summary: string;
    known_facts: Record<string, unknown>;
    required_evidence: string[];
    max_steps: number;
    max_tool_calls: number;
  } | null;
  investigation_questions?: InvestigationQuestion[];
  action_history?: Array<{
    action: {
      action_id: string;
      kind: string;
      agent: string;
      reason: string;
      inputs: Record<string, unknown>;
      scope?: Record<string, unknown>;
      required_evidence_ids: string[];
      max_attempts: number;
    };
    status: "completed" | "skipped" | "failed";
    produced_finding_ids: string[];
    produced_evidence_ids: string[];
    decision_summary: string;
  }>;
  planner_decisions?: PlannerDecision[];
  goal_status?: GoalStatus | null;
  conclusion_level?: ConclusionLevel | null;
  evidence_gaps?: string[];
  stop_reason?: string | null;
}

export interface RCAJobResponse {
  job_id: string;
  status: TaskStatus;
  state: RCAState;
}

export interface RCAReportResponse {
  job_id: string;
  status: TaskStatus;
  report: RCAReport;
}

export interface YieldTrendPoint {
  date: string;
  lot_count: number;
  pass_count: number;
  fail_count: number;
  pass_rate: number;
}

export interface EvidenceChainItem {
  stage: string;
  claim: string;
  confidence: number;
  evidence_ids: string[];
}

export interface RecommendedAction {
  action: string;
  evidence_ids: string[];
}

export interface SpcViolation {
  rule_code: string;
  description: string;
  direction: string;
  sample_ids: string[];
  lot_ids: string[];
  wafer_ids: string[];
  start_timestamp: string;
  end_timestamp: string;
  evidence_id: string;
}

export interface SpcChartResult {
  chart_type: "I_MR" | "XBAR_S" | "XBAR_R" | "P";
  parameter_name: string;
  unit: string;
  status: "OOC" | "IN_CONTROL";
  baseline_id: string;
  baseline_window: { start: string; end: string };
  center_line: number;
  lower_control_limit: number;
  upper_control_limit: number;
  point_violation_count: number;
  violated_rules: string[];
  violations: SpcViolation[];
  series: Array<{
    sample_id: string;
    lot_id: string;
    wafer_id: string | null;
    timestamp: string;
    value: number;
  }>;
  capability: {
    cp: number | null;
    cpk: number | null;
    pp: number | null;
    ppk: number | null;
    spec_lower: number | null;
    spec_upper: number | null;
    valid_for_decision: boolean;
    warning: string | null;
  } | null;
}

export interface SpcOocContext {
  event_key: string;
  trigger_lot_id: string;
  trigger_wafer_id: string | null;
  trigger_hold: { hold_id: string; hold_code: string } | null;
  spc_rule_codes: string[];
  impact_scopes: Array<{ lot_id: string; hold_id: string }>;
}

export type EngineerRole =
  | "yield_engineer"
  | "process_engineer"
  | "equipment_engineer"
  | "quality_engineer";

export interface MemoryApproval {
  approval_id: string;
  candidate_id: string;
  engineer_id: string;
  engineer_role: EngineerRole;
  decision: "approve" | "reject";
  comment: string;
  decided_at: string;
}

export interface MemoryCandidate {
  candidate_id: string;
  job_id: string;
  status: "pending_approval" | "published" | "rejected";
  scope_level: "event" | "fab";
  title: string;
  incident_summary: string;
  engineering_summary: string;
  root_cause: string;
  confidence: number;
  recommendations: Record<string, Array<Record<string, unknown>>>;
  evidence_ids: string[];
  source_lot_id: string | null;
  product_id: string | null;
  requires_process_engineer_approval: boolean;
  approvals: MemoryApproval[];
  approval_count: number;
  required_approval_count: number;
  has_process_engineer_approval: boolean;
  published_case_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryCandidateResponse {
  candidate: MemoryCandidate;
}

export interface MemoryApprovalRequest {
  engineer_id: string;
  engineer_role: EngineerRole;
  decision: "approve" | "reject";
  comment: string;
}
