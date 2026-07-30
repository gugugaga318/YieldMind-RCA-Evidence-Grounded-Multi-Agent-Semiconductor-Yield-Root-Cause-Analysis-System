import {
  BrainCircuit,
  CheckCircle2,
  CircleDashed,
  Database,
  Gauge,
  Lightbulb,
  Microscope,
  SearchCheck,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { formatAgentName } from "../selectors";
import type { AgentTask, RCAState } from "../types";

const AGENT_ICONS: Record<string, LucideIcon> = {
  mes: Database,
  fdc: Gauge,
  defect_wat: Microscope,
  knowledge: SearchCheck,
  rca_reasoning: BrainCircuit,
  improvement: Lightbulb,
};

interface WorkflowTimelineProps {
  tasks: AgentTask[];
  state?: RCAState;
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatActionKind(kind: string): string {
  return kind
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function ControlledTimeline({ state }: { state: RCAState }) {
  const actions = state.action_history ?? [];
  const goal = state.investigation_goal;

  return (
    <section className="workflow-section controlled-workflow" aria-labelledby="workflow-heading">
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Observe · act · re-plan</span>
          <h2 id="workflow-heading">Controlled ReAct Investigation Path</h2>
        </div>
        <span className="section-count">{actions.length} bounded actions</span>
      </div>

      {goal && (
        <div className="investigation-goal">
          <div>
            <span>Goal intent</span>
            <strong>{formatActionKind(goal.intent)}</strong>
          </div>
          <div className="goal-summary">
            <span>Investigation objective</span>
            <strong>{goal.summary}</strong>
          </div>
          <div>
            <span>Safety budget</span>
            <strong>
              {goal.max_steps} steps / {goal.max_tool_calls} tool calls
            </strong>
          </div>
          {Object.keys(goal.known_facts).length > 0 && (
            <div className="goal-facts">
              <span>Known facts</span>
              <div className="trace-chip-list">
                {Object.entries(goal.known_facts).map(([key, value]) => (
                  <code key={key}>
                    {key}: {formatValue(value)}
                  </code>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <ol className="controlled-action-list">
        {actions.map((record, index) => {
          const AgentIcon = AGENT_ICONS[record.action.agent] ?? CircleDashed;
          return (
            <li className={`controlled-action action-${record.status}`} key={record.action.action_id}>
              <div className="controlled-action-index">{index + 1}</div>
              <div className="controlled-action-card">
                <div className="controlled-action-heading">
                  <div>
                    <span className="action-agent">
                      <AgentIcon size={15} aria-hidden="true" />
                      {formatAgentName(record.action.agent)}
                    </span>
                    <strong>{formatActionKind(record.action.kind)}</strong>
                  </div>
                  <span className={`action-status action-status-${record.status}`}>
                    {record.status}
                  </span>
                </div>

                <div className="trace-block">
                  <span>Why this action</span>
                  <p>{record.action.reason}</p>
                </div>

                <div className="trace-block">
                  <span>Action input</span>
                  <div className="trace-chip-list">
                    {Object.entries(record.action.inputs).map(([key, value]) => (
                      <code key={key}>
                        {key}: {formatValue(value)}
                      </code>
                    ))}
                  </div>
                </div>

                <div className="trace-block">
                  <span>Observed result</span>
                  <p>{record.decision_summary}</p>
                </div>

                <div className="trace-block">
                  <span>Produced evidence</span>
                  <div className="trace-chip-list">
                    {record.produced_evidence_ids.length > 0 ? (
                      record.produced_evidence_ids.map((evidenceId) => (
                        <code key={evidenceId}>{evidenceId}</code>
                      ))
                    ) : (
                      <em>No new evidence</em>
                    )}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      <div className="controlled-stop-summary">
        <div>
          <span>Goal status</span>
          <strong>{state.goal_status ?? "not available"}</strong>
        </div>
        <div>
          <span>Conclusion level</span>
          <strong>{state.conclusion_level ?? "not available"}</strong>
        </div>
        <div>
          <span>Stop reason</span>
          <strong>{state.stop_reason ?? "not available"}</strong>
        </div>
        <div>
          <span>Remaining evidence gaps</span>
          <strong>
            {state.evidence_gaps && state.evidence_gaps.length > 0
              ? state.evidence_gaps.join(", ")
              : "None"}
          </strong>
        </div>
      </div>
    </section>
  );
}

export function WorkflowTimeline({ tasks, state }: WorkflowTimelineProps) {
  if (state && (state.action_history?.length ?? 0) > 0) {
    return <ControlledTimeline state={state} />;
  }

  return (
    <section className="workflow-section" aria-labelledby="workflow-heading">
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Execution trace</span>
          <h2 id="workflow-heading">Agent Workflow</h2>
        </div>
        <span className="section-count">
          {tasks.filter((task) => task.status === "completed").length}/{tasks.length} complete
        </span>
      </div>

      <ol className="workflow-timeline">
        {tasks.map((task, index) => {
          const AgentIcon = AGENT_ICONS[task.agent] ?? CircleDashed;
          const StateIcon = task.status === "completed" ? CheckCircle2 : CircleDashed;
          return (
            <li className={`workflow-step workflow-${task.status}`} key={task.task_id}>
              <div className="workflow-node">
                <AgentIcon size={18} aria-hidden="true" />
              </div>
              <div className="workflow-copy">
                <div className="workflow-title">
                  <span>{formatAgentName(task.agent)}</span>
                  <StateIcon size={15} aria-hidden="true" />
                </div>
                <p>{task.objective}</p>
              </div>
              {index < tasks.length - 1 && <span className="workflow-connector" />}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
