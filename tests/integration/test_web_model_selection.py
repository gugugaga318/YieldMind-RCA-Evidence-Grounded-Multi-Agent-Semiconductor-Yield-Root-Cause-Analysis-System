# ruff: noqa: E402
"""Offline API-to-queue selection contracts. No live provider requests."""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "backend"), str(ROOT / "scripts")]

from fastapi.testclient import TestClient
from yield_rca_api.app import create_app
from yield_rca_api.store import InMemoryRCAJobStore
from yield_rca_core.llm_gateway import LLMSettings
from yield_rca_core.workflow import build_csv_workflow

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data/seeds/golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
KEYS = {"DASHSCOPE_API_KEY": "offline-qwen-key", "DEEPSEEK_API_KEY": "offline-deepseek-key"}


import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from yield_rca_api.audit import InMemoryAuditSink
from yield_rca_api.memory import InMemoryMemoryStore, MemoryApprovalService
from yield_rca_api.model_selection import ModelSelectionService, workflow_factory
from yield_rca_api.observability import RCAMetrics
from yield_rca_api.worker import RCAQueueWorker, WorkerSettings
from yield_rca_core import llm_gateway as gateway
from yield_rca_core.models import RCAState
from yield_rca_core.repositories import CsvFabRepository


def make_app(**kwargs):
    workflow = build_csv_workflow(
        SEED,
        llm_settings=LLMSettings(
            agent_mode="llm",
            api_key=KEYS["DASHSCOPE_API_KEY"],
        ),
    )
    return create_app(workflow=workflow, runtime_dataset="golden_case", **kwargs)


def test_catalog_only_exposes_approved_pairs_and_no_secret():
    with patch.dict("os.environ", KEYS), TestClient(make_app()) as client:
        response = client.get("/rca/model-options")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert [(p["provider"], m["model"]) for p in providers for m in p["models"]] == [
        ("dashscope", "qwen-plus"),
        ("deepseek", "deepseek-v4-pro"),
    ]
    assert all(key not in response.text for key in KEYS.values())


