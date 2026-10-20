import type { TriageDimension, TriageResult, TriageScope, TriageUncertaintyKind } from "./types";

export const comparisonLabels: Record<TriageResult["comparison"]["preference_status"], string> = {
  preference_established: "Evidence preference established; causal confirmation is separate.",
  preference_not_established: "Preference not established.",
  tied: "Tied — not distinguishable with current evidence.",
  not_distinguishable: "Not distinguishable with current evidence.",
};
export const uncertaintyLabels: Record<TriageUncertaintyKind, string> = {
  not_observed: "Not observed in the scoped observation",
  not_acquired: "Evidence not yet acquired",
  unavailable: "Evidence unavailable or assessment not recorded",
  conflicting: "Conflicting evidence",
  unresolved_alternative: "Unresolved alternative",
  unsupported_mechanism: "Mechanism not yet supported",
  insufficient_scope: "Insufficient scope support",
  insufficient_temporal_support: "Insufficient time support",
};
export const assessmentLabels: Record<TriageDimension, string> = {
  parameter_support: "Parameter", mechanism_support: "Mechanism", entity_consistency: "Entity",
  operation_consistency: "Operation", recipe_version_consistency: "Recipe / Version",
  scope_consistency: "Scope", temporal_consistency: "Time", contradiction: "Contradiction",
  causal_chain: "Causal Chain", alternative_status: "Alternative",
};
export function scopeText(scope: TriageScope): string {
  const fields = [ ["Entity", scope.entity], ["Operation", scope.operation], ["Equipment", scope.equipment],
    ["Chamber", scope.chamber], ["Recipe", scope.recipe], ["Version", scope.recipe_version] ];
  const parts = fields.filter(([,v]) => v !== null).map(([k,v]) => `${k}: ${v}`);
  parts.push(`Scope: ${scope.scope_kind}`);
  if (scope.lane_ids.length) parts.push(`Lanes: ${scope.lane_ids.join(", ")}`);
  parts.push(`Time: ${scope.time_window.join(" to ") || "Not declared"}`);
  return parts.join("; ");
}
export function readable(value: string): string { return value.replaceAll("_", " "); }
