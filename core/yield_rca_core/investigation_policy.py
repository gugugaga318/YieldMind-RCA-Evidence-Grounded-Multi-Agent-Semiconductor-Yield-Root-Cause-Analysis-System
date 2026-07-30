"""Deterministic next-action policy for controlled ReAct RCA investigations."""

from __future__ import annotations

from dataclasses import dataclass, field

from yield_rca_core.investigation_models import (
    ActionKind,
    ActionRecord,
    ConclusionLevel,
    GoalStatus,
    InvestigationAction,
    InvestigationGoal,
    InvestigationIntent,
    StopReason,
)
from yield_rca_core.models import AgentFinding, AgentKind


@dataclass(frozen=True)
class ActionDefinition:
    """Static allowlist entry connecting a controlled action to one specialist."""

    kind: str
    agent: str
    required_finding_agents: tuple[str, ...] = ()


ACTION_REGISTRY: dict[str, ActionDefinition] = {
    ActionKind.INSPECT_DEFECT_PATTERN.value: ActionDefinition(
        kind=ActionKind.INSPECT_DEFECT_PATTERN.value,
        agent=AgentKind.DEFECT_WAT.value,
    ),
    ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value: ActionDefinition(
        kind=ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value,
        agent=AgentKind.DEFECT_WAT.value,
        required_finding_agents=(AgentKind.MES.value, AgentKind.DEFECT_WAT.value),
    ),
    ActionKind.FIND_SHARED_EXPOSURE.value: ActionDefinition(
        kind=ActionKind.FIND_SHARED_EXPOSURE.value,
        agent=AgentKind.MES.value,
    ),
    ActionKind.ASSESS_IMPACT_SCOPE.value: ActionDefinition(
        kind=ActionKind.ASSESS_IMPACT_SCOPE.value,
        agent=AgentKind.MES.value,
        required_finding_agents=(AgentKind.MES.value,),
    ),
    ActionKind.INSPECT_FDC_SPC.value: ActionDefinition(
        kind=ActionKind.INSPECT_FDC_SPC.value,
        agent=AgentKind.FDC.value,
        required_finding_agents=(AgentKind.MES.value,),
    ),
    ActionKind.INSPECT_RECIPE_CHANGE.value: ActionDefinition(
        kind=ActionKind.INSPECT_RECIPE_CHANGE.value,
        agent=AgentKind.MES.value,
        required_finding_agents=(AgentKind.MES.value,),
    ),
    ActionKind.VALIDATE_HISTORICAL_CASE.value: ActionDefinition(
        kind=ActionKind.VALIDATE_HISTORICAL_CASE.value,
        agent=AgentKind.KNOWLEDGE.value,
        required_finding_agents=(
            AgentKind.MES.value,
            AgentKind.FDC.value,
            AgentKind.DEFECT_WAT.value,
        ),
    ),
    ActionKind.RUN_RCA_REASONING.value: ActionDefinition(
        kind=ActionKind.RUN_RCA_REASONING.value,
        agent=AgentKind.RCA_REASONING.value,
        required_finding_agents=(
            AgentKind.MES.value,
            AgentKind.FDC.value,
            AgentKind.DEFECT_WAT.value,
        ),
    ),
    ActionKind.CONCLUDE_INCONCLUSIVE.value: ActionDefinition(
        kind=ActionKind.CONCLUDE_INCONCLUSIVE.value,
        agent=AgentKind.RCA_REASONING.value,
    ),
}


@dataclass(frozen=True)
class PolicyDecision:
    """A policy result: either one legal next action or an explicit stop state."""

    goal_status: str
    conclusion_level: str
    next_action: InvestigationAction | None
    evidence_gaps: list[str]
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        GoalStatus(self.goal_status)
        ConclusionLevel(self.conclusion_level)
        if self.next_action is not None and not isinstance(self.next_action, InvestigationAction):
            raise ValueError("next_action must be an InvestigationAction or None")
        if self.stop_reason is not None:
            StopReason(self.stop_reason)
        if self.next_action is None and self.stop_reason is None:
            raise ValueError("a terminal policy decision requires stop_reason")
        if self.next_action is not None and self.stop_reason is not None:
            raise ValueError("a next action and stop_reason are mutually exclusive")


def _finding_agents(findings: list[AgentFinding]) -> set[str]:
    return {finding.agent for finding in findings}


def _completed_action_kinds(records: list[ActionRecord]) -> set[str]:
    return {
        record.action.kind
        for record in records
        if record.status in {"completed", "skipped", "failed"}
    }


