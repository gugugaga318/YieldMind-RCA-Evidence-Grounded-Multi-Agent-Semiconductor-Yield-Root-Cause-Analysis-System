"""Dedicated-process Pilot dispatch. Dry-run follows the same path up to HTTP send.

No CLI paid entry point: review the infrastructure patch before any real dispatch.
Frozen formal runner is adapted only at settings/metadata injection boundaries.
"""

from __future__ import annotations

import json
import socket
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from benchmark_execution import observe_once, verify_freeze
from benchmark_observer import runtime_read_guard
from pilot_execution_binding import (
    export_manifest,
    file_hash,
    read_json,
    validate_authorization,
    validate_context,
)
from pilot_export import run_envelope
from yield_rca_core.llm_gateway import LLMSettings

ENDPOINTS = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com",
}


class DryRunBoundary(BaseException):
    """Not a model failure; must bypass production caller repair/retry handlers."""


class InfrastructureStop(BaseException):
    """Fail closed without turning infrastructure errors into model retries."""


def settings_for(manifest, planned, api_key):
    c = manifest["config"]
    return LLMSettings(
        agent_mode=c["agent_mode"],
        provider=planned["provider"],
        model=planned["model"],
        base_url=ENDPOINTS[planned["provider"]],
        api_key=api_key,
        timeout_seconds=c["timeout_seconds_per_http_phase"],
        max_retries=c["http_max_retries"],
        min_context_tokens=c["min_context_tokens"],
    )


