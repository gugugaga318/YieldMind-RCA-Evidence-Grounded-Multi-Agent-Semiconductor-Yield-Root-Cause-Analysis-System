import { Bot, Braces, Clock3, Cpu, Gauge, GitBranch, ShieldCheck, Wrench } from "lucide-react";

import type { RCAState } from "../types";

export function RuntimeMetadata({ state }: { state: RCAState }) {
  const metadata = state.execution_metadata;
  const requestedMode = metadata.orchestration_requested_mode ?? metadata.orchestration_mode;
  const actualMode = metadata.orchestration_mode ?? "fixed";
  const fallbackReason = metadata.orchestration_fallback_reason;
  const items = [
    {
      label: "Requested path",
      value: requestedMode ?? "fixed",
      icon: GitBranch,
    },
    {
      label: "Actual path",
      value: actualMode,
      icon: ShieldCheck,
    },
    {
      label: "Agent mode",
      value: metadata.agent_mode ?? "deterministic",
      icon: Bot,
    },
    {
      label: "Model",
      value: metadata.model ?? "No LLM",
      icon: Cpu,
    },
    {
      label: "Conclusion",
      value: state.conclusion_level ?? state.hypotheses.at(-1)?.status ?? "not available",
      icon: Braces,
    },
    {
      label: "Tokens",
      value: String(metadata.total_tokens ?? 0),
      icon: Gauge,
    },
    {
      label: "LLM latency",
      value: `${Number(metadata.llm_latency_ms ?? 0).toFixed(1)} ms`,
      icon: Clock3,
    },
    {
      label: "Tool calls",
      value: String(metadata.tool_call_count ?? 0),
      icon: Wrench,
    },
  ];

  return (
    <>
      <section className="runtime-strip" aria-label="Agent runtime metadata">
        {items.map(({ label, value, icon: Icon }) => (
          <div className="runtime-item" key={label}>
            <Icon size={15} aria-hidden="true" />
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </section>
      {fallbackReason && (
        <div className="orchestration-fallback" role="status">
          <GitBranch size={16} aria-hidden="true" />
          <div>
            <strong>Controlled ReAct request used the Fixed compatibility path</strong>
            <span>
              {fallbackReason === "controlled_react_requires_lot_investigation"
                ? "Controlled ReAct currently requires a Lot investigation."
                : fallbackReason === "controlled_react_requires_explicit_defect_clue"
                  ? "Add an explicit Scratch, defect, 划伤, or 刮伤 clue to enable the controlled path."
                  : fallbackReason}
            </span>
          </div>
        </div>
      )}
    </>
  );
}