def _action(
    goal: InvestigationGoal,
    kind: str,
    reason: str,
    *,
    required_evidence_ids: list[str] | None = None,
) -> InvestigationAction:
    definition = ACTION_REGISTRY[kind]
    return InvestigationAction(
        action_id=f"{goal.goal_id}:{kind}",
        kind=kind,
        agent=definition.agent,
        reason=reason,
        inputs=dict(goal.known_facts),
        required_evidence_ids=required_evidence_ids or [],
    )


@dataclass(frozen=True)
class InvestigationPolicy:
    """Select the smallest legal next action using deterministic safety rules."""

    registry: dict[str, ActionDefinition] = field(default_factory=lambda: dict(ACTION_REGISTRY))

    def __post_init__(self) -> None:
        registry = self.registry
        if set(registry) != set(ACTION_REGISTRY):
            raise ValueError("controlled ReAct policy requires the complete action registry")
        object.__setattr__(self, "registry", dict(registry))

    def next_action(
        self,
        *,
        goal: InvestigationGoal,
        findings: list[AgentFinding],
        action_records: list[ActionRecord],
        tool_call_count: int,
        critical_contradictions: list[str] | None = None,
    ) -> PolicyDecision:
        """Return one allowed next action or an explainable terminal decision."""
        if len(action_records) >= goal.max_steps or tool_call_count >= goal.max_tool_calls:
            return self._stop(
                GoalStatus.BUDGET_EXHAUSTED.value,
                ConclusionLevel.INCONCLUSIVE.value,
                StopReason.BUDGET_EXHAUSTED.value,
                self._gaps(goal, findings),
            )

        contradictions = critical_contradictions or []
        if contradictions:
            return self._stop(
                GoalStatus.BLOCKED.value,
                ConclusionLevel.CONFLICTED.value,
                StopReason.CRITICAL_CONTRADICTION.value,
                list(dict.fromkeys(contradictions)),
            )

        agents = _finding_agents(findings)
        completed = _completed_action_kinds(action_records)
        gaps = self._gaps(goal, findings)

        if goal.intent == InvestigationIntent.IMPACT_SCOPE.value:
            if AgentKind.MES.value not in agents:
                return self._next(
                    goal,
                    completed,
                    ActionKind.FIND_SHARED_EXPOSURE.value,
                    "Impact scope requires shared process exposure before selecting affected lots.",
                    gaps,
                )
            return self._stop(
                GoalStatus.SATISFIED.value,
                ConclusionLevel.SIGNAL.value,
                StopReason.GOAL_SATISFIED.value,
                [],
            )

        if goal.intent == InvestigationIntent.SPC_CHECK.value:
            if AgentKind.MES.value not in agents and not self._has_process_context(goal):
                return self._next(
                    goal,
                    completed,
                    ActionKind.FIND_SHARED_EXPOSURE.value,
                    (
                        "SPC analysis needs equipment, chamber, operation, and "
                        "exposure-window context."
                    ),
                    gaps,
                )
            if AgentKind.FDC.value not in agents:
                return self._next(
                    goal,
                    completed,
                    ActionKind.INSPECT_FDC_SPC.value,
                    "The process context is available; inspect FDC, OOC, and SPC evidence.",
                    gaps,
                )
            return self._stop(
                GoalStatus.SATISFIED.value,
                ConclusionLevel.SIGNAL.value,
                StopReason.GOAL_SATISFIED.value,
                [],
            )

        if goal.intent == InvestigationIntent.HISTORICAL_LOOKUP.value:
            required = {AgentKind.MES.value, AgentKind.FDC.value, AgentKind.DEFECT_WAT.value}
            if not required <= agents:
                return self._root_cause_next(goal, findings, agents, completed, gaps)
            if AgentKind.KNOWLEDGE.value not in agents:
                return self._next(
                    goal,
                    completed,
                    ActionKind.VALIDATE_HISTORICAL_CASE.value,
                    (
                        "Historical retrieval requires current MES, FDC, and defect "
                        "context for validation."
                    ),
                    gaps,
                )
            return self._stop(
                GoalStatus.SATISFIED.value,
                ConclusionLevel.CANDIDATE.value,
                StopReason.GOAL_SATISFIED.value,
                [],
            )

        return self._root_cause_next(goal, findings, agents, completed, gaps)

    def _root_cause_next(
        self,
        goal: InvestigationGoal,
        findings: list[AgentFinding],
        agents: set[str],
        completed: set[str],
        gaps: list[str],
    ) -> PolicyDecision:
        if AgentKind.MES.value not in agents and not goal.known_facts.get("lot_id"):
            return self._next(
                goal,
                completed,
                ActionKind.FIND_SHARED_EXPOSURE.value,
                "A product-window RCA needs MES scope before a Lot-specific defect inspection.",
                gaps,
            )
        if AgentKind.DEFECT_WAT.value not in agents:
            return self._next(
                goal,
                completed,
                ActionKind.INSPECT_DEFECT_PATTERN.value,
                "Root-cause assessment requires an observed product-outcome pattern.",
                gaps,
            )
        if AgentKind.MES.value not in agents:
            return self._next(
                goal,
                completed,
                ActionKind.FIND_SHARED_EXPOSURE.value,
                "The observed outcome needs shared equipment, chamber, recipe, and timing context.",
                gaps,
            )
        if ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value not in completed:
            return self._next(
                goal,
                completed,
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN.value,
                "Shared-exposure Lots require a second defect-pattern comparison before FDC RCA.",
                gaps,
            )
        if AgentKind.FDC.value not in agents:
            return self._next(
                goal,
                completed,
                ActionKind.INSPECT_FDC_SPC.value,
                "The shared exposure needs FDC/SPC process-mechanism evidence.",
                gaps,
            )
        if AgentKind.RCA_REASONING.value not in agents:
            return self._next(
                goal,
                completed,
                ActionKind.RUN_RCA_REASONING.value,
                "Scope, mechanism, and product-outcome findings are available for RCA gating.",
                gaps,
            )
        if not self._shared_defect_pattern_matches(findings):
            return self._stop(
                GoalStatus.SATISFIED.value,
                ConclusionLevel.CANDIDATE.value,
                StopReason.GOAL_SATISFIED.value,
                ["matching_shared_defect_pattern"],
            )
        rca = next(
            finding for finding in findings if finding.agent == AgentKind.RCA_REASONING.value
        )
        rca_status = str(rca.details.get("status", "inconclusive"))
        if rca_status != ConclusionLevel.SUPPORTED.value:
            return self._stop(
                GoalStatus.SATISFIED.value,
                ConclusionLevel(rca_status).value,
                StopReason.GOAL_SATISFIED.value,
                gaps,
            )
        return self._stop(
            GoalStatus.SATISFIED.value,
            ConclusionLevel.SUPPORTED.value,
            StopReason.GOAL_SATISFIED.value,
            [],
        )

    @staticmethod
    def _has_process_context(goal: InvestigationGoal) -> bool:
        facts = goal.known_facts
        return all(facts.get(field) for field in ("equipment_id", "chamber_id", "operation_no"))

    @staticmethod
    def _gaps(goal: InvestigationGoal, findings: list[AgentFinding]) -> list[str]:
        agents = _finding_agents(findings)
        standard_gaps = {
            AgentKind.MES.value: "shared_exposure",
            AgentKind.FDC.value: "process_mechanism",
            AgentKind.DEFECT_WAT.value: "product_outcome",
        }
        missing = [gap for agent, gap in standard_gaps.items() if agent not in agents]
        return list(dict.fromkeys([*goal.required_evidence, *missing]))

    @staticmethod
    def _shared_defect_pattern_matches(findings: list[AgentFinding]) -> bool:
        defect_findings = [
            finding for finding in findings if finding.agent == AgentKind.DEFECT_WAT.value
        ]
        source = next(
            (
                finding
                for finding in defect_findings
                if finding.details.get("evidence_scope") == "selected_lots"
            ),
            None,
        )
        comparison = next(
            (
                finding
                for finding in defect_findings
                if finding.details.get("evidence_scope") == "shared_exposure_comparison"
            ),
            None,
        )
        if source is None or comparison is None:
            return False
        source_patterns = {
            str(pattern)
            for pattern, count in source.details.get("defect_patterns", {}).items()
            if int(count) > 0
        }
        comparison_patterns = {
            str(pattern)
            for pattern, count in comparison.details.get("defect_patterns", {}).items()
            if int(count) > 0
        }
        return bool(source_patterns & comparison_patterns)

    def _next(
        self,
        goal: InvestigationGoal,
        completed: set[str],
        kind: str,
        reason: str,
        gaps: list[str],
    ) -> PolicyDecision:
        definition = self.registry[kind]
        if kind in completed:
            return self._stop(
                GoalStatus.BLOCKED.value,
                ConclusionLevel.INCONCLUSIVE.value,
                StopReason.NO_ALLOWED_ACTION.value,
                gaps,
            )
        return PolicyDecision(
            goal_status=GoalStatus.IN_PROGRESS.value,
            conclusion_level=ConclusionLevel.SIGNAL.value,
            next_action=_action(goal, definition.kind, reason),
            evidence_gaps=gaps,
        )

    @staticmethod
    def _stop(
        goal_status: str,
        conclusion_level: str,
        stop_reason: str,
        evidence_gaps: list[str],
    ) -> PolicyDecision:
        return PolicyDecision(
            goal_status=goal_status,
            conclusion_level=conclusion_level,
            next_action=None,
            evidence_gaps=evidence_gaps,
            stop_reason=stop_reason,
        )
