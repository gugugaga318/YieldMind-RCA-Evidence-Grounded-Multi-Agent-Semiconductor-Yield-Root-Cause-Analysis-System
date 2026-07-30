"""Planner Agent for the Yield RCA MVP.

The Planner converts a user question into a structured task graph. It does
not execute tasks, access data, call Tools, infer a root cause, or render a
report.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import date

from yield_rca_core.investigation_models import InvestigationGoal, InvestigationIntent
from yield_rca_core.llm_gateway import LLMClient, LLMOutputValidationError, LLMRequest
from yield_rca_core.models import (
    AgentKind,
    AgentMode,
    AgentTask,
    FindingKind,
    InvestigationMode,
    ModelValidationError,
    TaskPlan,
)

DEFAULT_PLANNABLE_AGENTS = frozenset(
    {
        AgentKind.MES.value,
        AgentKind.FDC.value,
        AgentKind.DEFECT_WAT.value,
        AgentKind.KNOWLEDGE.value,
        AgentKind.RCA_REASONING.value,
        AgentKind.IMPROVEMENT.value,
    }
)

_MONTHS = {month.lower(): index for index, month in enumerate(calendar.month_name) if month}


class PlannerConfigurationError(ValueError):
    """Raised when the Planner's registered Agent set cannot execute its plan."""


def _normalize_query(user_query: str) -> str:
    if not isinstance(user_query, str) or not user_query.strip():
        raise ModelValidationError("user_query must be a non-empty string")
    return " ".join(user_query.split())


def _default_plan_id(user_query: str) -> str:
    digest = hashlib.sha256(user_query.encode("utf-8")).hexdigest()[:12]
    return f"plan_{digest}"


def _extract_product_id(user_query: str) -> str | None:
    match = re.search(
        r"(?<![A-Za-z0-9_])\d{2,3}[A-Za-z]?_[A-Za-z0-9_]+(?![A-Za-z0-9_])",
        user_query,
    )
    return match.group(0).upper() if match else None


def _extract_lot_id(user_query: str) -> str | None:
    match = re.search(r"(?<![A-Za-z0-9_])LOT_[A-Za-z0-9_]+(?![A-Za-z0-9_])", user_query, re.I)
    return match.group(0).upper() if match else None


def _month_window(year: int, month: int) -> dict[str, str | int]:
    last_day = calendar.monthrange(year, month)[1]
    return {
        "start_date": date(year, month, 1).isoformat(),
        "end_date": date(year, month, last_day).isoformat(),
        "year": year,
        "month": month,
    }


def _extract_time_window(user_query: str) -> dict[str, str | int] | None:
    iso_dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", user_query)
    if len(iso_dates) >= 2:
        start_date = date.fromisoformat(iso_dates[0])
        end_date = date.fromisoformat(iso_dates[1])
        if end_date < start_date:
            raise ModelValidationError("time window end date must not precede start date")
        return {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}

    chinese_year_month = re.search(r"(?<!\d)(20\d{2})\s*年\s*(1[0-2]|[1-9])\s*月", user_query)
    if chinese_year_month:
        return _month_window(
            int(chinese_year_month.group(1)),
            int(chinese_year_month.group(2)),
        )

    lowered = user_query.lower()
    for month_name, month_number in _MONTHS.items():
        match = re.search(rf"\b{month_name}\b(?:\s*,?\s*(20\d{{2}}))?", lowered)
        if not match:
            continue
        if match.group(1):
            return _month_window(int(match.group(1)), month_number)
        return {"month": month_number, "label": calendar.month_name[month_number]}

    chinese_month = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月", user_query)
    if chinese_month:
        month_number = int(chinese_month.group(1))
        return {"month": month_number, "label": f"{month_number}月"}
    return None


