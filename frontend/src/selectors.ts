import type {
  ActionRecord,
  AgentFinding,
  AgentTask,
  AgentTraceEvaluationStatus,
  AgentTraceNodeViewModel,
  AgentTraceOrigin,
  AgentTraceViewModel,
  CapabilityNotice,
  CreateRCAJobRequest,
  Evidence,
  EvidenceChainItem,
  FindingKind,
  InvestigationAction,
  InvestigationMode,
  QuestionEvidenceLink,
  OrchestrationMode,
  PlannerDecision,
  RCAState,
  RecommendedAction,
  SpecialistToolStepViewModel,
  SpecialistTraceViewModel,
  ToolLatencyRecord,
  YieldTrendPoint,
} from "./types";

export function buildRCAJobRequest(
  investigationMode: InvestigationMode,
  query: string,
  lotId: string,
): CreateRCAJobRequest {
  const userQuery = query.trim();
  if (investigationMode === "lot") {
    return {
      investigation_mode: "lot",
      lot_id: lotId.trim().toUpperCase(),
      user_query: userQuery,
    };
  }
  return {
    investigation_mode: "product_window",
    user_query: userQuery,
  };
}

export function getDefaultLotId(dataset: string): string {
  return dataset === "multi_case" || dataset === "spc_case" ? "LOT_A_015" : "LOT_A_001";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function findingFor(
  state: RCAState,
  agent: string,
  findingKind: FindingKind = "specialist_observation",
): AgentFinding | undefined {
  const task = state.task_plan?.tasks.find(
    (item) => item.agent === agent && item.finding_kind === findingKind,
  );
  if (task) {
    const byTask = state.findings.find((finding) => finding.task_id === task.task_id);
    if (byTask) return byTask;
  }

  return (
    state.findings.find(
      (finding) => finding.agent === agent && finding.finding_kind === findingKind,
    ) ?? state.findings.find((finding) => finding.agent === agent)
  );
}

export function getYieldTrend(state: RCAState): YieldTrendPoint[] {
  const raw = findingFor(state, "mes")?.details.yield_trend;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is YieldTrendPoint => {
    if (!isRecord(item)) return false;
    return (
      typeof item.date === "string" &&
      typeof item.lot_count === "number" &&
      typeof item.pass_count === "number" &&
      typeof item.fail_count === "number" &&
      typeof item.pass_rate === "number"
    );
  });
}

export function getNormalLotCount(state: RCAState): number {
  const value = findingFor(state, "mes")?.details.normal_lots;
  return Array.isArray(value) ? value.length : 0;
}

export function getTargetChamber(state: RCAState): string {
  const value = findingFor(state, "mes")?.details.target_commonality;
  if (!isRecord(value)) return "Not available";
  return typeof value.chamber_id === "string" ? value.chamber_id : "Not available";
}

export function getHoldCount(state: RCAState): number {
  const value = findingFor(state, "mes")?.details.hold_count;
  return typeof value === "number" ? value : 0;
}

export function getEvidenceChain(state: RCAState): EvidenceChainItem[] {
  const raw = findingFor(state, "rca_reasoning", "hypothesis_ranking")?.details.evidence_chain;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is EvidenceChainItem => {
    if (!isRecord(item)) return false;
    return (
      typeof item.stage === "string" &&
      typeof item.claim === "string" &&
      typeof item.confidence === "number" &&
      Array.isArray(item.evidence_ids) &&
      item.evidence_ids.every((id) => typeof id === "string")
    );
  });
}

export function getRecommendedActions(state: RCAState): RecommendedAction[] {
  const raw = findingFor(state, "rca_reasoning", "hypothesis_ranking")?.details
    .recommended_actions;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is RecommendedAction => {
    if (!isRecord(item)) return false;
    return (
      typeof item.action === "string" &&
      Array.isArray(item.evidence_ids) &&
      item.evidence_ids.every((id) => typeof id === "string")
    );
  });
}

export function getFdcShifts(state: RCAState): Array<{
  parameter_name: string;
  avg_delta_percent: number;
}> {
  const raw = findingFor(state, "fdc")?.details.parameter_summary;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (
      !isRecord(item) ||
      typeof item.parameter_name !== "string" ||
      typeof item.avg_delta_percent !== "number"
    ) {
      return [];
    }
    return [
      {
        parameter_name: item.parameter_name,
        avg_delta_percent: item.avg_delta_percent,
      },
    ];
  });
}