def dispatch(context, manifest, authorization, *, repo, public, output, ledger, api_key=""):
    """Dispatch one next frozen run; never retry, resume or reorder a run.

    Must be called in a disposable single-thread process with no scorer GT loaded.
    Dry runs always dispatch the first manifest row into a separate diagnostic dir.
    """
    import run_formal_blind_rca as runner

    data = validate_context(context, manifest, authorization)
    dry_run = data["dry_run"]
    repo, public, output, ledger = map(Path, (repo, public, output, ledger))
    verify_freeze(manifest, repo, public)
    for name, digest in data["launch_file_hashes"].items():
        if file_hash(repo / name) != digest:
            raise ValueError("Infrastructure drift after preflight")
    if dry_run:
        planned = manifest["runs"][0]
    else:
        if data["draft_infrastructure"]:
            raise ValueError("Uncommitted launch infrastructure cannot execute paid runs")
        entries = sorted(ledger.glob("*.json")) if ledger.exists() else []
        if len(entries) >= len(manifest["runs"]):
            raise ValueError("Authorized runs exhausted")
        for index, entry in enumerate(entries):
            prior = json.loads(entry.read_text(encoding="utf-8"))
            if prior != manifest["runs"][index]:
                raise ValueError("Execution order ledger differs")
            if not (ledger / (entry.stem + ".complete")).exists():
                raise ValueError("Previous dispatch incomplete; integrity review required")
        planned = manifest["runs"][len(entries)]
    settings = settings_for(manifest, planned, "dry-run-placeholder" if dry_run else api_key)
    pricing = read_json(repo / "docs/model_benchmark/phase05/pricing_snapshot.json")
    from benchmark_observer import sha

    if sha(pricing) != manifest["pricing_hash"]:
        raise ValueError("Pricing snapshot drift")
    price = next(
        p
        for p in pricing["models"]
        if (p["provider"], p["model"]) == (planned["provider"], planned["model"])
    )
    envelope = run_envelope(
        context,
        manifest,
        authorization,
        planned,
        price["snapshot_id"],
        dry_run=dry_run,
    )
    view = export_manifest(context, manifest, authorization)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in (
        ("execution_context.json", data),
        ("launch_manifest.json", view),
        ("authorization.json", authorization),
        ("dispatch_envelope.json", envelope.model_dump(mode="json")),
    ):
        (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    latch = None
    if not dry_run:
        ledger.mkdir(parents=True, exist_ok=True)
        latch = ledger / f"{planned['order_index']:02d}.json"
        with latch.open("x", encoding="utf-8") as f:
            json.dump(planned, f)
    args = Namespace(
        public_dir=public,
        output_dir=output / "raw",
        evaluation_role="development_regression",
        case_id=[planned["case_id"]],
        orchestration_mode=manifest["config"]["orchestration_mode"],
        acceptance_contract="model_contract",
        confirm_paid_model=True,
        confirm_paid_qwen=False,
        max_llm_calls_per_case=manifest["config"]["max_logical_calls"],
        overwrite=False,
        challenge_diagnostics=True,
        challenge_response_json=False,
    )
    boundary = {"http_send_boundaries": 0, "provider_http_sent": 0}
    original_send = httpx.Client.send

    def send(client, request, *a, **kw):
        try:
            validate_authorization(manifest, authorization)
            verify_freeze(manifest, repo, public)
            for name, digest in data["launch_file_hashes"].items():
                if file_hash(repo / name) != digest:
                    raise ValueError("Infrastructure drift")
            payload = json.loads(request.content)
            if (
                str(request.url) != settings.base_url.rstrip("/") + "/chat/completions"
                or payload.get("model") != planned["model"]
            ):
                raise ValueError("Provider/model HTTP binding mismatch")
        except Exception as exc:
            raise InfrastructureStop(type(exc).__name__) from exc
        boundary["http_send_boundaries"] += 1
        if dry_run:
            raise DryRunBoundary()
        boundary["provider_http_sent"] += 1
        return original_send(client, request, *a, **kw)

    def no_network(*a, **kw):
        raise InfrastructureStop("Dry-run network escape")

    def execute():
        with runtime_read_guard():
            # Explicit negative probe: no GT file is opened or read.
            try:
                (repo / ".blind_evaluation/benchmark_scoring_v1_1/guard_probe.json").read_text()
            except PermissionError:
                boundary["gt_read_blocked"] = True
            else:
                raise InfrastructureStop("GT guard missing")
            return runner.run_formal_blind(args)

    network_guard = (
        patch.object(socket.socket, "connect", no_network)
        if dry_run
        else patch.object(socket.socket, "connect", socket.socket.connect)
    )
    result = None
    try:
        with (
            patch.object(
                runner,
                "_code_snapshot",
                lambda: (data["execution_head"], data["working_tree_clean"]),
            ),
            patch.object(runner, "LLMSettings", SimpleNamespace(from_env=lambda: settings)),
            patch.object(httpx.Client, "send", send),
            network_guard,
        ):
            result = observe_once(
                execute, output / "observation", manifest=view, repo=repo, public=public
            )
    except DryRunBoundary:
        if not dry_run:
            raise
        boundary["stopped_before_provider_http"] = True
    finally:
        (output / "dispatch_status.json").write_text(
            json.dumps(boundary, indent=2) + "\n", encoding="utf-8"
        )
    if dry_run:
        if (
            boundary.get("stopped_before_provider_http") is not True
            or boundary["provider_http_sent"]
        ):
            raise ValueError("Dry-run did not reach the HTTP dispatch boundary")
    elif latch is not None:
        latch.with_suffix(".complete").touch(exist_ok=False)
    return boundary if dry_run else result


def launch(
    *,
    repo,
    public,
    manifest_file,
    authorization_file,
    approval_file,
    reference_file,
    output,
    ledger,
    dry_run=True,
    api_key="",
):
    """Single trusted entry: preflight always precedes initialization and dispatch."""
    from pilot_execution_binding import preflight, read_json

    manifest = read_json(manifest_file)
    authorization = read_json(authorization_file)
    approval = read_json(approval_file)
    context = preflight(
        repo, public, manifest, authorization, approval, reference_file, dry_run=dry_run
    )
    return dispatch(
        context,
        manifest,
        authorization,
        repo=repo,
        public=public,
        output=output,
        ledger=ledger,
        api_key=api_key,
    )