@dataclass(frozen=True)
class PlannerAgent:
    """Create an acyclic TaskPlan using only explicitly registered Agents."""

    registered_agents: frozenset[str] = DEFAULT_PLANNABLE_AGENTS
    llm_client: LLMClient | None = None
    agent_mode: str = AgentMode.DETERMINISTIC.value
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.registered_agents:
            raise PlannerConfigurationError("registered_agents must not be empty")
        known_agents = {agent.value for agent in AgentKind}
        unknown_agents = set(self.registered_agents) - known_agents
        if unknown_agents:
            raise PlannerConfigurationError(f"unknown registered agents: {sorted(unknown_agents)}")
        try:
            AgentMode(self.agent_mode)
        except ValueError as exc:
            raise PlannerConfigurationError(f"unknown agent mode: {self.agent_mode}") from exc
        if self.agent_mode == AgentMode.DETERMINISTIC.value and self.llm_client is not None:
            raise PlannerConfigurationError("deterministic Planner must not configure an LLM")
        if self.agent_mode != AgentMode.DETERMINISTIC.value and self.llm_client is None:
            raise PlannerConfigurationError("LLM/Fake Planner requires an LLM client")

    def plan(
        self,
        user_query: str,
        *,
        plan_id: str | None = None,
        lot_id: str | None = None,
    ) -> TaskPlan:
        normalized_query = _normalize_query(user_query)
        selected_agents = DEFAULT_PLANNABLE_AGENTS
        unavailable_agents = selected_agents - self.registered_agents
        if unavailable_agents:
            raise PlannerConfigurationError(
                f"yield RCA planning requires unregistered agents: {sorted(unavailable_agents)}"
            )

        resolved_lot_id = (lot_id or _extract_lot_id(normalized_query) or "").strip().upper()
        investigation_mode = (
            InvestigationMode.LOT.value
            if resolved_lot_id
            else InvestigationMode.PRODUCT_WINDOW.value
        )
        mes_inputs: dict[str, object] = {
            "user_query": normalized_query,
            "investigation_mode": investigation_mode,
        }
        if resolved_lot_id:
            mes_inputs["lot_id"] = resolved_lot_id
        else:
            product_id = _extract_product_id(normalized_query)
            if product_id:
                mes_inputs["product_id"] = product_id
            time_window = _extract_time_window(normalized_query)
            if time_window:
                mes_inputs["time_window"] = time_window

        task_mes = AgentTask(
            task_id="task_mes",
            agent=AgentKind.MES.value,
            objective=(
                "Resolve the abnormal Lot and identify impact Lots from shared exposure."
                if resolved_lot_id
                else "Identify affected lots and analyze MES process commonality."
            ),
            inputs=mes_inputs,
        )
        task_fdc = AgentTask(
            task_id="task_fdc",
            agent=AgentKind.FDC.value,
            objective="Analyze FDC feature shifts and OOC events for the MES process context.",
            depends_on=[task_mes.task_id],
            inputs={
                "lot_ids_from": "task_mes.affected_lots",
                "process_context_from": "task_mes.target_commonality",
                "finding_task_ids": [task_mes.task_id],
            },
        )
        task_defect_wat = AgentTask(
            task_id="task_defect_wat",
            agent=AgentKind.DEFECT_WAT.value,
            objective="Analyze defect patterns and WAT fail modes for affected lots.",
            depends_on=[task_mes.task_id],
            inputs={
                "lot_ids_from": "task_mes.affected_lots",
                "finding_task_ids": [task_mes.task_id],
            },
        )
        task_knowledge_discovery = AgentTask(
            task_id="task_knowledge_discovery",
            agent=AgentKind.KNOWLEDGE.value,
            objective="Retrieve historical cases relevant to the collected engineering findings.",
            depends_on=[task_mes.task_id, task_fdc.task_id, task_defect_wat.task_id],
            inputs={
                "user_query": normalized_query,
                "finding_task_ids": [
                    task_mes.task_id,
                    task_fdc.task_id,
                    task_defect_wat.task_id,
                ],
            },
            finding_kind=FindingKind.KNOWLEDGE_DISCOVERY.value,
        )
        task_knowledge_validation = AgentTask(
            task_id="task_knowledge_validation",
            agent=AgentKind.KNOWLEDGE.value,
            objective=(
                "Validate legacy preliminary RCA candidates against confirmed historical "
                "knowledge assets."
            ),
            depends_on=[task_knowledge_discovery.task_id],
            inputs={
                "user_query": normalized_query,
                "finding_task_ids": [
                    task_mes.task_id,
                    task_fdc.task_id,
                    task_defect_wat.task_id,
                    task_knowledge_discovery.task_id,
                ],
            },
            finding_kind=FindingKind.KNOWLEDGE_VALIDATION.value,
        )
        task_rca = AgentTask(
            task_id="task_rca",
            agent=AgentKind.RCA_REASONING.value,
            objective="Fuse Specialist findings into an evidence-based RCA assessment.",
            depends_on=[
                task_mes.task_id,
                task_fdc.task_id,
                task_defect_wat.task_id,
                task_knowledge_discovery.task_id,
                task_knowledge_validation.task_id,
            ],
            inputs={
                "finding_task_ids": [
                    task_mes.task_id,
                    task_fdc.task_id,
                    task_defect_wat.task_id,
                    task_knowledge_discovery.task_id,
                    task_knowledge_validation.task_id,
                ]
            },
        )
        task_improvement = AgentTask(
            task_id="task_improvement",
            agent=AgentKind.IMPROVEMENT.value,
            objective=(
                "Synthesize evidence-backed containment, corrective, Recipe, preventive, "
                "and Fab-level improvement recommendations."
            ),
            depends_on=[task_rca.task_id],
            inputs={
                "finding_task_ids": [
                    task_mes.task_id,
                    task_fdc.task_id,
                    task_defect_wat.task_id,
                    task_knowledge_discovery.task_id,
                    task_knowledge_validation.task_id,
                    task_rca.task_id,
                ]
            },
        )

        tasks = [
            task_mes,
            task_fdc,
            task_defect_wat,
            task_knowledge_discovery,
            task_knowledge_validation,
            task_rca,
            task_improvement,
        ]
        self._validate_registered_agents(tasks)
        fallback_plan = TaskPlan(
            plan_id=plan_id or _default_plan_id(normalized_query),
            objective=(
                f"Investigate abnormal Lot {resolved_lot_id}: {normalized_query}"
                if resolved_lot_id
                else f"Investigate the yield RCA request: {normalized_query}"
            ),
            tasks=tasks,
        )
        if self.agent_mode == AgentMode.DETERMINISTIC.value:
            return fallback_plan

        assert self.llm_client is not None
        response = self.llm_client.complete_json(
            LLMRequest(
                agent=AgentKind.PLANNER.value,
                prompt_name="planner",
                prompt_version=self.prompt_version,
                payload={
                    "user_query": normalized_query,
                    "requested_plan_id": fallback_plan.plan_id,
                    "registered_agents": sorted(self.registered_agents),
                    "required_agents": sorted(DEFAULT_PLANNABLE_AGENTS),
                    "fallback_plan": fallback_plan.to_dict(),
                },
            )
        )
        try:
            planned = TaskPlan.from_dict(response.data)
        except (KeyError, TypeError, ModelValidationError) as exc:
            raise LLMOutputValidationError("Planner returned an invalid TaskPlan") from exc
        if planned.plan_id != fallback_plan.plan_id:
            raise LLMOutputValidationError("Planner changed the requested plan_id")
        planned_agents = {task.agent for task in planned.tasks}
        if planned_agents != DEFAULT_PLANNABLE_AGENTS:
            raise LLMOutputValidationError(
                "Planner TaskPlan must contain MES, FDC, Defect/WAT, Knowledge, RCA, "
                "and Improvement"
            )
        self._validate_registered_agents(planned.tasks)
        return planned

    def plan_investigation_goal(
        self,
        user_query: str,
        *,
        lot_id: str | None = None,
    ) -> InvestigationGoal:
        """Extract a bounded controlled-ReAct goal without selecting free-form tools."""
        query = _normalize_query(user_query)
        lowered = query.lower()
        resolved_lot_id = (lot_id or _extract_lot_id(query) or "").strip().upper()
        if any(token in lowered for token in ("impact", "affected lots", "影响批次")) and not any(
            token in lowered for token in ("root cause", "rca", "原因", "根因")
        ):
            intent = InvestigationIntent.IMPACT_SCOPE.value
        elif "spc" in lowered and not any(
            token in lowered for token in ("root cause", "原因", "根因")
        ):
            intent = InvestigationIntent.SPC_CHECK.value
        elif any(token in lowered for token in ("historical", "similar case", "历史案例")):
            intent = InvestigationIntent.HISTORICAL_LOOKUP.value
        elif any(token in lowered for token in ("full rca", "完整rca", "完整 rca")):
            intent = InvestigationIntent.FULL_RCA.value
        else:
            intent = InvestigationIntent.ROOT_CAUSE.value
        facts: dict[str, object] = {}
        if resolved_lot_id:
            facts["lot_id"] = resolved_lot_id
        product_id = _extract_product_id(query)
        if product_id:
            facts["product_id"] = product_id
        time_window = _extract_time_window(query)
        if time_window:
            facts.update(time_window)
        if any(token in lowered for token in ("scratch", "划伤", "刮伤")):
            facts["defect"] = "scratch"
        elif "缺陷" in lowered:
            facts["defect"] = "reported_defect"
        if ("cu" in lowered and "cmp" in lowered) or any(
            token in lowered for token in ("铜cmp", "铜 cmp")
        ):
            facts["module"] = "CU_CMP"
        return InvestigationGoal(
            goal_id=f"goal_{_default_plan_id(query).removeprefix('plan_')}",
            intent=intent,
            summary=query,
            known_facts=facts,
        )

    def _validate_registered_agents(self, tasks: list[AgentTask]) -> None:
        unregistered = {task.agent for task in tasks} - self.registered_agents
        if unregistered:
            raise PlannerConfigurationError(
                f"TaskPlan references unregistered agents: {sorted(unregistered)}"
            )