export function formatAgentName(agent: string): string {
  const names: Record<string, string> = {
    mes: "MES",
    fdc: "FDC",
    defect_wat: "Defect / WAT",
    knowledge: "Knowledge",
    rca_reasoning: "RCA Reasoning",
    improvement: "Improvement",
  };
  return names[agent] ?? agent.replaceAll("_", " ");
}

export function formatTraceLabel(value: string): string {
  const acronyms: Record<string, string> = {
    cmp: "CMP",
    fdc: "FDC",
    id: "ID",
    ids: "IDs",
    llm: "LLM",
    mes: "MES",
    qwen: "Qwen",
    rca: "RCA",
    react: "ReAct",
    spc: "SPC",
    wat: "WAT",
  };
  return value
    .split("_")
    .map(
      (part) =>
        acronyms[part.toLowerCase()] ??
        `${part.charAt(0).toUpperCase()}${part.slice(1)}`,
    )
    .join(" ");
}

interface UniqueIndex<T> {
  values: Map<string, T>;
  duplicates: Set<string>;
}

function indexUnique<T>(
  items: T[],
  idFor: (item: T) => string,
  label: string,
  integrityIssues: string[],
): UniqueIndex<T> {
  const values = new Map<string, T>();
  const duplicates = new Set<string>();
  for (const item of items) {
    const id = idFor(item);
    if (values.has(id)) {
      duplicates.add(id);
      values.delete(id);
    } else if (!duplicates.has(id)) {
      values.set(id, item);
    }
  }
  for (const id of duplicates) {
    integrityIssues.push(`Duplicate ${label} ID: ${id}.`);
  }
  return { values, duplicates };
}

function resolveIds<T>(
  ids: string[],
  index: UniqueIndex<T>,
  label: string,
  integrityIssues: string[],
): T[] {
  return ids.flatMap((id) => {
    const item = index.values.get(id);
    if (item) return [item];
    integrityIssues.push(
      index.duplicates.has(id)
        ? `${label} ID ${id} is ambiguous.`
        : `${label} ID ${id} is missing.`,
    );
    return [];
  });
}

function orchestrationMode(value: unknown): OrchestrationMode | null {
  return value === "fixed" || value === "controlled_react" || value === "llm_react"
    ? value
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function stringArray(value: unknown): string[] | null {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    return null;
  }
  return value;
}

function integerValue(value: unknown, minimum = 0): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum
    ? value
    : null;
}

function isToolLatencyRecord(value: unknown): value is ToolLatencyRecord {
  if (!isRecord(value)) return false;
  return (
    typeof value.tool_name === "string" &&
    typeof value.tool_request_id === "string" &&
    typeof value.agent === "string" &&
    (value.outcome === "success" || value.outcome === "failed") &&
    typeof value.duration_ms === "number"
  );
}

function evaluationStatus(
  state: RCAState,
  requestedMode: OrchestrationMode | null,
  actualMode: OrchestrationMode | null,
  fallbackReason: string | null,
): AgentTraceEvaluationStatus {
  if (state.run_evaluation) return "available";
  const isFallback =
    requestedMode === "llm_react" &&
    (fallbackReason !== null ||
      (actualMode !== null && actualMode !== "llm_react"));
  if (isFallback) return "fallback";
  if (requestedMode === "llm_react" || actualMode === "llm_react") {
    return state.job.status === "running" || state.job.status === "pending"
      ? "pending"
      : "unavailable";
  }
  return "not_applicable";
}

interface SpecialistParseContext {
  state: RCAState;
  finding: AgentFinding;
  action: InvestigationAction;
  actionRecord: ActionRecord;
  evidenceIndex: UniqueIndex<Evidence>;
  toolLatencies: ToolLatencyRecord[];
}

