"""Benchmark-only execution envelope. No model choice, fallback, retry or GT imports.

Call once around an existing configured workflow. Use a disposable worker process
with runtime_read_guard before building/reading runtime data.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

from benchmark_observer import capture_benchmark, clean, runtime_read_guard, serial, sha
from yield_rca_core.challenge_diagnostics import capture_challenge_diagnostics
from yield_rca_core.model_telemetry import capture_model_telemetry


def verify_freeze(manifest, repo: Path, public: Path):
    for root, hashes, normalized in (
        (repo, manifest["source_hashes"], True),
        (public, manifest["public_hashes"], False),
    ):
        for name, expected in hashes.items():
            path = (root / name).resolve()
            if root.resolve() not in path.parents:
                raise ValueError("Frozen path escapes allowed root")
            data = path.read_bytes()
            if normalized:
                data = data.replace(b"\r\n", b"\n")
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Frozen input/source drift: " + name)
    return {"verified": True, "manifest_hash": sha(manifest)}


def observe_once(execute, artifact_dir: Path, *, manifest=None, repo=None, public=None):
    artifact_dir.mkdir(parents=True, exist_ok=False)
    verification = (
        verify_freeze(manifest, repo, public) if manifest is not None else {"verified": False}
    )
    start = perf_counter()
    with (
        runtime_read_guard(),
        capture_model_telemetry() as telemetry,
        capture_challenge_diagnostics() as challenge,
        capture_benchmark() as capture,
    ):
        failure = None
        try:
            return execute()
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            # Persist in outer finally even when no RCA State exists.
            document = {
                "source_verification": verification,
                "capture": capture.to_dict(),
                "model_telemetry": {
                    "events": serial(telemetry.events),
                    "complete": telemetry.complete,
                    "dropped_count": telemetry.dropped_count,
                },
                "challenge": {
                    "records": serial(challenge.records),
                    "complete": challenge.dropped_count == 0 and challenge.truncation_count == 0,
                    "dropped_count": challenge.dropped_count,
                    "truncation_count": challenge.truncation_count,
                },
                "failure_type": failure,
                "workflow_latency_ms": (perf_counter() - start) * 1000,
            }
            try:
                (artifact_dir / "sidecar.json").write_text(
                    json.dumps(clean(document), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                if failure is None:
                    raise  # Infrastructure failure: do not report a captured run.
                # Preserve the original business exception. Missing sidecar makes
                # the run unexportable; never turn it into a successful record.
