import {
  Activity,
  AlertTriangle,
  Boxes,
  FileText,
  FlaskConical,
  LayoutDashboard,
  PanelTop,
  Search,
  Server,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  createRCAJob,
  getMemoryCandidate,
  getRCAJob,
  getRCAReport,
  getRuntimeInfo,
  lookupKnowledge,
} from "./api";
import { EvidenceChain } from "./components/EvidenceChain";
import { JobComposer } from "./components/JobComposer";
import { LotImpactPanel } from "./components/LotImpactPanel";
import { KnowledgeWorkspace } from "./components/KnowledgeWorkspace";
import { MemoryApprovalPanel } from "./components/MemoryApprovalPanel";
import { ReportView } from "./components/ReportView";
import { RootCausePanel } from "./components/RootCausePanel";
import { SpcEvidencePanel } from "./components/SpcEvidencePanel";
import { RuntimeMetadata } from "./components/RuntimeMetadata";
import { StatusBadge } from "./components/StatusBadge";
import { WorkflowTimeline } from "./components/WorkflowTimeline";
import { YieldTrendChart } from "./components/YieldTrendChart";
import {
  buildRCAJobRequest,
  getDefaultLotId,
  getHoldCount,
  getNormalLotCount,
  getTargetChamber,
  getYieldTrend,
} from "./selectors";
import type {
  KnowledgeLookupResult,
  KnowledgeQuestionKind,
  MemoryCandidate,
  RCAJobCreated,
  RCAReport,
  RCAState,
  RuntimeInfo,
  WorkspaceMode,
} from "./types";

const DEFAULT_QUERY =
  "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31.";
const DEFAULT_LOT_QUERY =
  "Investigate the scratch found in Cu CMP and identify root cause and impact lots.";
const DEFAULT_KNOWLEDGE_QUERY =
  "Which approved Cu CMP cases describe radial scratch patterns and retaining-ring wear?";

type WorkspaceTab = "investigation" | "report";