function parseSpecialistToolStep(
  value: unknown,
  rawIndex: number,
  traceSupersededIds: Set<string>,
  context: SpecialistParseContext,
): SpecialistToolStepViewModel | null {
  const issues: string[] = [];
  if (!isRecord(value)) {
    issues.push(`Specialist Tool step ${rawIndex + 1} is not an object.`);
    return null;
  }
  const stepId = stringValue(value.step_id);
  const stepIndex = integerValue(value.step_index, 1);
  const actionId = stringValue(value.action_id);
  const agent = stringValue(value.agent);
  const specialistDecisionId = stringValue(value.decision_id);
  const candidateId = stringValue(value.candidate_id);
  const toolName = stringValue(value.tool_name);
  const parameters = isRecord(value.parameters) ? value.parameters : null;
  const reason = stringValue(value.reason);
  const evidenceIds = stringArray(value.evidence_ids);
  const outputSummary = stringValue(value.output_summary);
  const status =
    value.status === "completed" || value.status === "failed"
      ? value.status
      : null;
  if (
    stepId === null ||
    stepIndex === null ||
    actionId === null ||
    agent === null ||
    specialistDecisionId === null ||
    candidateId === null ||
    toolName === null ||
    parameters === null ||
    reason === null ||
    evidenceIds === null ||
    outputSummary === null ||
    status === null
  ) {
    return null;
  }
  if (stepIndex > 2) {
    issues.push(`Specialist Tool step ${stepId} exceeds the two-step boundary.`);
  }
  if (actionId !== context.action.action_id) {
    issues.push(`Specialist Tool step ${stepId} references a different action.`);
  }
  if (agent !== context.action.agent || agent !== context.finding.agent) {
    issues.push(`Specialist Tool step ${stepId} references a different Agent.`);
  }

  const superseded = traceSupersededIds.has(stepId);
  const evidence = resolveIds(
    evidenceIds,
    context.evidenceIndex,
    "Specialist Tool Evidence",
    issues,
  );
  if (superseded) {
    for (let index = issues.length - 1; index >= 0; index -= 1) {
      if (issues[index]?.startsWith("Specialist Tool Evidence ID")) {
        issues.splice(index, 1);
      }
    }
  } else {
    const actionEvidenceIds = new Set(context.actionRecord.produced_evidence_ids);
    for (const evidenceId of evidenceIds) {
      if (!actionEvidenceIds.has(evidenceId)) {
        issues.push(
          `Effective Specialist Tool Evidence ID ${evidenceId} is not in its ActionRecord.`,
        );
      }
    }
  }

  const toolRequestId =
    `${context.state.job.job_id}:${context.action.action_id}` +
    `:specialist-step-${stepIndex}`;
  const requestMatches = context.toolLatencies.filter(
    (latency) => latency.tool_request_id === toolRequestId,
  );
  const exactLatencyMatches = requestMatches.filter(
    (latency) => latency.tool_name === toolName && latency.agent === agent,
  );
  let latency: ToolLatencyRecord | null = null;
  if (exactLatencyMatches.length === 1) {
    [latency] = exactLatencyMatches;
  } else if (exactLatencyMatches.length > 1) {
    issues.push(`Tool latency ID ${toolRequestId} is duplicated.`);
  } else if (requestMatches.length > 0) {
    issues.push(`Tool latency ID ${toolRequestId} has a Tool or Agent mismatch.`);
  }

  return {
    key: `${context.finding.finding_id}:${stepId}:${rawIndex}`,
    stepId,
    stepIndex,
    actionId,
    specialistDecisionId,
    candidateId,
    toolName,
    parameters,
    reason,
    evidenceIds,
    evidence,
    outputSummary,
    status,
    superseded,
    toolRequestId,
    latency,
    integrityIssues: issues,
  };
}

