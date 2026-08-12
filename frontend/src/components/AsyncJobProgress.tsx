import {
  Activity,
  Ban,
  CheckCircle2,
  CircleDashed,
  Database,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
} from "lucide-react";

import type {
  JobStreamConnection,
  RCAJobEvent,
  RCAJobQueueMetadata,
  TaskStatus,
} from "../types";
import { StatusBadge } from "./StatusBadge";

interface AsyncJobProgressProps {
  jobId: string;
  status: TaskStatus;
  queue: RCAJobQueueMetadata | null;
  events: RCAJobEvent[];
  connection: JobStreamConnection;
  cancelling: boolean;
  onCancel: () => void;
}

function eventLabel(event: RCAJobEvent): string {
  const payload = event.payload;
  switch (event.event_type) {
    case "job_queued":
      return "Request persisted in the PostgreSQL queue";
    case "job_started":
      return `Worker claimed attempt ${String(payload.attempt_number ?? "")}`.trim();
    case "investigation_planned":
      return `Investigation planned: ${String(payload.summary ?? payload.intent ?? "bounded plan")}`;
    case "planner_decision":
      return `Planner selected ${String(payload.action_kind ?? "the next action")}`;
    case "action_started":
      return `${String(payload.agent ?? "Agent")} started ${String(payload.action_kind ?? "action")}`;
    case "action_completed":
      return String(payload.summary ?? "Agent action completed");
    case "agent_started":
      return `${String(payload.agent ?? "Agent")} started: ${String(payload.objective ?? "analysis")}`;
    case "agent_completed":
      return String(payload.summary ?? `${String(payload.agent ?? "Agent")} completed`);
    case "planner_stopped":
    case "investigation_stopped":
      return `Investigation stopped: ${String(payload.stop_reason ?? "bounded goal reached")}`;
    case "job_retry_scheduled":
      return `Transient failure; retry ${String(payload.attempt_number ?? "")} scheduled`;
    case "job_lease_recovered":
      return "Expired Worker lease recovered; Job returned to the queue";
    case "job_cancel_requested":
      return "Cancellation requested; active result will be discarded";
    case "job_cancelled":
      return "Job cancelled";
    case "job_completed":
      return "RCA result committed";
    case "job_failed":
      return String(
        (payload.error as { message?: string } | undefined)?.message ?? "RCA Job failed",
      );
    default:
      return event.event_type.replaceAll("_", " ");
  }
}

function EventIcon({ type }: { type: string }) {
  if (type.endsWith("completed")) return <CheckCircle2 size={16} />;
  if (type.includes("retry") || type.includes("recovered")) return <RotateCcw size={16} />;
  if (type.includes("cancel")) return <Ban size={16} />;
  if (type === "job_queued") return <Database size={16} />;
  if (type.endsWith("started") || type === "planner_decision") {
    return <LoaderCircle size={16} />;
  }
  return <CircleDashed size={16} />;
}

export function AsyncJobProgress({
  jobId,
  status,
  queue,
  events,
  connection,
  cancelling,
  onCancel,
}: AsyncJobProgressProps) {
  const canCancel = ["queued", "running", "retry_wait", "cancel_requested"].includes(status);
  return (
    <section className="async-job-panel" aria-labelledby="async-job-heading" aria-live="polite">
      <div className="async-job-heading">
        <div className="loading-icon"><Activity size={22} aria-hidden="true" /></div>
        <div>
          <span className="section-kicker">PostgreSQL Queue + leased Worker</span>
          <h2 id="async-job-heading">Asynchronous RCA progress</h2>
          <code>{jobId}</code>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="async-job-facts">
        <div><span>Stream</span><strong className={`stream-${connection}`}><RefreshCw size={13} /> {connection}</strong></div>
        <div><span>Attempt</span><strong>{queue?.attempt_count ?? 0} / {queue?.max_attempts ?? 3}</strong></div>
        <div><span>Evidence trace</span><strong>{events.filter((event) => event.event_type.endsWith("completed")).length} observations</strong></div>
      </div>

      <ol className="async-event-list">
        {events.map((event) => (
          <li key={event.sequence} className={`async-event event-${event.event_type}`}>
            <div className="async-event-icon"><EventIcon type={event.event_type} /></div>
            <div>
              <strong>{eventLabel(event)}</strong>
              <small>
                #{event.sequence} · {new Date(event.created_at).toLocaleTimeString()}
                {Array.isArray(event.payload.evidence_ids) && event.payload.evidence_ids.length > 0
                  ? ` · ${event.payload.evidence_ids.length} Evidence`
                  : ""}
              </small>
            </div>
          </li>
        ))}
      </ol>

      {canCancel && (
        <button
          type="button"
          className="cancel-job-button"
          onClick={onCancel}
          disabled={cancelling || status === "cancel_requested"}
        >
          <Ban size={15} aria-hidden="true" />
          {cancelling || status === "cancel_requested" ? "Cancellation requested" : "Cancel investigation"}
        </button>
      )}
    </section>
  );
}
