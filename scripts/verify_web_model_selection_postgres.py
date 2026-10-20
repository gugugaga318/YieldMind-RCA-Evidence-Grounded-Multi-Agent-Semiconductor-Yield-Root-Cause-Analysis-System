"""Real PostgreSQL W1 round-trip; mocked execution, no model/network providers.

Uses a unique disposable schema and existing queue DDL; never alters public tables.
Reads TEST_DATABASE_URL from the process or a named config file without loading env.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "backend")]

import httpx  # noqa: E402
import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.audit import InMemoryAuditSink  # noqa: E402
from yield_rca_api.memory import InMemoryMemoryStore, MemoryApprovalService  # noqa: E402
from yield_rca_api.observability import RCAMetrics  # noqa: E402
from yield_rca_api.store import PostgresRCAJobStore  # noqa: E402
from yield_rca_api.worker import RCAQueueWorker, WorkerSettings  # noqa: E402
from yield_rca_core.llm_gateway import LLMCallError, LLMSettings  # noqa: E402
from yield_rca_core.models import RCAJob, RCAState  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402


def verify(database_url: str) -> dict:
    environment_before = dict(os.environ)
    schema = "w1_roundtrip_" + uuid4().hex
    scoped_url = make_conninfo(database_url, options=f"-c search_path={schema}")
    defaults = LLMSettings(agent_mode="llm", api_key="offline-config-only")
    initial = build_csv_workflow(ROOT / "data/seeds/golden_case", llm_settings=defaults)
    built = []
    attempted = []
    retry_jobs = set()
    original_getenv = os.getenv

    def fixture_config(name, default=None):
        if name in {"DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"}:
            return "offline-config-only"
        return original_getenv(name, default)

    def factory(settings, mode):
        workflow = build_csv_workflow(
            ROOT / "data/seeds/golden_case", llm_settings=settings, orchestration_mode=mode
        )
        built.append((settings.provider, settings.model))

        def run(query, *, job_id, **kwargs):
            attempted.append((job_id, settings.provider, settings.model))
            if job_id in retry_jobs:
                retry_jobs.remove(job_id)
                raise LLMCallError("simulated transient; no HTTP", status_code=503)
            return RCAState(job=RCAJob(job_id=job_id, user_query=query, status="completed"))

        return SimpleNamespace(
            llm_settings=settings,
            orchestration_mode=mode,
            supervisor=workflow.supervisor,
            run=run,
        )

    def worker():
        # Each worker receives a new store and fresh connections, never the API store.
        return RCAQueueWorker(
            store=PostgresRCAJobStore(scoped_url),
            workflow=initial,
            job_workflow_factory=factory,
            runtime_dataset="golden_case",
            audit_sink=InMemoryAuditSink(),
            memory_service=MemoryApprovalService(InMemoryMemoryStore()),
            metrics=RCAMetrics(),
            worker_id="w1-" + uuid4().hex,
            settings=WorkerSettings(lease_seconds=60, heartbeat_seconds=10),
        )

    def record(job_id):
        return PostgresRCAJobStore(scoped_url).get_record(job_id)

    checks = []
    created = False
    try:
        with psycopg.connect(database_url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        with psycopg.connect(scoped_url) as connection:
            connection.execute("CREATE TABLE schema_migrations (version text PRIMARY KEY)")
            for name in ["005_runtime_resilience.up.sql", "011_async_job_queue.up.sql"]:
                connection.execute((ROOT / "db/migrations" / name).read_text())
        with (
            patch("yield_rca_api.model_selection.os.getenv", side_effect=fixture_config),
            patch.object(
                httpx.HTTPTransport,
                "handle_request",
                side_effect=AssertionError("Provider HTTP forbidden"),
            ),
        ):
            # TestClient uses its in-process transport; real HTTP transport is blocked.
            app = create_app(
                workflow=initial,
                store=PostgresRCAJobStore(scoped_url),
                runtime_dataset="golden_case",
                audit_sink=InMemoryAuditSink(),
                memory_store=InMemoryMemoryStore(),
            )
            with TestClient(app) as client:

                def submit(pair=None):
                    body = {"user_query": "Analyze the offline test product window."}
                    if pair:
                        body["model_selection"] = dict(
                            zip(["provider", "model"], pair, strict=True)
                        )
                    response = client.post("/rca/jobs", json=body)
                    assert response.status_code == 202, response.status_code
                    job_id = response.json()["job_id"]
                    with psycopg.connect(scoped_url) as connection:
                        request, config = connection.execute(
                            "SELECT request, runtime_config FROM rca_job_state WHERE job_id=%s",
                            (job_id,),
                        ).fetchone()
                    assert request.get("model_selection") == body.get("model_selection")
                    expected = pair or (defaults.provider, defaults.model)
                    assert (config["provider"], config["model"]) == expected
                    return job_id

                for pair in [("dashscope", "qwen-plus"), ("deepseek", "deepseek-v4-pro")]:
                    job = submit(pair)
                    assert worker().run_once()
                    saved = record(job)
                    assert saved.state.job.status == "completed"
                    assert built[-1] == pair
                    identity = saved.state.execution_metadata["model_selection"]
                    assert (identity["actual_provider"], identity["actual_model"]) == pair
                    checks.append({"scenario": "explicit", "pair": pair, "passed": True})

                job = submit(("deepseek", "deepseek-v4-pro"))
                retry_jobs.add(job)
                assert worker().run_once()
                assert record(job).state.job.status == "retry_wait"
                with psycopg.connect(scoped_url) as connection:
                    connection.execute(
                        "UPDATE rca_job_state SET next_attempt_at=now()-interval '1 second' "
                        "WHERE job_id=%s",
                        (job,),
                    )
                assert worker().run_once()
                saved = record(job)
                assert saved.state.job.status == "completed" and saved.attempt_count == 2
                assert [(p, m) for j, p, m in attempted if j == job] == [
                    ("deepseek", "deepseek-v4-pro")
                ] * 2
                checks.append({"scenario": "database_reload_retry", "passed": True})

                job = submit(("deepseek", "deepseek-v4-pro"))
                count = len(built)
                with psycopg.connect(scoped_url) as connection:
                    connection.execute(
                        "UPDATE rca_job_state SET runtime_config="
                        "jsonb_set(runtime_config, '{model}', %s::jsonb) WHERE job_id=%s",
                        (json.dumps("qwen-plus"), job),
                    )
                assert worker().run_once()
                saved = record(job)
                assert saved.state.job.status == "failed" and not saved.error["retryable"]
                assert len(built) == count
                checks.append(
                    {
                        "scenario": "tampered_pair",
                        "passed": True,
                        "error": saved.error["error_code"],
                    }
                )

                for legacy in [False, True]:
                    job = submit()
                    if legacy:
                        with psycopg.connect(scoped_url) as connection:
                            connection.execute(
                                "UPDATE rca_job_state SET "
                                "runtime_config=runtime_config-'model_selection', "
                                "state=state #- '{execution_metadata,model_selection}' "
                                "WHERE job_id=%s",
                                (job,),
                            )
                    assert worker().run_once()
                    saved = record(job)
                    assert saved.state.job.status == "completed"
                    assert built[-1] == (defaults.provider, defaults.model)
                    if legacy:
                        assert "model_selection" not in saved.state.execution_metadata
                    checks.append(
                        {
                            "scenario": "legacy_without_stamp" if legacy else "default_request",
                            "passed": True,
                        }
                    )
    finally:
        if created:
            with psycopg.connect(database_url) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        assert dict(os.environ) == environment_before, "process environment changed"
    return {
        "checks": checks,
        "database": "real PostgreSQL",
        "provider_http_calls": 0,
        "process_environment_unchanged": True,
        "isolated_schema_removed": True,
        "production_schema_changed": False,
        "model_execution": "mock run; real client assembly",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = os.getenv("TEST_DATABASE_URL", "")
    if args.database_config_file:
        for line in args.database_config_file.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("TEST_DATABASE_URL="):
                url = line.partition("=")[2].strip().strip("\"'")
    if not url:
        raise SystemExit("Test database configuration required; environment was not modified.")
    result = verify(url)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Real PostgreSQL round-trip: all six scenarios passed; zero provider HTTP calls.")


if __name__ == "__main__":
    main()
