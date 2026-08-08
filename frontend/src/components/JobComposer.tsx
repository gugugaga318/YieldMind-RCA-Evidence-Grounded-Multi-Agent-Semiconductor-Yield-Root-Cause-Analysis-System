import { BookOpen, CalendarRange, Play, Search, ServerCog, Waypoints } from "lucide-react";
import type { FormEvent } from "react";

import type {
  KnowledgeQuestionKind,
  RCAJobCreated,
  RuntimeInfo,
  WorkspaceMode,
} from "../types";
import { StatusBadge } from "./StatusBadge";

interface JobComposerProps {
  workspaceMode: WorkspaceMode;
  onWorkspaceModeChange: (mode: WorkspaceMode) => void;
  query: string;
  onQueryChange: (query: string) => void;
  lotId: string;
  onLotIdChange: (lotId: string) => void;
  knowledgeQuestionKind: KnowledgeQuestionKind;
  onKnowledgeQuestionKindChange: (kind: KnowledgeQuestionKind) => void;
  knowledgeModule: string;
  onKnowledgeModuleChange: (module: string) => void;
  onSubmit: () => void;
  loading: boolean;
  currentJob: RCAJobCreated | null;
  runtimeInfo: RuntimeInfo | null;
}

export function JobComposer({
  workspaceMode,
  onWorkspaceModeChange,
  query,
  onQueryChange,
  lotId,
  onLotIdChange,
  knowledgeQuestionKind,
  onKnowledgeQuestionKindChange,
  knowledgeModule,
  onKnowledgeModuleChange,
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
            aria-selected={workspaceMode === "product_window"}
            className={workspaceMode === "product_window" ? "active" : ""}
            onClick={() => onWorkspaceModeChange("product_window")}
            disabled={loading}
          >
            <CalendarRange size={15} aria-hidden="true" />
            Product
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={workspaceMode === "lot"}
            className={workspaceMode === "lot" ? "active" : ""}
            onClick={() => onWorkspaceModeChange("lot")}
            disabled={loading}
          >
            <Waypoints size={15} aria-hidden="true" />
            Lot ID
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={workspaceMode === "knowledge"}
            className={workspaceMode === "knowledge" ? "active" : ""}
            onClick={() => onWorkspaceModeChange("knowledge")}
            disabled={loading}
          >
            <BookOpen size={15} aria-hidden="true" />
            Knowledge
          </button>
        </div>

        {workspaceMode === "knowledge" ? (
          <>
            <label htmlFor="knowledge-kind">Reference type</label>
            <select
              id="knowledge-kind"
              value={knowledgeQuestionKind}
              onChange={(event) =>
                onKnowledgeQuestionKindChange(
                  event.target.value as KnowledgeQuestionKind,
                )
              }
              disabled={loading}
            >
              <option value="historical_match">Historical RCA Case</option>
              <option value="procedure_guidance">SOP guidance</option>
              <option value="engineering_note_lookup">Engineering Note</option>
            </select>
            <label htmlFor="knowledge-module">Module filter</label>
            <input
              id="knowledge-module"
              value={knowledgeModule}
              onChange={(event) => onKnowledgeModuleChange(event.target.value)}
              maxLength={200}
              placeholder="e.g. Cu CMP"
              disabled={loading}
            />
            <label htmlFor="knowledge-query">Knowledge question</label>
            <textarea
              id="knowledge-query"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              rows={5}
              maxLength={4000}
              placeholder="Search an approved case, SOP, or Engineering Note."
              disabled={loading}
            />
            <small className="field-help">
              This path runs only the Knowledge Agent and never produces an RCA conclusion.
            </small>
          </>
        ) : workspaceMode === "lot" ? (
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
            (workspaceMode === "lot"
              ? lotId.trim().length === 0 || query.trim().length === 0
              : query.trim().length === 0)
          }
        >
          <Play size={16} fill="currentColor" aria-hidden="true" />
          {loading
            ? workspaceMode === "knowledge"
              ? "Searching references"
              : "Running analysis"
            : workspaceMode === "knowledge"
              ? "Search knowledge"
              : "Run RCA"}
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