def test_explicit_selection_survives_enqueue():
    store = InMemoryRCAJobStore()
    with patch.dict("os.environ", KEYS), TestClient(make_app(store=store)) as client:
        response = client.post(
            "/rca/jobs",
            json={
                "user_query": QUERY,
                "model_selection": {
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                },
            },
        )
    assert response.status_code == 202
    record = store.get_record(response.json()["job_id"])
    assert record.runtime_config["provider"] == "deepseek"
    assert record.runtime_config["model"] == "deepseek-v4-pro"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("Live network forbidden in W1 tests")

    original_connect = socket.socket.connect

    def local_event_loop_only(sock, address):
        # Windows asyncio socketpair uses loopback for its internal wakeup pipe.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return forbidden()

    monkeypatch.setattr(socket.socket, "connect", local_event_loop_only)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def offline_wire(monkeypatch):
    """Real Runtime/Adapters, deterministic response generation; zero network."""
    response_context = ContextVar("w1_response")
    original = gateway.RuntimeLLMClient.complete_json
    calls = []
    clients = []
    barrier = {"value": None}

    def complete(client, request):
        # Isolate fake usage; only the real Adapter's reported usage reaches State.
        with gateway.capture_llm_usage() as fake_usage:
            generated = gateway.FakeLLMClient().complete_json(request)
            fake_usage.clear()  # Nested capture otherwise forwards fake usage to its parent.
        token = response_context.set((client.provider, client.model, generated.data))
        if client not in clients:
            clients.append(client)
            if barrier["value"]:
                barrier["value"].wait(timeout=15)
        try:
            return original(client, request)
        finally:
            response_context.reset(token)

    def post(client, url, **kwargs):
        provider, model, data = response_context.get()
        body = kwargs["json"]
        expected_host = "api.deepseek.com" if provider == "deepseek" else "dashscope.aliyuncs.com"
        assert expected_host in str(url)
        assert body["model"] == model
        key = KEYS["DEEPSEEK_API_KEY" if provider == "deepseek" else "DASHSCOPE_API_KEY"]
        assert kwargs["headers"]["Authorization"] == f"Bearer {key}"
        calls.append((provider, model))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "model": model,
                "choices": [{"message": {"content": json.dumps(data)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setattr(gateway.RuntimeLLMClient, "complete_json", complete)
    # Patch after API requests, or use TestClient.request instead of inherited post.
    monkeypatch.setattr(httpx.Client, "post", post)
    return SimpleNamespace(calls=calls, clients=clients, barrier=barrier)


def submit(client, selection=None, key=None):
    body = {"user_query": QUERY}
    if selection is not None:
        body["model_selection"] = {"provider": selection[0], "model": selection[1]}
    response = client.request(
        "POST", "/rca/jobs", json=body, headers={"Idempotency-Key": key} if key else {}
    )
    assert response.status_code in {201, 202}, response.text
    return response.json()["job_id"]


def make_worker(store, factory=None, defaults=None, worker_id="worker-selection"):
    initial = build_csv_workflow(
        SEED,
        llm_settings=defaults
        or LLMSettings(
            agent_mode="llm",
            api_key=KEYS["DASHSCOPE_API_KEY"],
        ),
    )
    return RCAQueueWorker(
        store=store,
        workflow=initial,
        job_workflow_factory=factory,
        audit_sink=InMemoryAuditSink(),
        memory_service=MemoryApprovalService(InMemoryMemoryStore()),
        metrics=RCAMetrics(),
        runtime_dataset="golden_case",
        worker_id=worker_id,
        settings=WorkerSettings(
            lease_seconds=60,
            heartbeat_seconds=10,
            poll_seconds=0.01,
            recovery_seconds=10,
            retry_base_seconds=0.001,
        ),
    )


@pytest.mark.parametrize(
    "selection", [None, ("dashscope", "qwen-plus"), ("deepseek", "deepseek-v4-pro")]
)
def test_api_queue_worker_real_runtime_identity(selection, offline_wire):
    store = InMemoryRCAJobStore()
    factory = workflow_factory(CsvFabRepository(SEED))
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, selection)
        pending = client.get(f"/rca/jobs/{job}").json()["state"]
        assert pending["execution_metadata"]["model_selection"]["actual_provider"] is None
        assert make_worker(store, factory).run_once()
        state = client.get(f"/rca/jobs/{job}").json()["state"]
    expected = selection or ("dashscope", "qwen-plus")
    assert state["job"]["status"] == "completed"
    assert set(offline_wire.calls) == {expected}
    identity = state["execution_metadata"]["model_identity"]
    assert (identity["provider"], identity["model"]) == expected
    binding = state["execution_metadata"]["model_selection"]
    assert (binding["actual_provider"], binding["actual_model"]) == expected
    assert binding["identity_match"] is True
    assert binding["requested_provider"] == (selection[0] if selection else None)
    assert RCAState.from_dict(store.get(job).to_dict()).to_dict() == store.get(job).to_dict()
    assert len(offline_wire.clients) == 1


@pytest.mark.parametrize(
    "selection",
    [
        {"provider": "invalid", "model": "qwen-plus"},
        {"provider": "dashscope", "model": "invalid"},
        {"provider": "dashscope", "model": "deepseek-v4-pro"},
        {"provider": "deepseek", "model": "qwen-plus"},
        {"provider": "deepseek", "model": "deepseek-flash"},
        {"provider": "qwen", "model": "qwen-plus"},
        {"provider": "deepseek"},
    ],
)
def test_invalid_pairs_rejected(selection):
    with patch.dict(os.environ, KEYS), TestClient(make_app()) as client:
        response = client.request(
            "POST", "/rca/jobs", json={"user_query": QUERY, "model_selection": selection}
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "base_url",
        "endpoint",
        "timeout",
        "retry",
        "temperature",
        "context_limit",
        "params",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_forbidden_inputs_not_accepted_or_echoed(field, nested):
    payload = {
        "user_query": QUERY,
        "model_selection": {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
        },
    }
    (payload["model_selection"] if nested else payload)[field] = "private-value-must-not-echo"
    with patch.dict(os.environ, KEYS), TestClient(make_app()) as client:
        response = client.request("POST", "/rca/jobs", json=payload)
    assert response.status_code == 422
    assert "private-value-must-not-echo" not in response.text


def test_missing_key_is_configuration_error_without_call():
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), TestClient(make_app()) as client:
        catalog = client.get("/rca/model-options").json()
        option = catalog["providers"][1]
        assert option["configured"] is False
        assert option["models"][0]["available"] is False
        response = client.request(
            "POST",
            "/rca/jobs",
            json={
                "user_query": QUERY,
                "model_selection": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            },
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "MODEL_PROVIDER_NOT_CONFIGURED"


@pytest.mark.parametrize("change", ["pair", "snapshot", "request", "params", "version"])
def test_worker_revalidates_tampered_persistence(change, offline_wire):
    from copy import deepcopy

    store = InMemoryRCAJobStore()
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        record = store.get_record(job)
        config = deepcopy(record.runtime_config)
        request = deepcopy(record.request)
        if change == "pair":
            config["model"] = "qwen-plus"
        if change == "snapshot":
            config["model_selection"]["requested_model"] = "qwen-plus"
        if change == "request":
            request["model_selection"]["provider"] = "dashscope"
        if change == "params":
            config["base_url"] = "https://untrusted.invalid"
        if change == "version":
            config["model_selection"]["catalog_version"] = "unknown"
        # New queue emulates a manually edited/non-Web producer record.
        altered = InMemoryRCAJobStore()
        altered.enqueue(replace(record, runtime_config=config, request=request))
        w = make_worker(altered, workflow_factory(CsvFabRepository(SEED)))
        w.run_once()
        failed = altered.get_record(job)
    assert failed.state.job.status == "failed"
    assert failed.error["retryable"] is False
    assert not offline_wire.calls


def test_queue_retry_keeps_provider_after_server_default_changes(offline_wire):
    from datetime import UTC, datetime, timedelta

    store = InMemoryRCAJobStore()
    factory = workflow_factory(CsvFabRepository(SEED))
    built = []

    def failing_first(settings, mode):
        w = factory(settings, mode)
        built.append((settings.provider, settings.model))
        if len(built) == 1:

            def fail(*args, **kwargs):
                raise gateway.LLMCallError("offline transient", status_code=503)

            return SimpleNamespace(
                llm_settings=settings, orchestration_mode=mode, supervisor=w.supervisor, run=fail
            )
        return w

    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        make_worker(store, failing_first).run_once()
        record = store.get_record(job)
        assert record.state.job.status == "retry_wait"
        assert record.state.execution_metadata["model_selection"]["actual_provider"] == "deepseek"
        # Advance retry scheduling deterministically, without sleeping or changing identity.
        altered = InMemoryRCAJobStore()
        altered.enqueue(
            replace(record, next_attempt_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        )
        changed_default = LLMSettings(
            agent_mode="llm",
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key=KEYS["DEEPSEEK_API_KEY"],
        )
        make_worker(altered, failing_first, defaults=changed_default).run_once()
        done = altered.get_record(job)
    assert done.state.job.status == "completed"
    assert built == [("deepseek", "deepseek-v4-pro")] * 2
    assert done.attempt_count == 2
    assert done.state.execution_metadata["model_selection"]["actual_provider"] == "deepseek"


def test_interleaved_jobs_do_not_share_clients_or_environment(offline_wire):
    store = InMemoryRCAJobStore()
    factory = workflow_factory(CsvFabRepository(SEED))
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        qwen = submit(client, ("dashscope", "qwen-plus"))
        deepseek = submit(client, ("deepseek", "deepseek-v4-pro"))
        before = dict(os.environ)
        workers = [make_worker(store, factory, worker_id=f"parallel-{i}") for i in range(2)]
        offline_wire.barrier["value"] = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(pool.map(lambda w: w.run_once(), workers))
        assert dict(os.environ) == before
    for job, provider in [(qwen, "dashscope"), (deepseek, "deepseek")]:
        state = store.get(job)
        assert state.job.status == "completed"
        assert state.execution_metadata["model_identity"]["provider"] == provider
        assert all(usage.provider == provider for usage in state.llm_usage)
    assert len(offline_wire.clients) == 2
    assert {c.provider for c in offline_wire.clients} == {"dashscope", "deepseek"}


def test_idempotency_hash_includes_selection_and_old_shape_is_preserved():
    from yield_rca_api.app import _normalized_job_request
    from yield_rca_api.schemas import CreateRCAJobRequest

    old = _normalized_job_request(CreateRCAJobRequest(user_query=QUERY))
    assert set(old) == {"investigation_mode", "user_query", "lot_id"}
    with patch.dict(os.environ, KEYS), TestClient(make_app()) as client:
        first = submit(client, ("dashscope", "qwen-plus"), "same")
        assert submit(client, ("dashscope", "qwen-plus"), "same") == first
        response = client.request(
            "POST",
            "/rca/jobs",
            headers={"Idempotency-Key": "same"},
            json={
                "user_query": QUERY,
                "model_selection": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            },
        )
    assert response.status_code == 409


def test_inline_uses_same_resolver_and_actual_identity(offline_wire):
    factory = workflow_factory(CsvFabRepository(SEED))
    with (
        patch.dict(os.environ, KEYS),
        TestClient(
            make_app(
                execute_jobs_inline=True,
                job_workflow_factory=factory,
            )
        ) as client,
    ):
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        state = client.get(f"/rca/jobs/{job}").json()["state"]
    assert state["execution_metadata"]["model_selection"]["identity_match"] is True
    assert state["execution_metadata"]["model_identity"]["provider"] == "deepseek"


def test_default_non_catalog_server_configuration_is_not_a_web_option():
    # Historical server configuration remains trusted; Web cannot request this pair.
    defaults = LLMSettings(agent_mode="llm", model="qwen-legacy", api_key="server-only")
    service = ModelSelectionService(defaults, orchestration_mode="fixed", dataset="golden_case")
    config = service.freeze(None)
    assert service.resolve(config, {"user_query": QUERY}).model == "qwen-legacy"
    from yield_rca_api.model_selection import ModelSelectionError

    with pytest.raises(ModelSelectionError):
        service.freeze({"provider": "dashscope", "model": "qwen-legacy"})


def test_default_job_stays_frozen_when_worker_default_changes(offline_wire):
    store = InMemoryRCAJobStore()
    factory = workflow_factory(CsvFabRepository(SEED))
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client)
        changed = LLMSettings(
            agent_mode="llm",
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key=KEYS["DEEPSEEK_API_KEY"],
        )
        make_worker(store, factory, defaults=changed).run_once()
    assert store.get(job).job.status == "completed"
    assert store.get(job).execution_metadata["model_identity"]["provider"] == "dashscope"
    assert set(offline_wire.calls) == {("dashscope", "qwen-plus")}


def test_configuration_drift_is_not_silently_applied(offline_wire):
    store = InMemoryRCAJobStore()
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        changed = LLMSettings(agent_mode="llm", api_key=KEYS["DASHSCOPE_API_KEY"], max_retries=9)
        make_worker(store, workflow_factory(CsvFabRepository(SEED)), defaults=changed).run_once()
    record = store.get_record(job)
    assert record.error["error_code"] == "WORKER_RUNTIME_CONFIG_MISMATCH"
    assert not offline_wire.calls


def test_worker_rechecks_missing_credential(offline_wire):
    store = InMemoryRCAJobStore()
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            make_worker(store, workflow_factory(CsvFabRepository(SEED))).run_once()
    record = store.get_record(job)
    assert record.error["error_code"] == "MODEL_PROVIDER_NOT_CONFIGURED"
    assert record.state.execution_metadata["model_selection"]["actual_provider"] is None
    assert not offline_wire.calls


def test_actual_mismatch_is_preserved_and_fails_without_call(offline_wire):
    store = InMemoryRCAJobStore()
    real_factory = workflow_factory(CsvFabRepository(SEED))

    def wrong_client(settings, mode):
        wrong = replace(
            settings,
            provider="dashscope",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=KEYS["DASHSCOPE_API_KEY"],
        )
        return replace(real_factory(wrong, mode), llm_settings=settings)

    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        make_worker(store, wrong_client).run_once()
    record = store.get_record(job)
    assert record.error["error_code"] == "MODEL_IDENTITY_MISMATCH"
    binding = record.state.execution_metadata["model_selection"]
    assert binding["requested_provider"] == "deepseek"
    assert binding["actual_provider"] == "dashscope"
    assert binding["identity_match"] is False
    assert not offline_wire.calls


def test_legacy_state_does_not_gain_fabricated_identity():
    from yield_rca_api.model_selection import attach_selection
    from yield_rca_core.models import RCAJob

    old = RCAState(job=RCAJob(job_id="legacy", user_query=QUERY))
    assert attach_selection(old, {"provider": "dashscope", "model": "qwen-plus"}) == old
    assert old.execution_metadata.get("model_identity") is None


def test_unqualified_capabilities_fail_before_enqueue(monkeypatch):
    from yield_rca_core.model_contracts import ModelCapabilities
    from yield_rca_core.providers.deepseek import DeepSeekAdapter

    monkeypatch.setattr(DeepSeekAdapter, "capabilities", ModelCapabilities())
    with patch.dict(os.environ, KEYS), TestClient(make_app()) as client:
        response = client.request(
            "POST",
            "/rca/jobs",
            json={
                "user_query": QUERY,
                "model_selection": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            },
        )
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "MODEL_CONFIGURATION_INVALID"


def test_legacy_deterministic_default_completes_without_model(offline_wire):
    store = InMemoryRCAJobStore()
    workflow = build_csv_workflow(SEED, llm_settings=LLMSettings())
    with TestClient(
        create_app(workflow=workflow, store=store, runtime_dataset="golden_case")
    ) as client:
        job = submit(client)
        make_worker(
            store, workflow_factory(CsvFabRepository(SEED)), defaults=LLMSettings()
        ).run_once()
    assert store.get(job).job.status == "completed"
    assert not offline_wire.calls
    assert store.get(job).execution_metadata["model_selection"]["actual_provider"] is None


def test_retry_preflight_does_not_trust_previous_state_identity():
    from yield_rca_api.model_selection import ModelSelectionError, verify_execution_identity
    from yield_rca_core.models import RCAJob

    defaults = LLMSettings(agent_mode="llm", api_key=KEYS["DASHSCOPE_API_KEY"])
    service = ModelSelectionService(defaults, orchestration_mode="fixed", dataset="golden_case")
    with patch.dict(os.environ, KEYS):
        config = service.freeze({"provider": "deepseek", "model": "deepseek-v4-pro"})
    old_actual = {
        "author": "model",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "basis": "runtime_config",
    }
    previous = RCAState(
        job=RCAJob(job_id="retry-identity", user_query=QUERY),
        execution_metadata={"model_identity": old_actual},
    )
    wrong = {**old_actual, "provider": "dashscope", "model": "qwen-plus"}
    with pytest.raises(ModelSelectionError, match="MODEL_IDENTITY_MISMATCH") as caught:
        verify_execution_identity(previous, config, wrong)
    assert (
        caught.value.state.execution_metadata["model_selection"]["actual_provider"] == "dashscope"
    )


def test_cancelled_attempt_preserves_binding_event_without_publishing_result(offline_wire):
    store = InMemoryRCAJobStore()
    factory = workflow_factory(CsvFabRepository(SEED))

    def cancelling_factory(settings, mode):
        w = factory(settings, mode)

        def run(*args, **kwargs):
            result = w.run(*args, **kwargs)
            store.request_cancel(kwargs["job_id"])
            return result

        return SimpleNamespace(
            llm_settings=settings, orchestration_mode=mode, supervisor=w.supervisor, run=run
        )

    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        make_worker(store, cancelling_factory).run_once()
    state = store.get(job)
    assert state.job.status == "cancelled"
    assert not state.evidence
    binding = next(e for e in store.list_events(job) if e.event_type == "investigation_model_bound")
    assert binding.payload["model_selection"]["actual_provider"] == "deepseek"
    assert state.execution_metadata["model_selection"]["requested_provider"] == "deepseek"


def test_postgres_json_record_roundtrip_keeps_selection_and_no_credentials():
    from yield_rca_api.store import PostgresRCAJobStore

    store = InMemoryRCAJobStore()
    with patch.dict(os.environ, KEYS), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
    record = store.get_record(job)
    values = {}

    def execute(sql, params):
        values["sql"], values["params"] = sql, params

    PostgresRCAJobStore._insert(SimpleNamespace(execute=execute), record)
    params = values["params"]
    assert json.loads(params[3]) == record.request
    assert json.loads(params[6]) == record.runtime_config
    restored = PostgresRCAJobStore._record_from_row(
        ["state", "request", "request_hash", "runtime_config"],
        [params[2], params[3], params[4], params[6]],
    )
    assert restored.runtime_config == record.runtime_config
    assert restored.request == record.request
    assert all(key not in repr(params) for key in KEYS.values())


def test_worker_bootstrap_supplies_job_factory(monkeypatch, offline_wire):
    import run_rca_worker as bootstrap

    store = InMemoryRCAJobStore()
    monkeypatch.setattr(bootstrap, "PostgresFabRepository", lambda url: CsvFabRepository(SEED))
    monkeypatch.setattr(bootstrap, "build_knowledge_retriever", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "PostgresRCAJobStore", lambda url: store)
    monkeypatch.setattr(bootstrap, "PostgresAuditSink", lambda url: InMemoryAuditSink())
    monkeypatch.setattr(bootstrap, "PostgresMemoryStore", lambda url: InMemoryMemoryStore())
    env = {
        **KEYS,
        "YIELD_RCA_AGENT_MODE": "llm",
        "YIELD_RCA_LLM_PROVIDER": "dashscope",
        "YIELD_RCA_LLM_MODEL": "qwen-plus",
        "YIELD_RCA_ORCHESTRATION_MODE": "fixed",
        "YIELD_RCA_DATASET": "golden_case",
    }
    with patch.dict(os.environ, env), TestClient(make_app(store=store)) as client:
        job = submit(client, ("deepseek", "deepseek-v4-pro"))
        worker = bootstrap.build_worker("offline-placeholder")
        assert worker.job_workflow_factory is not None
        worker.run_once()
    assert store.get(job).execution_metadata["model_identity"]["provider"] == "deepseek"


def test_nonweb_explicit_selection_cannot_bypass_server_execution_mode():
    from yield_rca_api.model_selection import ModelSelectionError

    service = ModelSelectionService(
        LLMSettings(agent_mode="fake"), orchestration_mode="fixed", dataset="golden_case"
    )
    config = service.freeze(None)
    config["model_selection"].update(
        source="explicit", requested_provider="dashscope", requested_model="qwen-plus"
    )
    with pytest.raises(ModelSelectionError, match="MODEL_SELECTION_REQUIRES_LLM_MODE"):
        service.resolve(
            config, {"model_selection": {"provider": "dashscope", "model": "qwen-plus"}}
        )


def test_qwen_capability_mismatch_is_rejected_before_execution(monkeypatch):
    from yield_rca_api.model_selection import ModelSelectionError
    from yield_rca_core.model_contracts import ModelCapabilities
    from yield_rca_core.providers.qwen import QwenAdapter

    service = ModelSelectionService(
        LLMSettings(agent_mode="llm", api_key="offline"),
        orchestration_mode="fixed",
        dataset="golden_case",
    )
    monkeypatch.setattr(QwenAdapter, "capabilities", ModelCapabilities(output_modes=frozenset()))
    with (
        patch.dict(os.environ, KEYS),
        pytest.raises(ModelSelectionError, match="MODEL_CONFIGURATION_INVALID"),
    ):
        service.freeze({"provider": "dashscope", "model": "qwen-plus"})
