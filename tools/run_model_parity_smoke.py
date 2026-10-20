"""One authorized FORMAL_012 smoke arm with external, model-only observation.

No business instrumentation, request rewriting, retry, or production source edits.
An exclusive per-arm latch prevents accidental paid repeats. Outputs stay local.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from contextlib import ExitStack, nullcontext
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import httpx

OLD = "8d9192f2769f0c6843f8944a84dd280a301f8ed9"
NEW = "0972ef88440f7830f0e9bc40d5e1e1bb9252f495"
CONFIG = {
    "YIELD_RCA_AGENT_MODE": "llm",
    "YIELD_RCA_ORCHESTRATION_MODE": "llm_react",
    "YIELD_RCA_LLM_PROVIDER": "dashscope",
    "YIELD_RCA_LLM_MODEL": "qwen-plus",
    "YIELD_RCA_LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "YIELD_RCA_LLM_TIMEOUT_SECONDS": "120",
    "YIELD_RCA_LLM_MAX_RETRIES": "1",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe(value, secret):
    if not isinstance(value, (str, int, float)):
        return None
    text = str(value).replace(secret, "[REDACTED]") if secret else str(value)
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    return text[:200]


def number(value):
    return value if type(value) in (int, float) else None


class ModelObserver:
    """Transparent wrappers; mutable model payloads are neither retained nor changed."""

    def __init__(self, gateway, secret="", progress=None):
        self.gateway = gateway
        self.secret = secret
        self.progress = progress
        self.calls = []
        self.current = None
        self.previous_request = None
        self.previous_error = None
        self.dropped = 0

    def attempt_metadata(self, request):
        same_retry = request is self.previous_request and isinstance(
            self.previous_error, self.gateway.LLMCallError
        )
        raw = request.payload.get("output_attempt")
        source = "payload.output_attempt"
        if type(raw) is not int:
            if request.prompt_name in {"specialist_tool_planner", "specialist_analysis"}:
                errors = request.payload.get("validation_errors", [])
                raw = len(errors) + 1 if isinstance(errors, list) else None
                source = "existing specialist payload.validation_errors length"
            elif request.prompt_name in {
                "planner",
                "specialist",
                "improvement",
                "causal_candidate_comparator",
                "rca_reasoning",
            }:
                raw = 1
                source = "existing single-attempt caller"
            else:
                raw = None
                source = "unobserved"
        return {
            "output_attempt": raw,
            "attempt_source": source,
            "planner_transient_retry": bool(
                same_retry and request.prompt_name == "next_action_planner"
            ),
            "bounded_output_retry": bool(raw is not None and raw > 1 and not same_retry),
        }

    def wrap_complete(self, original):
        def observed(client, request):
            started = perf_counter()
            record = None
            try:
                record = {
                    "ordinal": len(self.calls) + 1,
                    "correlation_id": f"FORMAL_012:call:{len(self.calls) + 1}",
                    "stage": request.prompt_name,
                    "agent": request.agent,
                    "prompt_version": request.prompt_version,
                    "provider": client.provider,
                    "model": client.model,
                    "request_sha256": digest(asdict(request)),
                    "http_attempts": [],
                    "usage_events": [],
                    "error": None,
                    **self.attempt_metadata(request),
                }
                self.calls.append(record)
                self.current = record
            except Exception:
                self.dropped += 1
            error = None
            try:
                result = original(client, request)
                if record is not None:
                    try:
                        record["parsed_response_sha256"] = digest(result.data)
                        record["outcome"] = "json_object"
                    except Exception:
                        self.dropped += 1
                return result
            except Exception as exc:
                error = exc
                if record is not None:
                    try:
                        record["outcome"] = "failed"
                        record["error"] = {
                            "type": type(exc).__name__,
                            "category": getattr(exc, "failure_category", None),
                            "status_code": getattr(exc, "status_code", None),
                            "provider_code": safe(getattr(exc, "provider_code", None), self.secret),
                            "request_id": safe(getattr(exc, "request_id", None), self.secret),
                            "call_attempt_count": getattr(exc, "call_attempt_count", None),
                        }
                    except Exception:
                        self.dropped += 1
                raise
            finally:
                self.previous_request, self.previous_error = request, error
                self.current = None
                if record is not None:
                    record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
                    if self.progress:
                        try:
                            self.progress(record)
                        except Exception:
                            self.dropped += 1

        return observed

    def wrap_usage(self, original):
        def observed(event):
            result = original(event)
            try:
                if self.current is not None:
                    self.current["usage_events"].append(event.to_dict())
                    self.current["logical_call_id"] = event.call_id
            except Exception:
                self.dropped += 1
            return result

        return observed

    def wrap_post(self, original):
        def observed(client, *args, **kwargs):
            started = perf_counter()
            attempt = None
            try:
                if self.current is not None:
                    attempt = {
                        "index": len(self.current["http_attempts"]) + 1,
                        "request_body_sha256": digest(kwargs.get("json")),
                        "status_code": None,
                        "error_type": None,
                    }
                    self.current["http_attempts"].append(attempt)
            except Exception:
                self.dropped += 1
            try:
                response = original(client, *args, **kwargs)
                if attempt is not None:
                    try:
                        attempt["status_code"] = response.status_code
                        attempt["request_id"] = safe(
                            response.headers.get("x-request-id"), self.secret
                        )
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = None
                        if isinstance(payload, dict):
                            usage = payload.get("usage")
                            attempt["reported_usage"] = (
                                {
                                    key: number(usage.get(key))
                                    for key in (
                                        "prompt_tokens",
                                        "completion_tokens",
                                        "total_tokens",
                                    )
                                }
                                if isinstance(usage, dict)
                                else None
                            )
                            choices = payload.get("choices")
                            choice = choices[0] if isinstance(choices, list) and choices else None
                            attempt["stop_reason"] = (
                                safe(choice.get("finish_reason"), self.secret)
                                if isinstance(choice, dict)
                                else None
                            )
                            attempt["reported_model"] = safe(payload.get("model"), self.secret)
                    except Exception:
                        self.dropped += 1
                return response
            except Exception as exc:
                if attempt is not None:
                    attempt["error_type"] = type(exc).__name__
                raise
            finally:
                if attempt is not None:
                    attempt["latency_ms"] = round((perf_counter() - started) * 1000, 3)

        return observed

    def install(self, stack):
        stack.enter_context(
            patch.object(
                self.gateway.DashScopeLLMClient,
                "complete_json",
                self.wrap_complete(self.gateway.DashScopeLLMClient.complete_json),
            )
        )
        stack.enter_context(
            patch.object(self.gateway, "_record_usage", self.wrap_usage(self.gateway._record_usage))
        )
        stack.enter_context(patch.object(httpx.Client, "post", self.wrap_post(httpx.Client.post)))


def read_key(dotenv):
    # Only the explicitly named project's credential file; never print or serialize it.
    for raw in dotenv.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("DASHSCOPE_API_KEY="):
            return line.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
    raise ValueError("DASHSCOPE_API_KEY missing from named configuration")


def git(checkout, *args):
    return subprocess.check_output(["git", *args], cwd=checkout, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--arm", choices=["A_old", "B_runtime"], required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
    parser.add_argument("--historical-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--confirm-paid-qwen", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    expected = OLD if args.arm == "A_old" else NEW
    if git(checkout, "rev-parse", "HEAD") != expected:
        raise ValueError("Arm HEAD is not the approved exact commit")
    if git(checkout, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Tracked worktree must be clean")
    public = args.public_dir.resolve()
    manifest = json.loads(args.historical_manifest.read_text(encoding="utf-8"))
    hashes = {
        entry["path"]: entry["sha256"].lower()
        for entry in manifest["input_boundary"]["allowed_files"]
    }
    if set(hashes) != {p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()}:
        raise ValueError("Public file inventory changed")
    if any(sha(public / name) != value for name, value in hashes.items()):
        raise ValueError("Public input hashes differ from historical reference")
    key = read_key(args.dotenv)
    if not key:
        raise ValueError("Empty credential")
    for name in list(os.environ):
        if name.startswith("YIELD_RCA_"):
            del os.environ[name]
    os.environ.update(CONFIG)
    os.environ["DASHSCOPE_API_KEY"] = key
    sys.path[:0] = [str(checkout / "core"), str(checkout / "scripts")]
    os.chdir(checkout)
    import yield_rca_core.llm_gateway as gateway
    from yield_rca_core.investigation_models import InvestigationGoal

    if Path(gateway.__file__).resolve().parent.parent != checkout / "core":
        raise ValueError("Wrong implementation import root")
    budgets = {
        name: InvestigationGoal.__dataclass_fields__[name].default
        for name in ("max_steps", "max_tool_calls")
    }
    if budgets != {"max_steps": 8, "max_tool_calls": 20}:
        raise ValueError("Action/tool budget differs from baseline")
    settings = gateway.LLMSettings.from_env()
    assert settings.timeout_seconds == 120 and settings.max_retries == 1
    preflight = {
        "arm": args.arm,
        "head": expected,
        "tracked_clean": True,
        "configuration": CONFIG,
        "budgets": budgets,
        "logical_cap": 20,
        "public_hashes": hashes,
        "prompt_hashes": {
            p.name: sha(p) for p in sorted((checkout / "core/yield_rca_core/prompts").glob("*.md"))
        },
        "runner_sha256": sha(checkout / "scripts/run_formal_blind_rca.py"),
        "harness_sha256": sha(Path(__file__).resolve()),
        "credential_available": True,
        "python": sys.version,
        "case_id": "FORMAL_012",
    }
    if args.preflight_only:
        print(json.dumps({k: v for k, v in preflight.items() if not k.endswith("hashes")}))
        return 0
    if not args.confirm_paid_qwen:
        raise ValueError("Explicit paid-arm confirmation required")
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / (args.arm + ".started.json")).open("x", encoding="utf-8") as handle:
        json.dump(preflight, handle, ensure_ascii=False, indent=2)
    output = root / args.arm
    trace = root / (args.arm + ".calls.jsonl")

    def progress(record):
        with trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "arm": args.arm,
                    "call": record["ordinal"],
                    "stage": record["stage"],
                    "http": len(record["http_attempts"]),
                    "outcome": record["outcome"],
                }
            ),
            flush=True,
        )

    observer = ModelObserver(gateway, key, progress)
    spec = importlib.util.spec_from_file_location(
        "parity_formal_runner", checkout / "scripts/run_formal_blind_rca.py"
    )
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    run_args = runner.build_parser().parse_args(
        [
            "--public-dir",
            str(public),
            "--case-id",
            "FORMAL_012",
            "--evaluation-role",
            "development_regression",
            "--orchestration-mode",
            "llm_react",
            "--max-llm-calls-per-case",
            "20",
            "--confirm-paid-qwen",
            "--output-dir",
            str(output),
        ]
    )
    capped = []
    original_init = runner.CappedLLMClient.__init__

    def observed_init(client, *pos, **kw):
        original_init(client, *pos, **kw)
        capped.append(client)

    capture = nullcontext(None)
    if args.arm == "B_runtime":
        from yield_rca_core.model_telemetry import capture_model_telemetry

        capture = capture_model_telemetry(max_events=1000)
    started = perf_counter()
    result = None
    runtime_facts = None
    try:
        with ExitStack() as stack:
            observer.install(stack)
            stack.enter_context(patch.object(runner.CappedLLMClient, "__init__", observed_init))
            runtime_facts = stack.enter_context(capture)
            result = runner.run_formal_blind(run_args)
    finally:
        elapsed = perf_counter() - started
        evidence = {
            "preflight": preflight,
            "elapsed_seconds": elapsed,
            "calls": observer.calls,
            "observer_dropped": observer.dropped,
            "capped_call_counts": [c.call_count for c in capped],
            "capped_limit_exceeded": [c.limit_exceeded for c in capped],
            "runtime_events": [asdict(e) for e in runtime_facts.events] if runtime_facts else None,
            "runtime_dropped": runtime_facts.dropped_count if runtime_facts else None,
        }
        serialized = json.dumps(evidence, ensure_ascii=False, indent=2)
        if key in serialized or "Bearer " in serialized:
            raise ValueError("Sensitive content detected; telemetry artifact refused")
        (root / (args.arm + ".telemetry.json")).write_text(serialized + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "arm": args.arm,
                "completed": result["results"][0]["job_status"],
                "strict": result["results"][0]["strict_qwen_accepted"],
                "logical_calls": len(observer.calls),
                "http_attempts": sum(len(c["http_attempts"]) for c in observer.calls),
                "elapsed_seconds": elapsed,
            }
        ),
        flush=True,
    )
    return 0 if result["results"][0]["strict_qwen_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