function parseSpecialistTrace(
  context: SpecialistParseContext,
): SpecialistTraceViewModel | null {
  const raw = context.finding.details.specialist_v2;
  if (raw === undefined || raw === null) return null;
  const issues: string[] = [];
  if (!isRecord(raw)) {
    return {
      findingId: context.finding.finding_id,
      version: null,
      actionId: context.action.action_id,
      agent: context.action.agent,
      toolCallCount: 0,
      stopReason: null,
      analysisSource: null,
      fallbackReason: null,
      validationRetryCount: 0,
      localFallback: false,
      engineeringInterpretation: null,
      supersededStepIds: [],
      toolSteps: [],
      integrityIssues: ["Finding specialist_v2 details are not an object."],
    };
  }

  const actionId = stringValue(raw.action_id) ?? context.action.action_id;
  const agent = stringValue(raw.agent) ?? context.action.agent;
  if (typeof raw.action_id !== "string") {
    issues.push("Specialist trace action_id is missing or invalid.");
  } else if (actionId !== context.action.action_id) {
    issues.push("Specialist trace references a different action.");
  }
  if (typeof raw.agent !== "string") {
    issues.push("Specialist trace Agent is missing or invalid.");
  } else if (agent !== context.action.agent || agent !== context.finding.agent) {
    issues.push("Specialist trace references a different Agent.");
  }

  const version = stringValue(raw.version);
  if (version === null) issues.push("Specialist trace version is missing or invalid.");
  const stopReason = raw.stop_reason === null ? null : stringValue(raw.stop_reason);
  if (raw.stop_reason !== null && stopReason === null) {
    issues.push("Specialist trace stop reason is invalid.");
  }
  const fallbackReason =
    raw.fallback_reason === null ? null : stringValue(raw.fallback_reason);
  if (raw.fallback_reason !== null && fallbackReason === null) {
    issues.push("Specialist trace fallback reason is invalid.");
  }
  const analysisSource =
    raw.analysis_source === "qwen" ||
    raw.analysis_source === "deterministic_fallback"
      ? raw.analysis_source
      : null;
  if (analysisSource === null) {
    issues.push("Specialist trace analysis source is invalid.");
  }
  const validationRetryCount = integerValue(raw.validation_retry_count) ?? 0;
  if (integerValue(raw.validation_retry_count) === null) {
    issues.push("Specialist trace validation retry count is invalid.");
  }
  const supersededStepIds = stringArray(raw.superseded_step_ids) ?? [];
  if (stringArray(raw.superseded_step_ids) === null) {
    issues.push("Specialist trace superseded step IDs are invalid.");
  }
  const rawSteps = Array.isArray(raw.tool_steps) ? raw.tool_steps : [];
  if (!Array.isArray(raw.tool_steps)) {
    issues.push("Specialist trace Tool steps are not an array.");
  }
  const supersededSet = new Set(supersededStepIds);
  const toolSteps = rawSteps.flatMap((step, index) => {
    const parsed = parseSpecialistToolStep(
      step,
      index,
      supersededSet,
      context,
    );
    if (parsed) return [parsed];
    issues.push(`Specialist Tool step ${index + 1} is malformed and was omitted.`);
    return [];
  });
  toolSteps.sort((left, right) => left.stepIndex - right.stepIndex);

  const stepIds = toolSteps.map((step) => step.stepId);
  const stepIndices = toolSteps.map((step) => step.stepIndex);
  for (const duplicate of stepIds.filter(
    (stepId, index) => stepIds.indexOf(stepId) !== index,
  )) {
    issues.push(`Specialist Tool step ID ${duplicate} is duplicated.`);
  }
  for (const duplicate of stepIndices.filter(
    (stepIndex, index) => stepIndices.indexOf(stepIndex) !== index,
  )) {
    issues.push(`Specialist Tool step index ${duplicate} is duplicated.`);
  }
  for (const supersededStepId of supersededStepIds) {
    if (!stepIds.includes(supersededStepId)) {
      issues.push(
        `Superseded Specialist Tool step ${supersededStepId} is missing.`,
      );
    }
  }

  const toolCallCount = integerValue(raw.tool_call_count) ?? toolSteps.length;
  if (integerValue(raw.tool_call_count) === null) {
    issues.push("Specialist trace Tool-call count is invalid.");
  } else if (toolCallCount !== rawSteps.length) {
    issues.push("Specialist trace Tool-call count does not match its Tool steps.");
  }
  const engineeringInterpretation = stringValue(
    context.finding.details.engineering_interpretation,
  );

  return {
    findingId: context.finding.finding_id,
    version,
    actionId,
    agent,
    toolCallCount,
    stopReason,
    analysisSource,
    fallbackReason,
    validationRetryCount,
    localFallback:
      fallbackReason !== null ||
      analysisSource === "deterministic_fallback" ||
      supersededStepIds.length > 0,
    engineeringInterpretation,
    supersededStepIds,
    toolSteps,
    integrityIssues: issues,
  };
}

