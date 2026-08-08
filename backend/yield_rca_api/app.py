"""FastAPI application that wraps the proven pure Python RCA workflow."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from yield_rca_core.knowledge_ingestion import (
    KnowledgeCandidateNotFoundError,
    KnowledgeIngestionConflictError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
    KnowledgeStore,
)
from yield_rca_core.knowledge_lookup import KnowledgeLookupService
from yield_rca_core.knowledge_store import (
    InMemoryKnowledgeStore,
    PostgresKnowledgeStore,
    load_builtin_knowledge_store,
)
from yield_rca_core.llm_gateway import LLMCallError, LLMOutputValidationError
from yield_rca_core.models import RCAJob, RCAState, TaskStatus
from yield_rca_core.question_capability import QUESTION_CAPABILITY_REGISTRY
from yield_rca_core.supervisor import SupervisorExecutionError
from yield_rca_core.workflow import (
    PurePythonRCAWorkflow,
    build_csv_workflow,
    build_postgres_workflow,
)

from yield_rca_api.audit import (
    AuditEvent,
    AuditSink,
    InMemoryAuditSink,
    PostgresAuditSink,
)
from yield_rca_api.memory import (
    InMemoryMemoryStore,
    MemoryApprovalConflictError,
    MemoryApprovalService,
    MemoryApprovalValidationError,
    MemoryCandidateNotEligibleError,
    MemoryCandidateNotFoundError,
    MemoryStore,
    PostgresMemoryStore,
)
from yield_rca_api.observability import RCAMetrics, configure_logging
from yield_rca_api.schemas import (
    CreateRCAJobRequest,
    CreateRCAJobResponse,
    KnowledgeApprovalRequest,
    KnowledgeIngestionListResponse,
    KnowledgeIngestionResponse,
    KnowledgeLookupRequest,
    KnowledgeLookupResponse,
    MemoryApprovalRequest,
    MemoryCandidateResponse,
    RCAJobResponse,
    RCAJobStateResponse,
    RCAReportResponse,
    ReportResponse,
)
from yield_rca_api.store import (
    DuplicateJobError,
    InMemoryRCAJobStore,
    PostgresRCAJobStore,
    RCAJobStore,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DIR = PROJECT_ROOT / "data" / "seeds" / "golden_case"
DEFAULT_KNOWLEDGE_CORPUS = PROJECT_ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"
LOGGER = logging.getLogger("yield_rca_api")


def _state_api_payload(state: RCAState) -> dict[str, object]:
    """Project derived Question semantics into the public API response.

    The registry and links remain Python-owned facts.  The API merely groups
    them for product consumers; clients cannot submit these fields back to the
    Planner or mutate them through this projection.
    """

    payload: dict[str, object] = dict(state.to_dict())
    raw_links = payload.get("question_evidence_links", [])
    links = (
        [dict(item) for item in raw_links if isinstance(item, dict)]
        if isinstance(raw_links, list)
        else []
    )
    links_by_question: dict[str, list[dict[str, object]]] = {}
    for link in links:
        question_id = link.get("question_id")
        if isinstance(question_id, str):
            links_by_question.setdefault(question_id, []).append(link)

    raw_questions = payload.get("investigation_questions", [])
    if isinstance(raw_questions, list):
        for question in raw_questions:
            if not isinstance(question, dict):
                continue
            question_id = question.get("question_id")
            kind: object = question.get("question_kind")
            definition = QUESTION_CAPABILITY_REGISTRY.get(kind) if isinstance(kind, str) else None
            question_links = (
                links_by_question.get(question_id, []) if isinstance(question_id, str) else []
            )
            satisfied = {
                str(link["matched_evidence_group"])
                for link in question_links
                if link.get("relation") != "unavailable"
                and isinstance(link.get("matched_evidence_group"), str)
                and definition is not None
                and str(link["matched_evidence_group"]) in definition.closure_evidence_groups
            }
            required = set(definition.closure_evidence_groups) if definition else set()
            question["satisfied_evidence_groups"] = sorted(satisfied)
            question["missing_evidence_groups"] = sorted(required - satisfied)
            question["compatible_action_kinds"] = sorted(
                definition.allowed_actions if definition is not None else ()
            )
            question["evidence_links"] = question_links
    return dict(payload)


def _find_nested_error(
    error: BaseException,
    error_types: type[BaseException] | tuple[type[BaseException], ...],
) -> BaseException | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, error_types):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _is_llm_failure(error: BaseException) -> bool:
    return _find_nested_error(error, (LLMCallError, LLMOutputValidationError)) is not None


def _classify_workflow_error(error: BaseException) -> tuple[str, str, int]:
    llm_call_error = _find_nested_error(error, LLMCallError)
    if isinstance(llm_call_error, LLMCallError):
        if (llm_call_error.provider_code or "").casefold() == "arrearage":
            return (
                "LLM_BILLING_ERROR",
                "DashScope account billing is not in good standing. Settle the Model "
                "Studio account balance and retry.",
                status.HTTP_402_PAYMENT_REQUIRED,
            )
        if llm_call_error.status_code in {401, 403}:
            return (
                "LLM_AUTH_FAILED",
                "DashScope rejected the API credentials or model permission.",
                status.HTTP_502_BAD_GATEWAY,
            )
        if llm_call_error.status_code == 429:
            return (
                "LLM_RATE_LIMITED",
                "DashScope rate limit or quota was exceeded.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if llm_call_error.status_code is not None:
            diagnostic_parts = []
            if llm_call_error.provider_code:
                diagnostic_parts.append(f"code={llm_call_error.provider_code}")
            if llm_call_error.provider_message:
                diagnostic_parts.append(f"message={llm_call_error.provider_message}")
            if llm_call_error.request_id:
                diagnostic_parts.append(f"request_id={llm_call_error.request_id}")
            diagnostic = (
                f" Provider diagnostic: {'; '.join(diagnostic_parts)}." if diagnostic_parts else ""
            )
            return (
                "LLM_PROVIDER_ERROR",
                f"DashScope returned HTTP {llm_call_error.status_code}.{diagnostic}",
                status.HTTP_502_BAD_GATEWAY,
            )
        return (
            "LLM_UNAVAILABLE",
            "DashScope could not be reached or timed out.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if _find_nested_error(error, LLMOutputValidationError) is not None:
        return (
            "LLM_OUTPUT_INVALID",
            "Qwen returned output that violated the structured Agent contract.",
            status.HTTP_502_BAD_GATEWAY,
        )
    return (
        "WORKFLOW_EXECUTION_FAILED",
        "RCA workflow execution failed.",
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _llm_log_context(error: BaseException) -> dict[str, str]:
    llm_call_error = _find_nested_error(error, LLMCallError)
    if not isinstance(llm_call_error, LLMCallError):
        return {}
    context: dict[str, str] = {}
    if llm_call_error.provider_code:
        context["provider_code"] = llm_call_error.provider_code
    if llm_call_error.provider_message:
        context["provider_message"] = llm_call_error.provider_message
    if llm_call_error.request_id:
        context["provider_request_id"] = llm_call_error.request_id
    return context


def _default_workflow() -> PurePythonRCAWorkflow:
    database_url = os.getenv("YIELD_RCA_DATABASE_URL", "").strip()
    if database_url:
        return build_postgres_workflow(database_url)

    configured_seed_dir = os.getenv("YIELD_RCA_SEED_DIR", "").strip()
    seed_dir = Path(configured_seed_dir) if configured_seed_dir else DEFAULT_SEED_DIR
    if not seed_dir.is_absolute():
        seed_dir = PROJECT_ROOT / seed_dir
    return build_csv_workflow(seed_dir.resolve())


def _default_audit_sink() -> AuditSink:
    database_url = os.getenv("YIELD_RCA_DATABASE_URL", "").strip()
    return PostgresAuditSink(database_url) if database_url else InMemoryAuditSink()


def _default_memory_store(knowledge_store: KnowledgeStore | None = None) -> MemoryStore:
    database_url = os.getenv("YIELD_RCA_DATABASE_URL", "").strip()
    if database_url:
        return PostgresMemoryStore(database_url)
    return InMemoryMemoryStore(
        knowledge_store if isinstance(knowledge_store, InMemoryKnowledgeStore) else None
    )


def _default_knowledge_store() -> KnowledgeStore:
    database_url = os.getenv("YIELD_RCA_DATABASE_URL", "").strip()
    if database_url:
        return PostgresKnowledgeStore(database_url)
    configured_seed_dir = os.getenv("YIELD_RCA_SEED_DIR", "").strip()
    seed_dir = Path(configured_seed_dir) if configured_seed_dir else DEFAULT_SEED_DIR
    if not seed_dir.is_absolute():
        seed_dir = PROJECT_ROOT / seed_dir
    case_ids: set[str] = set()
    case_path = seed_dir / "rca_case.csv"
    if case_path.exists():
        import csv

        with case_path.open(newline="", encoding="utf-8") as handle:
            case_ids = {str(row["case_id"]) for row in csv.DictReader(handle) if row.get("case_id")}
    return load_builtin_knowledge_store(
        DEFAULT_KNOWLEDGE_CORPUS,
        additional_case_ids=case_ids,
    )


def _default_job_store() -> RCAJobStore:
    database_url = os.getenv("YIELD_RCA_DATABASE_URL", "").strip()
    return PostgresRCAJobStore(database_url) if database_url else InMemoryRCAJobStore()


def _default_runtime_dataset() -> str:
    configured_dataset = os.getenv("YIELD_RCA_DATASET", "").strip()
    if configured_dataset:
        return configured_dataset
    configured_seed_dir = os.getenv("YIELD_RCA_SEED_DIR", "").strip()
    if configured_seed_dir:
        return Path(configured_seed_dir).name
    return DEFAULT_SEED_DIR.name


def _controlled_react_eligibility(request: CreateRCAJobRequest) -> tuple[bool, str | None]:
    query = request.resolved_user_query().lower()
    if request.investigation_mode != "lot" or not request.lot_id:
        return False, "controlled_react_requires_lot_investigation"
    if not any(term in query for term in ("scratch", "缺陷", "划伤", "刮伤")):
        return False, "controlled_react_requires_explicit_defect_clue"
    return True, None


def create_app(
    *,
    workflow: PurePythonRCAWorkflow | None = None,
    store: RCAJobStore | None = None,
    audit_sink: AuditSink | None = None,
    memory_store: MemoryStore | None = None,
    knowledge_store: KnowledgeStore | None = None,
    metrics: RCAMetrics | None = None,
    runtime_dataset: str | None = None,
) -> FastAPI:
    """Create the HTTP adapter with injectable workflow and job storage."""

    rca_workflow = workflow or _default_workflow()
    job_store = store or _default_job_store()
    event_sink = audit_sink or _default_audit_sink()
    governed_knowledge_store = knowledge_store or _default_knowledge_store()
    memory_service = MemoryApprovalService(
        memory_store or _default_memory_store(governed_knowledge_store)
    )
    knowledge_ingestion_service = KnowledgeIngestionService(governed_knowledge_store)
    knowledge_lookup_service = KnowledgeLookupService(governed_knowledge_store)
    app_metrics = metrics or RCAMetrics()
    dataset_name = runtime_dataset or _default_runtime_dataset()
    configure_logging()
    application = FastAPI(
        title="Semiconductor Yield RCA API",
        version="0.1.0",
        description="HTTP adapter for the pure Python Yield RCA workflow.",
    )
    application.state.rca_workflow = rca_workflow
    application.state.job_store = job_store
    application.state.audit_sink = event_sink
    application.state.memory_service = memory_service
    application.state.knowledge_store = governed_knowledge_store
    application.state.knowledge_ingestion_service = knowledge_ingestion_service
    application.state.knowledge_lookup_service = knowledge_lookup_service
    application.state.metrics = app_metrics
    application.state.runtime_dataset = dataset_name

    def record_audit(event: AuditEvent) -> None:
        try:
            event_sink.record_event(event)
        except Exception:
            LOGGER.warning(
                "audit event write failed",
                extra={
                    "correlation_id": event.correlation_id,
                    "job_id": event.job_id,
                    "outcome": "failed",
                },
                exc_info=True,
            )

    def record_usage(state: RCAState, correlation_id: str) -> None:
        for usage in state.llm_usage:
            try:
                event_sink.record_llm_usage(
                    job_id=state.job.job_id,
                    correlation_id=correlation_id,
                    usage=usage,
                )
            except Exception:
                LOGGER.warning(
                    "LLM usage audit write failed",
                    extra={
                        "correlation_id": correlation_id,
                        "job_id": state.job.job_id,
                        "agent": usage.agent,
                        "outcome": "failed",
                    },
                    exc_info=True,
                )

    @application.middleware("http")
    async def correlation_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID") or f"CORR_{uuid4().hex.upper()}"
        request.state.correlation_id = correlation_id
        started = perf_counter()
        try:
            response = await call_next(request)
            outcome = "success" if response.status_code < 500 else "failed"
        except Exception:
            LOGGER.exception(
                "HTTP request failed",
                extra={"correlation_id": correlation_id, "outcome": "failed"},
            )
            raise
        response.headers["X-Correlation-ID"] = correlation_id
        LOGGER.info(
            "HTTP request completed",
            extra={
                "correlation_id": correlation_id,
                "duration_ms": round((perf_counter() - started) * 1000.0, 3),
                "outcome": outcome,
            },
        )
        return response

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @application.get("/ready")
    def ready() -> dict[str, str]:
        try:
            job_store.check_ready()
            governed_knowledge_store.check_ready()
        except Exception as exc:
            LOGGER.warning("readiness check failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "not_ready",
                    "reason": str(exc),
                    "dataset": dataset_name,
                },
            ) from exc
        return {
            "status": "ready",
            "agent_mode": rca_workflow.llm_settings.agent_mode,
            "model": rca_workflow.llm_settings.model,
            "dataset": dataset_name,
            "orchestration_mode": rca_workflow.orchestration_mode,
        }

    @application.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(
            generate_latest(app_metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @application.post(
        "/rca/jobs",
        response_model=CreateRCAJobResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_rca_job(
        request: CreateRCAJobRequest,
        http_request: Request,
    ) -> CreateRCAJobResponse:
        job_id = f"RCA_{uuid4().hex.upper()}"
        correlation_id = str(http_request.state.correlation_id)
        started = perf_counter()
        user_query = request.resolved_user_query()
        pending_state = RCAState(
            job=RCAJob(
                job_id=job_id,
                user_query=user_query,
                investigation_mode=request.investigation_mode,
                source_lot_id=request.lot_id,
                status=TaskStatus.RUNNING.value,
            ),
            execution_metadata={
                "agent_mode": rca_workflow.llm_settings.agent_mode,
                "provider": rca_workflow.llm_settings.provider,
                "model": rca_workflow.llm_settings.model,
            },
        )
        try:
            job_store.create(pending_state)
        except DuplicateJobError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        record_audit(
            AuditEvent(
                action="RCA_JOB_CREATED",
                job_id=job_id,
                correlation_id=correlation_id,
                outcome="success",
                details={
                    "investigation_mode": request.investigation_mode,
                    "source_lot_id": request.lot_id,
                    "agent_mode": rca_workflow.llm_settings.agent_mode,
                },
            )
        )

        try:
            fallback_reason: str | None = None
            if rca_workflow.orchestration_mode == "llm_react":
                selected_mode = "llm_react"
            elif rca_workflow.orchestration_mode == "controlled_react":
                eligible, fallback_reason = _controlled_react_eligibility(request)
                selected_mode = "controlled_react" if eligible else "fixed"
            else:
                selected_mode = "fixed"
            completed_state = rca_workflow.run(
                user_query,
                job_id=job_id,
                plan_id=f"PLAN_{job_id}",
                lot_id=request.lot_id,
                orchestration_mode_override=selected_mode,
            )
            execution_metadata = {
                **completed_state.execution_metadata,
                "orchestration_requested_mode": rca_workflow.orchestration_mode,
            }
            if fallback_reason and rca_workflow.orchestration_mode == "controlled_react":
                execution_metadata["orchestration_fallback_reason"] = fallback_reason
            completed_state = replace(
                completed_state,
                execution_metadata=execution_metadata,
            )
        except SupervisorExecutionError as exc:
            if _is_llm_failure(exc):
                error_code, error_message, error_status = _classify_workflow_error(exc)
            else:
                error_code = exc.error_code or "SUPERVISOR_EXECUTION_FAILED"
                error_message = str(exc)
                error_status = (
                    status.HTTP_404_NOT_FOUND
                    if exc.error_code == "LOT_NOT_FOUND"
                    else status.HTTP_422_UNPROCESSABLE_ENTITY
                    if exc.error_code
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            failed_state = exc.state or pending_state
            if failed_state.job.status != TaskStatus.FAILED.value:
                failed_state = replace(
                    failed_state,
                    job=replace(failed_state.job, status=TaskStatus.FAILED.value),
                )
            job_store.save(failed_state)
            app_metrics.observe_state(failed_state, outcome="failed")
            if _is_llm_failure(exc):
                app_metrics.observe_llm_error(
                    provider=rca_workflow.llm_settings.provider,
                    model=rca_workflow.llm_settings.model,
                )
            record_audit(
                AuditEvent(
                    action="RCA_JOB_FAILED",
                    job_id=job_id,
                    correlation_id=correlation_id,
                    outcome="failed",
                    details={"error_code": error_code},
                )
            )
            LOGGER.exception(
                "RCA Supervisor execution failed",
                extra={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "lot_id": request.lot_id,
                    "outcome": "failed",
                    "error_code": error_code,
                    "error_type": type(exc).__name__,
                    **_llm_log_context(exc),
                },
            )
            raise HTTPException(
                status_code=error_status,
                detail={
                    "job_id": job_id,
                    "error_code": error_code,
                    "message": error_message,
                },
            ) from exc
        except Exception as exc:
            error_code, error_message, http_status = _classify_workflow_error(exc)
            failed_state = replace(
                pending_state,
                job=replace(pending_state.job, status=TaskStatus.FAILED.value),
            )
            job_store.save(failed_state)
            app_metrics.observe_state(failed_state, outcome="failed")
            if _is_llm_failure(exc):
                app_metrics.observe_llm_error(
                    provider=rca_workflow.llm_settings.provider,
                    model=rca_workflow.llm_settings.model,
                )
            record_audit(
                AuditEvent(
                    action="RCA_JOB_FAILED",
                    job_id=job_id,
                    correlation_id=correlation_id,
                    outcome="failed",
                    details={"error_code": error_code},
                )
            )
            LOGGER.exception(
                "RCA workflow execution failed",
                extra={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "lot_id": request.lot_id,
                    "outcome": "failed",
                    "error_code": error_code,
                    "error_type": type(exc).__name__,
                    **_llm_log_context(exc),
                },
            )
            raise HTTPException(
                status_code=http_status,
                detail={
                    "job_id": job_id,
                    "error_code": error_code,
                    "message": error_message,
                },
            ) from exc

        job_store.save(completed_state)
        memory_candidate = None
        controlled_fast_path = completed_state.execution_metadata.get("orchestration_mode") in {
            "controlled_react",
            "llm_react",
        }
        try:
            if not controlled_fast_path:
                memory_candidate = memory_service.create_from_state(completed_state)
        except MemoryCandidateNotEligibleError:
            LOGGER.info(
                "RCA result is not eligible for controlled memory publication",
                extra={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "outcome": "success",
                },
            )
        if memory_candidate is not None:
            record_audit(
                AuditEvent(
                    action="MEMORY_CANDIDATE_CREATED",
                    job_id=job_id,
                    correlation_id=correlation_id,
                    outcome="success",
                    details={
                        "candidate_id": memory_candidate.candidate_id,
                        "scope_level": memory_candidate.scope_level,
                    },
                )
            )
        app_metrics.observe_state(completed_state, outcome="success")
        record_usage(completed_state, correlation_id)
        for tool_record in completed_state.execution_metadata.get("tool_latencies", []):
            if not isinstance(tool_record, dict):
                continue
            tool_request_id = str(tool_record.get("tool_request_id", ""))
            request_parts = tool_request_id.split(":")
            LOGGER.info(
                "Tool call completed",
                extra={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "agent": tool_record.get("agent"),
                    "task_id": request_parts[1] if len(request_parts) > 1 else None,
                    "tool_request_id": tool_request_id,
                    "duration_ms": tool_record.get("duration_ms"),
                    "outcome": tool_record.get("outcome"),
                },
            )
        for usage in completed_state.llm_usage:
            LOGGER.info(
                "LLM call completed",
                extra={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "agent": usage.agent,
                    "duration_ms": usage.latency_ms,
                    "outcome": usage.status,
                },
            )
        record_audit(
            AuditEvent(
                action="RCA_JOB_COMPLETED",
                job_id=job_id,
                correlation_id=correlation_id,
                outcome="success",
                details={
                    "agent_mode": completed_state.execution_metadata.get("agent_mode"),
                    "total_tokens": completed_state.execution_metadata.get("total_tokens", 0),
                    "duration_ms": round((perf_counter() - started) * 1000.0, 3),
                },
            )
        )
        LOGGER.info(
            "RCA job completed",
            extra={
                "correlation_id": correlation_id,
                "job_id": job_id,
                "lot_id": request.lot_id,
                "duration_ms": round((perf_counter() - started) * 1000.0, 3),
                "outcome": "success",
            },
        )
        return CreateRCAJobResponse(
            job_id=job_id,
            status=completed_state.job.status,
            created_at=completed_state.job.created_at,
            investigation_mode=completed_state.job.investigation_mode,
            source_lot_id=completed_state.job.source_lot_id,
            state_url=f"/rca/jobs/{job_id}",
            report_url=f"/rca/jobs/{job_id}/report",
            memory_candidate_id=(
                memory_candidate.candidate_id if memory_candidate is not None else None
            ),
            memory_candidate_url=(
                f"/memory/candidates/{memory_candidate.candidate_id}"
                if memory_candidate is not None
                else None
            ),
        )

    @application.get("/rca/jobs/{job_id}", response_model=RCAJobResponse)
    def get_rca_job(job_id: str) -> RCAJobResponse:
        state = job_store.get(job_id)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RCA job not found: {job_id}",
            )
        return RCAJobResponse(
            job_id=job_id,
            status=state.job.status,
            state=RCAJobStateResponse.model_validate(_state_api_payload(state)),
        )

    @application.get("/rca/jobs/{job_id}/report", response_model=RCAReportResponse)
    def get_rca_report(job_id: str, request: Request) -> RCAReportResponse:
        state = job_store.get(job_id)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RCA job not found: {job_id}",
            )
        if state.report is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"RCA report is not available for job: {job_id}",
            )
        record_audit(
            AuditEvent(
                action="RCA_REPORT_VIEWED",
                job_id=job_id,
                correlation_id=str(request.state.correlation_id),
                outcome="success",
            )
        )
        return RCAReportResponse(
            job_id=job_id,
            status=state.job.status,
            report=ReportResponse.model_validate(state.report.to_dict()),
        )

    @application.get(
        "/rca/jobs/{job_id}/memory-candidate",
        response_model=MemoryCandidateResponse,
    )
    def get_job_memory_candidate(job_id: str) -> MemoryCandidateResponse:
        if job_store.get(job_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"RCA job not found: {job_id}",
            )
        try:
            candidate = memory_service.get_by_job(job_id)
        except MemoryCandidateNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="RCA job has no eligible memory candidate",
            ) from exc
        return MemoryCandidateResponse(candidate=candidate.to_dict())

    @application.get(
        "/memory/candidates/{candidate_id}",
        response_model=MemoryCandidateResponse,
    )
    def get_memory_candidate(candidate_id: str) -> MemoryCandidateResponse:
        try:
            candidate = memory_service.get(candidate_id)
        except MemoryCandidateNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory candidate not found: {candidate_id}",
            ) from exc
        return MemoryCandidateResponse(candidate=candidate.to_dict())

    @application.post(
        "/memory/candidates/{candidate_id}/approvals",
        response_model=MemoryCandidateResponse,
    )
    def decide_memory_candidate(
        candidate_id: str,
        approval_request: MemoryApprovalRequest,
        request: Request,
    ) -> MemoryCandidateResponse:
        correlation_id = str(request.state.correlation_id)
        try:
            candidate = memory_service.decide(
                candidate_id=candidate_id,
                engineer_id=approval_request.engineer_id,
                engineer_role=approval_request.engineer_role,
                decision=approval_request.decision,
                comment=approval_request.comment,
                correlation_id=correlation_id,
            )
        except MemoryCandidateNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory candidate not found: {candidate_id}",
            ) from exc
        except MemoryApprovalConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except MemoryApprovalValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        return MemoryCandidateResponse(candidate=candidate.to_dict())

    def knowledge_error(error: KnowledgeIngestionError) -> HTTPException:
        if isinstance(error, KnowledgeCandidateNotFoundError):
            http_status = status.HTTP_404_NOT_FOUND
        elif isinstance(error, KnowledgeIngestionConflictError):
            http_status = status.HTTP_409_CONFLICT
        else:
            http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        return HTTPException(
            status_code=http_status,
            detail={"error_code": error.code, "message": str(error)},
        )

    @application.post(
        "/knowledge/lookups",
        response_model=KnowledgeLookupResponse,
    )
    def lookup_knowledge(request: KnowledgeLookupRequest) -> KnowledgeLookupResponse:
        try:
            result = knowledge_lookup_service.lookup(
                query=request.query,
                question_kind=request.question_kind,
                document_type=request.document_type,
                module=request.module,
                equipment_type=request.equipment_type,
                operation=request.operation,
                defect_type=request.defect_type,
                tags=request.tags,
                top_k=request.top_k,
            )
        except KnowledgeIngestionError as exc:
            raise knowledge_error(exc) from exc
        return KnowledgeLookupResponse.model_validate(result.to_dict())

    @application.post(
        "/knowledge/ingestions",
        response_model=KnowledgeIngestionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_knowledge_ingestion(
        file: Annotated[UploadFile, File()],
        document_type: Annotated[str, Form()],
        title: Annotated[str, Form(min_length=1, max_length=500)],
        module: Annotated[str, Form(min_length=1, max_length=200)],
        equipment_type: Annotated[str, Form(max_length=200)] = "",
        operation: Annotated[str, Form(max_length=200)] = "",
        defect_type: Annotated[str, Form(max_length=200)] = "",
        tags: Annotated[str, Form(max_length=2000)] = "",
        case_id: Annotated[str | None, Form(max_length=200)] = None,
    ) -> KnowledgeIngestionResponse:
        payload = file.file.read(knowledge_ingestion_service.parser.limits.max_file_bytes + 1)
        normalized_tags = tuple(
            dict.fromkeys(
                item.strip() for item in tags.replace(";", ",").split(",") if item.strip()
            )
        )
        try:
            candidate = knowledge_ingestion_service.ingest(
                filename=file.filename or "",
                content_type=file.content_type,
                payload=payload,
                document_type=document_type,
                title=title,
                module=module,
                equipment_type=equipment_type,
                operation=operation,
                defect_type=defect_type,
                tags=normalized_tags,
                case_id=case_id.strip() if case_id else None,
            )
        except KnowledgeIngestionError as exc:
            raise knowledge_error(exc) from exc
        return KnowledgeIngestionResponse.model_validate(
            {"candidate": candidate.to_dict(include_content=True)}
        )

    @application.get(
        "/knowledge/ingestions",
        response_model=KnowledgeIngestionListResponse,
    )
    def list_knowledge_ingestions(
        candidate_status: str | None = Query(default=None, alias="status"),
    ) -> KnowledgeIngestionListResponse:
        try:
            candidates = knowledge_ingestion_service.list(candidate_status)
        except KnowledgeIngestionError as exc:
            raise knowledge_error(exc) from exc
        return KnowledgeIngestionListResponse.model_validate(
            {"candidates": [item.to_dict(include_content=False) for item in candidates]}
        )

    @application.get(
        "/knowledge/ingestions/{candidate_id}",
        response_model=KnowledgeIngestionResponse,
    )
    def get_knowledge_ingestion(candidate_id: str) -> KnowledgeIngestionResponse:
        try:
            candidate = knowledge_ingestion_service.get(candidate_id)
        except KnowledgeIngestionError as exc:
            raise knowledge_error(exc) from exc
        return KnowledgeIngestionResponse.model_validate(
            {"candidate": candidate.to_dict(include_content=True)}
        )

    @application.post(
        "/knowledge/ingestions/{candidate_id}/approvals",
        response_model=KnowledgeIngestionResponse,
    )
    def decide_knowledge_ingestion(
        candidate_id: str,
        approval_request: KnowledgeApprovalRequest,
    ) -> KnowledgeIngestionResponse:
        try:
            candidate = knowledge_ingestion_service.decide(
                candidate_id=candidate_id,
                engineer_id=approval_request.engineer_id,
                engineer_role=approval_request.engineer_role,
                decision=approval_request.decision,
                comment=approval_request.comment,
            )
        except KnowledgeIngestionError as exc:
            raise knowledge_error(exc) from exc
        return KnowledgeIngestionResponse.model_validate(
            {"candidate": candidate.to_dict(include_content=True)}
        )

    return application


app = create_app()
