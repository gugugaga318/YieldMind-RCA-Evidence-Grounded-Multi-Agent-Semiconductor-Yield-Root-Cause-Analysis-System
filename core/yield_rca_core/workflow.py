"""Pure Python composition root for the Yield RCA workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from yield_rca_core.improvement_agent import ImprovementAgent
from yield_rca_core.investigation_models import OrchestrationMode
from yield_rca_core.llm_gateway import (
    LLMClient,
    LLMSettings,
    build_llm_client,
    capture_llm_usage,
)
from yield_rca_core.models import InvestigationMode, RCAJob, RCAState
from yield_rca_core.planner_agent import PlannerAgent
from yield_rca_core.rca_reasoning_agent import RCAReasoningAgent
from yield_rca_core.report_generator import ReportGenerator
from yield_rca_core.repositories import CsvFabRepository, FabRepository, PostgresFabRepository
from yield_rca_core.specialist_agents import DefectWATAgent, FDCAgent, KnowledgeAgent, MESAgent
from yield_rca_core.supervisor import Supervisor
from yield_rca_core.tool_layer import (
    AnalyzeLotGenealogyTool,
    AnalyzeParameterShiftTool,
    AnalyzeSpcEvidenceTool,
    FindAffectedLotsTool,
    FindImpactLotsTool,
    FindOocEventsTool,
    GetLotContextTool,
    PerformBasicSpcAnalysisTool,
    RetrieveSimilarCaseTool,
    SummarizeDefectWatTool,
    capture_tool_latencies,
)


@dataclass(frozen=True)
class PurePythonRCAWorkflow:
    """Plan and execute one RCA job without HTTP or frontend dependencies."""

    planner: PlannerAgent
    supervisor: Supervisor
    llm_settings: LLMSettings
    orchestration_mode: str = OrchestrationMode.FIXED.value

    def run(
        self,
        user_query: str,
        *,
        job_id: str,
        plan_id: str | None = None,
        lot_id: str | None = None,
        orchestration_mode_override: str | None = None,
    ) -> RCAState:
        active_orchestration_mode = orchestration_mode_override or self.orchestration_mode
        OrchestrationMode(active_orchestration_mode)
        started = perf_counter()
        with capture_llm_usage() as llm_usage, capture_tool_latencies() as tool_latencies:
            task_plan = self.planner.plan(user_query, plan_id=plan_id, lot_id=lot_id)
            mes_task = next(task for task in task_plan.tasks if task.agent == "mes")
            raw_window = mes_task.inputs.get("time_window", {})
            time_window = (
                {str(key): str(value) for key, value in raw_window.items()}
                if isinstance(raw_window, dict)
                else {}
            )
            product_id = mes_task.inputs.get("product_id")
            source_lot_id = mes_task.inputs.get("lot_id")
            investigation_mode = str(
                mes_task.inputs.get(
                    "investigation_mode",
                    InvestigationMode.PRODUCT_WINDOW.value,
                )
            )
            job = RCAJob(
                job_id=job_id,
                user_query=user_query,
                investigation_mode=investigation_mode,
                source_lot_id=str(source_lot_id) if source_lot_id else None,
                product_id=str(product_id) if product_id else None,
                time_window=time_window,
            )
            if active_orchestration_mode == OrchestrationMode.CONTROLLED_REACT.value:
                goal = self.planner.plan_investigation_goal(user_query, lot_id=lot_id)
                state = self.supervisor.execute_controlled(job, goal)
            else:
                state = self.supervisor.execute(job, task_plan)

        total_tokens = sum(event.total_tokens for event in llm_usage)
        llm_latency_ms = round(sum(event.latency_ms for event in llm_usage), 3)
        metadata = {
            "agent_mode": self.llm_settings.agent_mode,
            "provider": (
                self.supervisor.llm_client.provider if self.supervisor.llm_client else None
            ),
            "model": self.supervisor.llm_client.model if self.supervisor.llm_client else None,
            "prompt_version": (self.planner.prompt_version if self.supervisor.llm_client else None),
            "total_tokens": total_tokens,
            "llm_call_count": len(llm_usage),
            "llm_latency_ms": llm_latency_ms,
            "tool_call_count": len(tool_latencies),
            "tool_latency_ms": round(
                sum(float(record["duration_ms"]) for record in tool_latencies),
                3,
            ),
            "tool_latencies": list(tool_latencies),
            "workflow_duration_ms": round((perf_counter() - started) * 1000.0, 3),
            "reasoning_engine": "hypothesis_v1",
            "hypothesis_engine_mode": "active",
            "orchestration_mode": active_orchestration_mode,
        }
        return RCAState.from_dict(
            {
                **state.to_dict(),
                "llm_usage": [event.to_dict() for event in llm_usage],
                "execution_metadata": metadata,
            }
        )


def build_workflow(
    repository: FabRepository,
    *,
    llm_settings: LLMSettings | None = None,
    llm_client: LLMClient | None = None,
    orchestration_mode: str | None = None,
) -> PurePythonRCAWorkflow:
    """Assemble Tools, Agents, Supervisor, and Planner around a Repository."""

    settings = llm_settings or LLMSettings.from_env()
    selected_orchestration_mode = (
        orchestration_mode
        or os.getenv("YIELD_RCA_ORCHESTRATION_MODE")
        or OrchestrationMode.FIXED.value
    ).strip()
    try:
        OrchestrationMode(selected_orchestration_mode)
    except ValueError as exc:
        raise ValueError(
            "YIELD_RCA_ORCHESTRATION_MODE must be fixed or controlled_react"
        ) from exc
    shared_llm_client = llm_client if llm_client is not None else build_llm_client(settings)
    mes_agent = MESAgent(
        find_affected_lots_tool=FindAffectedLotsTool(repository),
        analyze_lot_genealogy_tool=AnalyzeLotGenealogyTool(repository),
        get_lot_context_tool=GetLotContextTool(repository),
        find_impact_lots_tool=FindImpactLotsTool(repository),
    )
    advanced_spc_tool = (
        AnalyzeSpcEvidenceTool(repository) if repository.rows("spc_baseline_profile") else None
    )
    fdc_agent = FDCAgent(
        analyze_parameter_shift_tool=AnalyzeParameterShiftTool(repository),
        find_ooc_events_tool=FindOocEventsTool(repository),
        perform_basic_spc_analysis_tool=PerformBasicSpcAnalysisTool(repository),
        analyze_spc_evidence_tool=advanced_spc_tool,
    )
    defect_wat_agent = DefectWATAgent(summarize_defect_wat_tool=SummarizeDefectWatTool(repository))
    knowledge_agent = KnowledgeAgent(retrieve_similar_case_tool=RetrieveSimilarCaseTool(repository))
    supervisor = Supervisor(
        mes_agent=mes_agent,
        fdc_agent=fdc_agent,
        defect_wat_agent=defect_wat_agent,
        knowledge_agent=knowledge_agent,
        rca_reasoning_agent=RCAReasoningAgent(
            llm_client=shared_llm_client,
            agent_mode=settings.agent_mode,
        ),
        improvement_agent=ImprovementAgent(
            llm_client=shared_llm_client,
            agent_mode=settings.agent_mode,
        ),
        report_generator=ReportGenerator(),
        llm_client=shared_llm_client,
        agent_mode=settings.agent_mode,
    )
    return PurePythonRCAWorkflow(
        planner=PlannerAgent(
            llm_client=shared_llm_client,
            agent_mode=settings.agent_mode,
        ),
        supervisor=supervisor,
        llm_settings=settings,
        orchestration_mode=selected_orchestration_mode,
    )


def build_csv_workflow(
    seed_dir: Path,
    *,
    llm_settings: LLMSettings | None = None,
    llm_client: LLMClient | None = None,
    orchestration_mode: str | None = None,
) -> PurePythonRCAWorkflow:
    """Build a workflow over an existing offline seed dataset."""

    return build_workflow(
        CsvFabRepository(seed_dir),
        llm_settings=llm_settings,
        llm_client=llm_client,
        orchestration_mode=orchestration_mode,
    )


def build_postgres_workflow(
    database_url: str,
    *,
    llm_settings: LLMSettings | None = None,
    llm_client: LLMClient | None = None,
    orchestration_mode: str | None = None,
) -> PurePythonRCAWorkflow:
    """Build a workflow over a previously seeded PostgreSQL database."""

    return build_workflow(
        PostgresFabRepository(database_url),
        llm_settings=llm_settings,
        llm_client=llm_client,
        orchestration_mode=orchestration_mode,
    )