export function selectAgentTrace(state: RCAState): AgentTraceViewModel {
  const integrityIssues: string[] = [];
  const questions = state.investigation_questions ?? [];
  const capabilityNotices: CapabilityNotice[] = state.capability_notices ?? [];
  const questionEvidenceLinks: QuestionEvidenceLink[] =
    state.question_evidence_links ?? [];
  const decisions = state.planner_decisions ?? [];
  const questionUpdateReviews = state.question_update_reviews ?? [];
  const records = state.action_history ?? [];
  const runEvaluation = state.run_evaluation ?? null;
  const questionIndex = indexUnique(
    questions,
    (question) => question.question_id,
    "Question",
    integrityIssues,
  );
  const decisionIdIndex = indexUnique(
    decisions,
    (decision) => decision.decision_id,
    "PlannerDecision",
    integrityIssues,
  );
  const plannerActionIndex = indexUnique(
    decisions.flatMap((decision) =>
      decision.next_action === null ? [] : [decision.next_action],
    ),
    (action) => action.action_id,
    "Planner Action",
    integrityIssues,
  );
  const recordIndex = indexUnique(
    records,
    (record) => record.action.action_id,
    "ActionRecord",
    integrityIssues,
  );
  const findingIndex = indexUnique(
    state.findings,
    (finding) => finding.finding_id,
    "Finding",
    integrityIssues,
  );
  const evidenceIndex = indexUnique(
    state.evidence,
    (evidence) => evidence.evidence_id,
    "Evidence",
    integrityIssues,
  );
  const evaluations = runEvaluation?.decision_evaluations ?? [];
  const evaluationIndex = indexUnique(
    evaluations,
    (evaluation) => evaluation.decision_id,
    "DecisionEvaluation",
    integrityIssues,
  );
  const questionUpdateReviewsByDecision = new Map<
    string,
    typeof questionUpdateReviews
  >();
  for (const review of questionUpdateReviews) {
    if (!decisionIdIndex.values.has(review.decision_id)) {
      integrityIssues.push(
        `QuestionUpdate review references missing PlannerDecision ${review.decision_id}.`,
      );
      continue;
    }
    const current = questionUpdateReviewsByDecision.get(review.decision_id) ?? [];
    current.push(review);
    questionUpdateReviewsByDecision.set(review.decision_id, current);
  }

  const actualMode = orchestrationMode(
    state.execution_metadata.orchestration_mode,
  );
  const requestedMode =
    orchestrationMode(state.execution_metadata.orchestration_requested_mode) ??
    actualMode;
  const fallbackReason =
    stringValue(state.execution_metadata.orchestration_fallback_reason);
  const fallbackStage =
    state.execution_metadata.orchestration_fallback_stage === "intent_planning" ||
    state.execution_metadata.orchestration_fallback_stage ===
      "next_action_planning"
      ? state.execution_metadata.orchestration_fallback_stage
      : null;
  const fallbackAfterActionCount = integerValue(
    state.execution_metadata.orchestration_fallback_after_action_count,
  );
  const fallbackAttemptCount = integerValue(
    state.execution_metadata.orchestration_fallback_attempt_count,
    1,
  );
  const rawFallbackValidationErrors =
    state.execution_metadata.orchestration_fallback_validation_errors;
  const fallbackValidationErrors = Array.isArray(rawFallbackValidationErrors)
    ? rawFallbackValidationErrors.filter(
        (error): error is string =>
          typeof error === "string" && error.trim().length > 0,
      )
    : [];
  if (
    rawFallbackValidationErrors !== undefined &&
    (!Array.isArray(rawFallbackValidationErrors) ||
      fallbackValidationErrors.length !== rawFallbackValidationErrors.length)
  ) {
    integrityIssues.push("Planner fallback validation diagnostics are malformed.");
  }
  if (
    fallbackAttemptCount !== null &&
    fallbackValidationErrors.length > 0 &&
    fallbackAttemptCount !== fallbackValidationErrors.length
  ) {
    integrityIssues.push(
      "Planner fallback attempt count does not match its validation diagnostics.",
    );
  }
  const isFallback =
    requestedMode === "llm_react" &&
    (fallbackReason !== null ||
      (actualMode !== null && actualMode !== "llm_react"));
  const status = evaluationStatus(
    state,
    requestedMode,
    actualMode,
    fallbackReason,
  );
  if (runEvaluation && state.investigation_goal) {
    if (runEvaluation.goal_id !== state.investigation_goal.goal_id) {
      integrityIssues.push("RunEvaluation references a different Goal.");
    }
  }
  if (runEvaluation && isFallback) {
    integrityIssues.push(
      "A fallback investigation unexpectedly contains a RunEvaluation.",
    );
  }

  const rawToolLatencies = state.execution_metadata.tool_latencies;
  const toolLatencies = Array.isArray(rawToolLatencies)
    ? rawToolLatencies.filter((item) => {
        const valid = isToolLatencyRecord(item);
        if (!valid) integrityIssues.push("A Tool latency record is malformed.");
        return valid;
      })
    : [];
  const matchedRecords = new Set<ActionRecord>();

  const buildNode = ({
    key,
    origin,
    task = null,
    decision = null,
    action = null,
    actionRecord = null,
  }: {
    key: string;
    origin: AgentTraceOrigin;
    task?: AgentTask | null;
    decision?: PlannerDecision | null;
    action?: InvestigationAction | null;
    actionRecord?: ActionRecord | null;
  }): AgentTraceNodeViewModel => {
    const nodeIssues: string[] = [];
    const duplicateDecisionId =
      decision !== null &&
      decisionIdIndex.duplicates.has(decision.decision_id);
    if (duplicateDecisionId && decision !== null) {
      nodeIssues.push(
        `PlannerDecision ID ${decision.decision_id} is ambiguous.`,
      );
    }
    const evaluation =
      decision === null || duplicateDecisionId
        ? null
        : evaluationIndex.values.get(decision.decision_id) ?? null;
    if (
      status === "available" &&
      decision !== null &&
      !duplicateDecisionId &&
      evaluation === null
    ) {
      nodeIssues.push(
        `DecisionEvaluation for ${decision.decision_id} is missing or ambiguous.`,
      );
    }
    const targetQuestions =
      decision === null
        ? []
        : resolveIds(
            decision.target_question_ids,
            questionIndex,
            "Target Question",
            nodeIssues,
          );
    const nodeQuestionUpdateReviews =
      decision === null
        ? []
        : (questionUpdateReviewsByDecision.get(decision.decision_id) ?? []);
    for (const review of nodeQuestionUpdateReviews) {
      if (
        review.disposition === "accepted" &&
        !decision?.question_updates.some(
          (update) =>
            update.question_id === review.question_id &&
            update.status === review.claimed_status,
        )
      ) {
        nodeIssues.push(
          "An accepted QuestionUpdate review has no matching committed update.",
        );
      }
    }

    let findings: AgentFinding[] = [];
    let evidence: Evidence[] = [];
    if (actionRecord !== null) {
      findings = resolveIds(
        actionRecord.produced_finding_ids,
        findingIndex,
        "Finding",
        nodeIssues,
      );
      evidence = resolveIds(
        actionRecord.produced_evidence_ids,
        evidenceIndex,
        "Action Evidence",
        nodeIssues,
      );
      if (
        action !== null &&
        (actionRecord.action.kind !== action.kind ||
          actionRecord.action.agent !== action.agent)
      ) {
        nodeIssues.push("Planner Action and ActionRecord do not agree.");
      }
    } else if (task !== null) {
      findings = state.findings.filter(
        (finding) => finding.task_id === task.task_id,
      );
      evidence = resolveIds(
        [...new Set(findings.flatMap((finding) => finding.evidence_ids))],
        evidenceIndex,
        "Task Evidence",
        nodeIssues,
      );
    }

    const newEvidence =
      evaluation === null
        ? []
        : resolveIds(
            evaluation.new_evidence_ids,
            evidenceIndex,
            "New Evidence",
            nodeIssues,
          );
    const specialistTraces =
      action === null || actionRecord === null
        ? []
        : findings.flatMap((finding) => {
            const trace = parseSpecialistTrace({
              state,
              finding,
              action,
              actionRecord,
              evidenceIndex,
              toolLatencies,
            });
            return trace ? [trace] : [];
          });
    for (const trace of specialistTraces) {
      nodeIssues.push(
        ...trace.integrityIssues.map(
          (issue) => `Specialist trace ${trace.findingId}: ${issue}`,
        ),
      );
      for (const step of trace.toolSteps) {
        nodeIssues.push(
          ...step.integrityIssues.map(
            (issue) => `Specialist step ${step.stepId}: ${issue}`,
          ),
        );
      }
    }

    return {
      key,
      origin,
      task,
      decision,
      evaluation,
      targetQuestions,
      newQuestions: decision?.new_questions ?? [],
      questionUpdates: decision?.question_updates ?? [],
      questionUpdateReviews: nodeQuestionUpdateReviews,
      action,
      actionRecord,
      findings,
      evidence,
      newEvidence,
      specialistTraces,
      integrityIssues: nodeIssues,
    };
  };

  const nodes: AgentTraceNodeViewModel[] = [];
  const plannerOrigin: AgentTraceOrigin =
    requestedMode === "llm_react" || actualMode === "llm_react"
      ? "llm_react"
      : "legacy";
  for (const [decisionPosition, decision] of decisions.entries()) {
    const action = decision.next_action;
    const duplicatePlannerAction =
      action !== null && plannerActionIndex.duplicates.has(action.action_id);
    let record: ActionRecord | null = null;
    if (action !== null && !duplicatePlannerAction) {
      record = recordIndex.values.get(action.action_id) ?? null;
      if (record !== null) matchedRecords.add(record);
    }
    const node = buildNode({
      key: decisionIdIndex.duplicates.has(decision.decision_id)
        ? `decision:${decision.decision_id}:${decisionPosition}`
        : `decision:${decision.decision_id}`,
      origin: plannerOrigin,
      decision,
      action,
      actionRecord: record,
    });
    if (duplicatePlannerAction && action !== null) {
      node.integrityIssues.push(
        `Planner Action ID ${action.action_id} is ambiguous.`,
      );
    } else if (action !== null && record === null) {
      node.integrityIssues.push(
        `ActionRecord for ${action.action_id} is missing or ambiguous.`,
      );
    }
    nodes.push(node);
  }

  const unmatchedRecords = records.filter((record) => !matchedRecords.has(record));
  for (const [recordPosition, record] of unmatchedRecords.entries()) {
    let origin: AgentTraceOrigin;
    if (isFallback) {
      origin = "controlled_fallback";
    } else if (decisions.length === 0 && actualMode === "controlled_react") {
      origin = "controlled_react";
    } else {
      origin = "legacy";
    }
    const node = buildNode({
      key: recordIndex.duplicates.has(record.action.action_id)
        ? `action:${record.action.action_id}:${recordPosition}`
        : `action:${record.action.action_id}`,
      origin,
      action: record.action,
      actionRecord: record,
    });
    if (recordIndex.duplicates.has(record.action.action_id)) {
      node.integrityIssues.push(
        `ActionRecord ID ${record.action.action_id} is ambiguous.`,
      );
    }
    if (decisions.length > 0 && !isFallback) {
      node.integrityIssues.push(
        "ActionRecord is not linked to a committed PlannerDecision.",
      );
    }
    nodes.push(node);
  }

  if (decisions.length === 0 && records.length === 0 && state.task_plan) {
    for (const task of state.task_plan.tasks) {
      nodes.push(
        buildNode({
          key: `task:${task.task_id}`,
          origin: actualMode === null ? "legacy" : "fixed",
          task,
        }),
      );
    }
  }

  if (status === "available") {
    const decisionIds = new Set(decisions.map((decision) => decision.decision_id));
    for (const evaluation of evaluations) {
      if (!decisionIds.has(evaluation.decision_id)) {
        integrityIssues.push(
          `DecisionEvaluation ${evaluation.decision_id} has no PlannerDecision.`,
        );
      }
    }
  }

  return {
    requestedMode,
    actualMode,
    evaluationStatus: status,
    runEvaluation,
    goal: state.investigation_goal ?? null,
    questions: [...questionIndex.values.values()],
    capabilityNotices,
    questionEvidenceLinks,
    nodes,
    fallbackReason,
    fallbackStage,
    fallbackAfterActionCount,
    fallbackAttemptCount,
    fallbackValidationErrors,
    integrityIssues,
  };
}