function App() {
  const [productQuery, setProductQuery] = useState(DEFAULT_QUERY);
  const [lotQuery, setLotQuery] = useState(DEFAULT_LOT_QUERY);
  const [knowledgeQuery, setKnowledgeQuery] = useState(DEFAULT_KNOWLEDGE_QUERY);
  const [workspaceMode, setWorkspaceMode] =
    useState<WorkspaceMode>("product_window");
  const [knowledgeQuestionKind, setKnowledgeQuestionKind] =
    useState<KnowledgeQuestionKind>("historical_match");
  const [knowledgeModule, setKnowledgeModule] = useState("Cu CMP");
  const [knowledgeExplicitModuleLimit, setKnowledgeExplicitModuleLimit] =
    useState(false);
  const [lotId, setLotId] = useState("LOT_A_001");
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [job, setJob] = useState<RCAJobCreated | null>(null);
  const [state, setState] = useState<RCAState | null>(null);
  const [report, setReport] = useState<RCAReport | null>(null);
  const [memoryCandidate, setMemoryCandidate] = useState<MemoryCandidate | null>(null);
  const [knowledgeResult, setKnowledgeResult] =
    useState<KnowledgeLookupResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("investigation");
  const query =
    workspaceMode === "lot"
      ? lotQuery
      : workspaceMode === "knowledge"
        ? knowledgeQuery
        : productQuery;

  useEffect(() => {
    let active = true;
    getRuntimeInfo()
      .then((info) => {
        if (!active) return;
        setRuntimeInfo(info);
        setLotId(getDefaultLotId(info.dataset));
      })
      .catch(() => {
        if (active) setRuntimeInfo(null);
      });
    return () => {
      active = false;
    };
  }, []);

  async function runInvestigation() {
    setLoading(true);
    setError(null);
    setState(null);
    setReport(null);
    setMemoryCandidate(null);
    setActiveTab("investigation");

    try {
      if (workspaceMode === "knowledge") {
        const result = await lookupKnowledge({
          query: knowledgeQuery,
          question_kind: knowledgeQuestionKind,
          module: knowledgeModule,
          explicit_module_limit: knowledgeExplicitModuleLimit,
          top_k: 5,
        });
        setKnowledgeResult(result);
        return;
      }
      setKnowledgeResult(null);
      const created = await createRCAJob(
        buildRCAJobRequest(workspaceMode, query, lotId),
      );
      setJob(created);
      const [jobResponse, reportResponse] = await Promise.all([
        getRCAJob(created.job_id),
        getRCAReport(created.job_id),
      ]);
      setState(jobResponse.state);
      setReport(reportResponse.report);
      if (created.memory_candidate_id) {
        const memoryResponse = await getMemoryCandidate(created.memory_candidate_id);
        setMemoryCandidate(memoryResponse.candidate);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "RCA request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">
            <FlaskConical size={20} />
          </div>
          <div>
            <strong>Yield RCA</strong>
            <span>Engineering workspace</span>
          </div>
        </div>
        <div className="topbar-status">
          <span className="connection-state">
            <span className="live-dot" />
            API connected
          </span>
          <span className="environment-label">MVP / 40N</span>
        </div>
      </header>

      <div className="app-body">
        <JobComposer
          workspaceMode={workspaceMode}
          onWorkspaceModeChange={setWorkspaceMode}
          query={query}
          onQueryChange={
            workspaceMode === "lot"
              ? setLotQuery
              : workspaceMode === "knowledge"
                ? setKnowledgeQuery
                : setProductQuery
          }
          lotId={lotId}
          onLotIdChange={setLotId}
          knowledgeQuestionKind={knowledgeQuestionKind}
          onKnowledgeQuestionKindChange={setKnowledgeQuestionKind}
          knowledgeModule={knowledgeModule}
          onKnowledgeModuleChange={setKnowledgeModule}
          knowledgeExplicitModuleLimit={knowledgeExplicitModuleLimit}
          onKnowledgeExplicitModuleLimitChange={setKnowledgeExplicitModuleLimit}
          onSubmit={runInvestigation}
          loading={loading}
          currentJob={workspaceMode === "knowledge" ? null : job}
          runtimeInfo={runtimeInfo}
        />

        <main className="workspace">
          <div className="workspace-toolbar">
            <div>
              <span className="workspace-eyebrow">
                {workspaceMode === "knowledge"
                  ? "Independent knowledge lookup"
                  : state?.job.investigation_mode === "lot"
                  ? "Lot-driven investigation"
                  : "Yield excursion investigation"}
              </span>
              <h1>
                {workspaceMode === "knowledge"
                  ? "Approved Knowledge Assets"
                  : state?.job.source_lot_id ??
                    state?.job.product_id ??
                    "RCA Job Workspace"}
              </h1>
            </div>
            {workspaceMode !== "knowledge" && (
              <div className="tab-control" role="tablist" aria-label="RCA workspace views">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "investigation"}
                className={activeTab === "investigation" ? "active" : ""}
                onClick={() => setActiveTab("investigation")}
              >
                <LayoutDashboard size={16} aria-hidden="true" />
                Investigation
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "report"}
                className={activeTab === "report" ? "active" : ""}
                onClick={() => setActiveTab("report")}
                disabled={!report}
              >
                <FileText size={16} aria-hidden="true" />
                Report
              </button>
              </div>
            )}
          </div>

          {error && (
            <div className="error-banner" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {loading && <LoadingState knowledge={workspaceMode === "knowledge"} />}
          {!loading && workspaceMode === "knowledge" && !error && (
            <KnowledgeWorkspace result={knowledgeResult} />
          )}
          {!loading && workspaceMode !== "knowledge" && !state && !error && <EmptyState />}
          {!loading && workspaceMode !== "knowledge" && state && activeTab === "investigation" && (
            <InvestigationView
              state={state}
              memoryCandidate={memoryCandidate}
              onMemoryCandidateChange={setMemoryCandidate}
            />
          )}
          {!loading && report && activeTab === "report" && <ReportView report={report} />}
        </main>
      </div>
    </div>
  );
}

function InvestigationView({
  state,
  memoryCandidate,
  onMemoryCandidateChange,
}: {
  state: RCAState;
  memoryCandidate: MemoryCandidate | null;
  onMemoryCandidateChange: (candidate: MemoryCandidate) => void;
}) {
  const tasks = state.task_plan?.tasks ?? [];
  const yieldTrend = getYieldTrend(state);
  const isLotDriven = state.job.investigation_mode === "lot";
  const metrics = isLotDriven
    ? [
        {
          label: "Investigated Lot",
          value: state.job.source_lot_id ?? "Not available",
          detail: "abnormal source Lot",
          icon: Search,
          tone: "critical",
        },
        {
          label: "Impact Lots",
          value: String(state.impact_lots.length),
          detail: "excluding source Lot",
          icon: Boxes,
          tone: "warning",
        },
        {
          label: "Target chamber",
          value: getTargetChamber(state),
          detail: "shared excursion exposure",
          icon: Server,
          tone: "accent",
        },
        {
          label: "Exposed population",
          value: String(state.affected_lots.length),
          detail: "source plus impact Lots",
          icon: Activity,
          tone: "neutral",
        },
      ]
    : [
    {
      label: "Affected lots",
      value: String(state.affected_lots.length),
      detail: "WAT fail population",
      icon: Boxes,
      tone: "critical",
    },
    {
      label: "Normal reference",
      value: String(getNormalLotCount(state)),
      detail: "comparison lots",
      icon: Activity,
      tone: "neutral",
    },
    {
      label: "Target chamber",
      value: getTargetChamber(state),
      detail: "100% commonality",
      icon: Server,
      tone: "accent",
    },
    {
      label: "Hold records",
      value: String(getHoldCount(state)),
      detail: "MES comments",
      icon: PanelTop,
      tone: "warning",
    },
      ];

  return (
    <div className="investigation-view">
      <div className="job-summary-row">
        <div className="job-summary-copy">
          <StatusBadge status={state.job.status} />
          <span>{state.task_plan?.objective ?? state.job.user_query}</span>
        </div>
        <code>{state.job.job_id}</code>
      </div>

      <section className="metric-grid" aria-label="Investigation summary">
        {metrics.map(({ label, value, detail, icon: Icon, tone }) => (
          <article
            className={`metric-card metric-${tone} ${value.length > 12 ? "metric-long-value" : ""}`}
            key={label}
          >
            <div className="metric-icon">
              <Icon size={18} aria-hidden="true" />
            </div>
            <div>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>{detail}</small>
            </div>
          </article>
        ))}
      </section>

      <RuntimeMetadata state={state} />

      <WorkflowTimeline tasks={tasks} state={state} />

      <div className="analysis-grid">
        {isLotDriven ? <LotImpactPanel state={state} /> : <YieldTrendChart data={yieldTrend} />}
        <RootCausePanel state={state} />
      </div>

      <SpcEvidencePanel state={state} />

      <EvidenceChain state={state} />

      {memoryCandidate && (
        <MemoryApprovalPanel
          candidate={memoryCandidate}
          onChange={onMemoryCandidateChange}
        />
      )}

      {state.warnings.length > 0 && (
        <section className="warning-section" aria-labelledby="warnings-heading">
          <div className="section-heading-row">
            <div>
              <span className="section-kicker">Review required</span>
              <h2 id="warnings-heading">Warnings</h2>
            </div>
          </div>
          {state.warnings.map((warning) => (
            <div className="warning-row" key={warning.warning_id}>
              <AlertTriangle size={16} aria-hidden="true" />
              <strong>{warning.warning_id}</strong>
              <span>{warning.message}</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function LoadingState({ knowledge = false }: { knowledge?: boolean }) {
  return (
    <div className="loading-state" aria-live="polite">
      <div className="loading-icon">
        <Activity size={24} aria-hidden="true" />
      </div>
      <div>
        <strong>{knowledge ? "Knowledge Agent searching" : "RCA workflow running"}</strong>
        <span>
          {knowledge ? "Filtering the approved Active Index" : "Collecting specialist findings"}
        </span>
      </div>
      <div className="loading-track">
        <span />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">
        <Boxes size={30} aria-hidden="true" />
      </div>
      <h2>No investigation selected</h2>
      <p>RCA job results will appear in this workspace.</p>
    </div>
  );
}

export default App;
