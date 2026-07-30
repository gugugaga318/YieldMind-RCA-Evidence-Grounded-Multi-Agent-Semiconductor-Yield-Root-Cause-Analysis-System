import type {
  AgentFinding,
  CreateRCAJobRequest,
  EvidenceChainItem,
  FindingKind,
  InvestigationMode,
  RCAState,
  RecommendedAction,
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
