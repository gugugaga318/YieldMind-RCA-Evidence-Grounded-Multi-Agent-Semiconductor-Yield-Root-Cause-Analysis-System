import { CalendarRange, Play, Search, ServerCog, Waypoints } from "lucide-react";
import type { FormEvent } from "react";

import type { InvestigationMode, RCAJobCreated, RuntimeInfo } from "../types";
import { StatusBadge } from "./StatusBadge";

interface JobComposerProps {
  investigationMode: InvestigationMode;
  onInvestigationModeChange: (mode: InvestigationMode) => void;
  query: string;
  onQueryChange: (query: string) => void;
  lotId: string;
  onLotIdChange: (lotId: string) => void;
  onSubmit: () => void;
  loading: boolean;
  currentJob: RCAJobCreated | null;
  runtimeInfo: RuntimeInfo | null;
}

export function JobComposer({
  investigationMode,
  onInvestigationModeChange,
  query,
  onQueryChange,
  lotId,
  onLotIdChange,
  onSubmit,
  loading,
  currentJob,
  runtimeInfo,
}: JobComposerProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <aside className="sidebar" aria-label="RCA investigation controls">
      <div className="sidebar-heading">
        <Search size={18} aria-hidden="true" />
        <h2>New investigation</h2>
      </div>

      <form onSubmit={handleSubmit} className="job-form">
        <span className="field-label">Investigation mode</span>
        <div className="mode-control" role="tablist" aria-label="Investigation mode">
          <button
            type="button"
            role="tab"
            aria-selected={investigationMode === "product_window"}
            className={investigationMode === "product_window" ? "active" : ""}
            onClick={() => onInvestigationModeChange("product_window")}
            disabled={loading}
          >
            <CalendarRange size={15} aria-hidden="true" />
            Product
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={investigationMode === "lot"}
            className={investigationMode === "lot" ? "active" : ""}
            onClick={() => onInvestigationModeChange("lot")}
            disabled={loading}
          >
            <Waypoints size={15} aria-hidden="true" />
            Lot ID
          </button>
        </div>

        {investigationMode === "lot" ? (
          <>
            <label htmlFor="rca-lot-id">Abnormal Lot ID</label>
            <input
              id="rca-lot-id"
              value={lotId}
              onChange={(event) => onLotIdChange(event.target.value.toUpperCase())}
              maxLength={100}
              autoComplete="off"
              disabled={loading}
            />
            <label htmlFor="rca-query">Investigation request</label>
            <textarea
              id="rca-query"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              rows={5}
              maxLength={2000}
              placeholder="Describe the known defect, process module, requested root cause, or impact scope."
              disabled={loading}
            />
            <small className="field-help">
              Include known clues such as Scratch, Cu CMP, SPC, equipment, or impact Lots.
            </small>
          </>
        ) : (
          <>
            <label htmlFor="rca-query">Investigation request</label>
            <textarea
              id="rca-query"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              rows={5}
              maxLength={2000}
              disabled={loading}
            />
          </>
        )}
        <button
          type="submit"
          className="primary-button"
          disabled={
            loading ||
            (investigationMode === "lot"
              ? lotId.trim().length === 0 || query.trim().length === 0
              : query.trim().length === 0)
          }
        >
          <Play size={16} fill="currentColor" aria-hidden="true" />
          {loading ? "Running analysis" : "Run RCA"}
        </button>
      </form>

      <div className="source-block">
        <div className="source-row">
          <ServerCog size={17} aria-hidden="true" />
          <div>
            <span className="field-label">Runtime data</span>
            <strong>{runtimeInfo?.dataset ?? "Resolving dataset"}</strong>
            <small>
              {runtimeInfo
                ? `${runtimeInfo.agent_mode} / ${runtimeInfo.model} · ${runtimeInfo.orchestration_mode}`
                : "API runtime"}
            </small>
          </div>
          <span className="live-dot" title="API connection active" />
        </div>
      </div>

      {currentJob && (
        <div className="current-job-block">
          <span className="field-label">Current job</span>
          <code>{currentJob.job_id}</code>
          <div className="current-job-meta">
            <StatusBadge status={currentJob.status} />
            <time dateTime={currentJob.created_at}>
              {new Date(currentJob.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
          </div>
        </div>
      )}
    </aside>
  );
}
