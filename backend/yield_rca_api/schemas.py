"""HTTP request and response contracts for the RCA API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class APIModel(BaseModel):
    """Strict base model shared by all public API contracts."""

    model_config = ConfigDict(extra="forbid")


class CreateRCAJobRequest(APIModel):
    """Request to execute one RCA investigation."""

    investigation_mode: Literal["product_window", "lot"] = "product_window"
    user_query: str | None = Field(default=None, min_length=1, max_length=2000)
    lot_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("user_query")
    @classmethod
    def query_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("user_query must not be blank")
        return stripped

    @field_validator("lot_id")
    @classmethod
    def normalize_lot_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip().upper()
        if not stripped:
            raise ValueError("lot_id must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_mode_fields(self) -> CreateRCAJobRequest:
        if self.investigation_mode == "lot" and self.lot_id is None:
            raise ValueError("lot investigation requires lot_id")
        if self.investigation_mode == "product_window" and self.user_query is None:
            raise ValueError("product_window investigation requires user_query")
        return self

    def resolved_user_query(self) -> str:
        if self.user_query:
            return self.user_query
        return f"Analyze abnormal Lot {self.lot_id} and identify impact Lots."


class CreateRCAJobResponse(APIModel):
    """Identity and terminal status returned after synchronous execution."""

    job_id: str
    status: str
    created_at: str
    investigation_mode: str
    source_lot_id: str | None
    state_url: str
    report_url: str
    memory_candidate_id: str | None = None
    memory_candidate_url: str | None = None


class EvidenceEntityResponse(APIModel):
    """Typed industrial entity referenced by one Evidence record."""

    entity_type: str
    entity_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvidenceResponse(APIModel):
    """Serialized Evidence, including nullable typed fields for legacy snapshots."""

    evidence_id: str
    source_type: str
    source_id: str
    summary: str
    source_table: str | None = None
    source_field: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: str | None = None
    evidence_type: str | None = None
    source_agent: str | None = None
    source_tool: str | None = None
    observation: str | None = None
    entities: list[EvidenceEntityResponse] = Field(default_factory=list)
    confidence: float | None = None
    evidence_schema_version: str | None = None


class WarningResponse(APIModel):
    """Serialized workflow warning."""

    warning_id: str
    message: str
    severity: str
    evidence_ids: list[str] = Field(default_factory=list)
    schema_version: str | None = None


class AgentTaskResponse(APIModel):
    """Serialized task plan node."""

    task_id: str
    agent: str
    objective: str
    depends_on: list[str] = Field(default_factory=list)
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    finding_kind: str | None = None
    schema_version: str | None = None


class TaskPlanResponse(APIModel):
    """Serialized execution plan."""

    plan_id: str
    objective: str
    tasks: list[AgentTaskResponse]
    schema_version: str | None = None


class InvestigationActionResponse(APIModel):
    """One registry-backed action selected by an investigation planner."""

    action_id: str
    kind: str
    agent: str
    reason: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    required_evidence_ids: list[str] = Field(default_factory=list)
    max_attempts: int = 1


class InvestigationQuestionResponse(APIModel):
    """One typed evidence question and its current lifecycle state."""

    question_id: str
    goal_id: str
    question: str
    rationale: str
    question_kind: str = "unsupported"
    scope: dict[str, Any] = Field(default_factory=dict)
    status: Literal["open", "closed", "unavailable"] = "open"
    answer: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None
    # These are derived, read-only product projections of the Python-owned
    # capability registry.  They are intentionally not accepted as Planner
    # input or persisted as mutable Question facts.
    satisfied_evidence_groups: list[str] = Field(default_factory=list)
    missing_evidence_groups: list[str] = Field(default_factory=list)
    compatible_action_kinds: list[str] = Field(default_factory=list)
    evidence_links: list[QuestionEvidenceLinkResponse] = Field(default_factory=list)


class CapabilityNoticeResponse(APIModel):
    """Typed notice for a requested capability absent from this deployment."""

    capability: str
    supported: bool
    reason: str
    available_alternatives: list[str] = Field(default_factory=list)
    request_source: Literal["user", "qwen", "system"] = "user"


class QuestionEvidenceLinkResponse(APIModel):
    """Typed relation connecting a Question to applicable Evidence."""

    question_id: str
    evidence_id: str
    action_id: str
    relation: Literal["supports", "contradicts", "context", "unavailable"]
    matched_evidence_group: str
    reason: str


class QuestionUpdateResponse(APIModel):
    """A terminal lifecycle delta for an existing investigation question."""

    question_id: str
    status: Literal["closed", "unavailable"]
    answer: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None


class PlannerDecisionResponse(APIModel):
    """One auditable LLM planner decision in the state trace."""

    decision_id: str
    goal_id: str
    decision_type: Literal["act", "stop"]
    reason: str
    goal_status: Literal["in_progress", "satisfied", "blocked", "budget_exhausted"]
    proposed_conclusion_level: Literal[
        "signal",
        "candidate",
        "supported",
        "conflicted",
        "inconclusive",
    ]
    next_action: InvestigationActionResponse | None = None
    target_question_ids: list[str] = Field(default_factory=list)
    new_questions: list[InvestigationQuestionResponse] = Field(default_factory=list)
    question_updates: list[QuestionUpdateResponse] = Field(default_factory=list)
    stop_reason: Literal[
        "goal_satisfied",
        "critical_contradiction",
        "no_allowed_action",
        "budget_exhausted",
        "data_unavailable",
    ] | None = None


class QuestionUpdateReviewResponse(APIModel):
    """Runtime acceptance or rejection of one model-proposed QuestionUpdate."""

    decision_id: str
    disposition: Literal["accepted", "rejected"]
    reason_code: Literal[
        "accepted",
        "malformed_collection",
        "too_many_updates",
        "malformed_update",
        "non_terminal_status",
        "duplicate_question",
        "unknown_question",
        "new_question_conflict",
        "terminal_question",
        "target_overlap",
        "unknown_evidence",
        "evidence_not_applicable",
        "insufficient_evidence_coverage",
        "unsupported_capability",
        "missing_unavailability_evidence",
    ]
    reason: str
    update_index: int | None = None
    question_id: str | None = None
    claimed_status: str | None = None


class DecisionEvaluationResponse(APIModel):
    """Deterministic quality metrics for one committed planner decision."""

    decision_id: str
    decision_valid: bool
    evidence_gain: bool
    redundant: bool
    reason: str
    new_evidence_ids: list[str] = Field(default_factory=list)


class RunEvaluationResponse(APIModel):
    """Run-level outcome metrics and their per-decision audit records."""

    goal_id: str
    goal_success: bool
    stop_correct: bool
    summary: str
    decision_evaluations: list[DecisionEvaluationResponse] = Field(min_length=1)


class AgentFindingResponse(APIModel):
    """Serialized AgentFinding with task identity and first-class Evidence."""

    finding_id: str
    task_id: str | None = None
    agent: str
    finding_kind: str | None = None
    summary: str
    confidence: float
    evidence_ids: list[str]
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    warnings: list[WarningResponse] = Field(default_factory=list)
    schema_version: str | None = None


class HypothesisResponse(APIModel):
    """Serialized RCA hypothesis."""

    hypothesis_id: str
    root_cause: str
    confidence: float
    evidence_ids: list[str]
    status: str
    rationale: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    neutral_evidence_ids: list[str] = Field(default_factory=list)
    validation_results: list[dict[str, Any]] = Field(default_factory=list)
    rank: int | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    schema_version: str | None = None


class ReportResponse(APIModel):
    """Serialized Markdown report."""

    report_id: str
    title: str
    markdown: str
    cited_evidence_ids: list[str]
    created_at: str
    schema_version: str | None = None


class LLMUsageEventResponse(APIModel):
    """Serialized LLM observability event."""

    call_id: str
    agent: str
    provider: str
    model: str
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0.0
    status: Literal["success", "failed"]
    schema_version: str | None = None


class RCAJobStateResponse(APIModel):
    """Complete serialized domain state returned by the job endpoint."""

    job: dict[str, Any]
    task_plan: TaskPlanResponse | None = None
    current_task_id: str | None = None
    completed_task_ids: list[str] = Field(default_factory=list)
    affected_lots: list[str] = Field(default_factory=list)
    impact_lots: list[str] = Field(default_factory=list)
    affected_wafers: list[str] = Field(default_factory=list)
    impact_wafers: list[str] = Field(default_factory=list)
    scope_level: str = "lot"
    impact_criteria: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    findings: list[AgentFindingResponse] = Field(default_factory=list)
    hypotheses: list[HypothesisResponse] = Field(default_factory=list)
    warnings: list[WarningResponse] = Field(default_factory=list)
    report: ReportResponse | None = None
    llm_usage: list[LLMUsageEventResponse] = Field(default_factory=list)
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    investigation_goal: dict[str, Any] | None = None
    capability_notices: list[CapabilityNoticeResponse] = Field(default_factory=list)
    investigation_questions: list[InvestigationQuestionResponse] = Field(
        default_factory=list
    )
    question_evidence_links: list[QuestionEvidenceLinkResponse] = Field(
        default_factory=list
    )
    action_history: list[dict[str, Any]] = Field(default_factory=list)
    planner_decisions: list[PlannerDecisionResponse] = Field(default_factory=list)
    question_update_reviews: list[QuestionUpdateReviewResponse] = Field(
        default_factory=list
    )
    run_evaluation: RunEvaluationResponse | None = None
    goal_status: str | None = None
    conclusion_level: str | None = None
    evidence_gaps: list[str] = Field(default_factory=list)
    stop_reason: str | None = None
    schema_version: str | None = None


class RCAJobResponse(APIModel):
    """Stored job status and complete serialized domain state."""

    job_id: str
    status: str
    state: RCAJobStateResponse


class RCAReportResponse(APIModel):
    """Stored Markdown report and its evidence references."""

    job_id: str
    status: str
    report: ReportResponse


class MemoryApprovalRequest(APIModel):
    """One named engineer decision for a pending memory candidate."""

    engineer_id: str = Field(min_length=1, max_length=100)
    engineer_role: Literal[
        "yield_engineer",
        "process_engineer",
        "equipment_engineer",
        "quality_engineer",
    ]
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)

    @field_validator("engineer_id")
    @classmethod
    def normalize_engineer_id(cls, value: str) -> str:
        stripped = value.strip().upper()
        if not stripped:
            raise ValueError("engineer_id must not be blank")
        return stripped

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        return value.strip()


class MemoryCandidateResponse(APIModel):
    """Candidate content, approval state, and publication identity."""

    candidate: dict[str, Any]
