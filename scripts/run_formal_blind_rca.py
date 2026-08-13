"""Run the formal RCA blind set using only its public data packet.

This runner deliberately has no Ground Truth input.  It reads the public case
catalogue and public CSV tables, writes one RCA state per case, and records the
exact public-file hashes used by the run.  Score the resulting directory only
with ``score_formal_blind_rca.py`` after the run has completed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.investigation_models import OrchestrationMode  # noqa: E402
from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    build_llm_client,
)
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

DEFAULT_PUBLIC_DIR = ROOT / ".blind_evaluation" / "formal_v1_candidate_r2" / "public"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_blind_v1" / "controlled_react_run"
SUPPORTED_CASE_KEYS = {
    "case_id",
    "source_lot_id",
    "query",
    "detected_module",
    "detected_operation",
    "known_observation",
    "candidate_peer_lot_ids",
    "declared_unavailable_sources",
}


class CappedLLMClient:
    """Apply a per-case ceiling when the runner is configured for a real LLM."""

    def __init__(self, delegate: LLMClient, *, max_calls: int) -> None:
        self.delegate = delegate
        self.max_calls = max_calls
        self.call_count = 0
        self.limit_exceeded = False
        self.last_prompt_name: str | None = None
        self.last_prompt_agent: str | None = None
        self.provider_failures: list[dict[str, Any]] = []
        self.provider = delegate.provider
        self.model = delegate.model

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if self.call_count >= self.max_calls:
            self.limit_exceeded = True
            raise LLMCallError(
                "Formal blind case exceeded its configured LLM-call cap",
                failure_category="formal_blind_call_cap",
            )
        self.call_count += 1
        self.last_prompt_name = request.prompt_name
        self.last_prompt_agent = request.agent
        try:
            return self.delegate.complete_json(request)
        except LLMCallError as exc:
            self.provider_failures.append(
                {
                    "prompt_name": request.prompt_name,
                    "agent": request.agent,
                    "failure_category": exc.failure_category,
                    "status_code": exc.status_code,
                    "provider_code": exc.provider_code,
                    "request_id": exc.request_id,
                    "provider_attempt_count": exc.call_attempt_count,
                }
            )
            raise


@dataclass(frozen=True)
class PublicCase:
    case_id: str
    source_lot_id: str
    query: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PublicCase:
        unknown = set(payload) - SUPPORTED_CASE_KEYS
        if unknown:
            raise ValueError(f"public case contains unsupported keys: {sorted(unknown)}")
        case = cls(
            case_id=str(payload["case_id"]).strip(),
            source_lot_id=str(payload["source_lot_id"]).strip(),
            query=str(payload["query"]).strip(),
        )
        if not case.case_id or not case.source_lot_id or not case.query:
            raise ValueError("public case_id, source_lot_id, and query are required")
        return case


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _public_files(public_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(public_dir)).replace("\\", "/"),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(public_dir.rglob("*"))
        if path.is_file()
    ]


def load_public_cases(public_dir: Path) -> tuple[str, list[PublicCase]]:
    """Load only the case catalogue exposed to the RCA system."""

    resolved = public_dir.resolve()
    if resolved.name != "public":
        raise ValueError("--public-dir must point to the formal packet's public directory")
    if not (resolved / "fab_data").is_dir():
        raise ValueError("public packet is missing fab_data/")
    payload = json.loads((resolved / "cases.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("public cases.json must contain an object")
    dataset_id = str(payload.get("dataset_id", "")).strip()
    raw_cases = payload.get("cases")
    if not dataset_id or not isinstance(raw_cases, list):
        raise ValueError("public cases.json requires dataset_id and cases")
    cases = [PublicCase.from_dict(dict(item)) for item in raw_cases]
    case_ids = [case.case_id for case in cases]
    if len(cases) != int(payload.get("case_count", 0)) or len(case_ids) != len(set(case_ids)):
        raise ValueError("public case_count or unique case_id contract is invalid")
    return dataset_id, cases


def _select_cases(cases: Iterable[PublicCase], requested: list[str]) -> list[PublicCase]:
    requested_ids = [item.strip() for item in requested if item.strip()]
    if not requested_ids:
        return list(cases)
    by_id = {case.case_id: case for case in cases}
    unknown = sorted(set(requested_ids) - set(by_id))
    if unknown:
        raise ValueError(f"unknown --case-id values: {unknown}")
    return [by_id[case_id] for case_id in requested_ids]


def _prepare_output(output_dir: Path, *, overwrite: bool) -> None:
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"output directory already exists: {output_dir}; use --overwrite to replace it"
            )
        shutil.rmtree(output_dir)
    (output_dir / "states").mkdir(parents=True)


def _strict_qwen_acceptance_reasons(
    result: dict[str, Any],
    *,
    requested_mode: str,
    agent_mode: str,
) -> list[str]:
    """Separate process completion from a clean, real-Qwen execution path."""

    if requested_mode != OrchestrationMode.LLM_REACT.value or agent_mode != "llm":
        return []
    reasons: list[str] = []
    if result.get("error") is not None or result.get("job_status") != "completed":
        reasons.append("workflow_not_completed")
    if result.get("actual_orchestration_mode") != OrchestrationMode.LLM_REACT.value:
        reasons.append("orchestration_fallback")
    if result.get("fallback_reason"):
        reasons.append("orchestration_fallback_reason_present")
    if result.get("hypothesis_candidate_source") != "qwen":
        reasons.append("hypothesis_candidate_not_qwen")
    if result.get("hypothesis_candidate_fallback_reason"):
        reasons.append("hypothesis_candidate_fallback")
    if result.get("provider_failures"):
        reasons.append("provider_failure")
    if result.get("llm_call_cap_exceeded"):
        reasons.append("llm_call_cap_exceeded")
    return reasons


def run_formal_blind(args: argparse.Namespace) -> dict[str, Any]:
    public_dir = args.public_dir.resolve()
    dataset_id, catalog_cases = load_public_cases(public_dir)
    cases = _select_cases(catalog_cases, args.case_id)
    mode = OrchestrationMode(args.orchestration_mode).value
    settings = LLMSettings.from_env()
    if settings.agent_mode == "llm" and not args.confirm_paid_qwen:
        raise ValueError(
            "--confirm-paid-qwen is required when YIELD_RCA_AGENT_MODE=llm"
        )
    _prepare_output(args.output_dir.resolve(), overwrite=args.overwrite)
    output_dir = args.output_dir.resolve()

    public_manifest = _public_files(public_dir)
    manifest = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "run_kind": "formal_rca_blind_execution",
        "created_at": datetime.now(UTC).isoformat(),
        "input_boundary": {
            "mode": "public_only",
            "public_dir": str(public_dir),
            "allowed_files": public_manifest,
            "ground_truth_loaded": False,
        },
        "configuration": {
            "orchestration_mode": mode,
            "agent_mode": settings.agent_mode,
            "provider": settings.provider if settings.agent_mode != "deterministic" else None,
            "model": settings.model if settings.agent_mode != "deterministic" else None,
            "max_llm_calls_per_case": args.max_llm_calls_per_case,
        },
        "selected_case_ids": [case.case_id for case in cases],
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    results: list[dict[str, Any]] = []
    for case in cases:
        client: CappedLLMClient | None = None
        try:
            delegate = build_llm_client(settings)
            if delegate is not None:
                client = CappedLLMClient(
                    delegate,
                    max_calls=args.max_llm_calls_per_case,
                )
            workflow = build_csv_workflow(
                public_dir / "fab_data",
                llm_settings=settings,
                llm_client=client,
                orchestration_mode=mode,
            )
            state = workflow.run(
                case.query,
                job_id=f"FORMAL_BLIND_{case.case_id}",
                lot_id=case.source_lot_id,
            )
            state_path = output_dir / "states" / f"{case.case_id}.json"
            state_path.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            hypothesis = state.hypotheses[-1] if state.hypotheses else None
            rca_finding = next(
                (
                    finding
                    for finding in reversed(state.findings)
                    if finding.agent == "rca_reasoning"
                ),
                None,
            )
            rca_details = rca_finding.details if rca_finding is not None else {}
            candidate_generation = dict(
                rca_details.get("hypothesis_candidate_generation", {})
            )
            ranked_candidates = list(rca_details.get("ranked_candidates", []))
            case_result = {
                    "case_id": case.case_id,
                    "source_lot_id": case.source_lot_id,
                    "state_file": str(state_path.relative_to(output_dir)).replace("\\", "/"),
                    "error": None,
                    "job_status": state.job.status,
                    "actual_orchestration_mode": state.execution_metadata.get(
                        "orchestration_mode"
                    ),
                    "fallback_reason": state.execution_metadata.get(
                        "orchestration_fallback_reason"
                    ),
                    "hypothesis_status": hypothesis.status if hypothesis else None,
                    "root_cause": hypothesis.root_cause if hypothesis else None,
                    "hypothesis_candidate_source": candidate_generation.get(
                        "source"
                    ),
                    "hypothesis_candidate_count": candidate_generation.get(
                        "candidate_count"
                    ),
                    "hypothesis_candidate_fallback_reason": candidate_generation.get(
                        "fallback_reason"
                    ),
                    "ranked_candidate_count": len(ranked_candidates),
                    "top_candidate_basis": (
                        ranked_candidates[0].get("basis")
                        if ranked_candidates
                        else None
                    ),
                    "impact_lot_count": len(state.impact_lots),
                    "evidence_count": len(state.evidence),
                    "action_count": len(state.action_history),
                    "llm_call_count": int(
                        state.execution_metadata.get("llm_call_count", 0)
                    ),
                    "last_llm_prompt_name": (
                        client.last_prompt_name if client is not None else None
                    ),
                    "last_llm_prompt_agent": (
                        client.last_prompt_agent if client is not None else None
                    ),
                    "provider_failures": (
                        list(client.provider_failures) if client is not None else []
                    ),
                    "llm_call_cap_exceeded": (
                        client.limit_exceeded if client is not None else False
                    ),
                }
            acceptance_reasons = _strict_qwen_acceptance_reasons(
                case_result,
                requested_mode=mode,
                agent_mode=settings.agent_mode,
            )
            case_result["strict_qwen_accepted"] = not acceptance_reasons
            case_result["strict_qwen_rejection_reasons"] = acceptance_reasons
            results.append(case_result)
        except Exception as exc:  # noqa: BLE001 - preserve every blind run result
            case_result = {
                    "case_id": case.case_id,
                    "source_lot_id": case.source_lot_id,
                    "state_file": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "job_status": None,
                    "actual_orchestration_mode": None,
                    "fallback_reason": None,
                    "hypothesis_status": None,
                    "root_cause": None,
                    "hypothesis_candidate_source": None,
                    "hypothesis_candidate_count": None,
                    "hypothesis_candidate_fallback_reason": None,
                    "ranked_candidate_count": None,
                    "top_candidate_basis": None,
                    "impact_lot_count": None,
                    "evidence_count": None,
                    "action_count": None,
                    "llm_call_count": client.call_count if client is not None else 0,
                    "last_llm_prompt_name": (
                        client.last_prompt_name if client is not None else None
                    ),
                    "last_llm_prompt_agent": (
                        client.last_prompt_agent if client is not None else None
                    ),
                    "provider_failures": (
                        list(client.provider_failures) if client is not None else []
                    ),
                    "llm_call_cap_exceeded": (
                        client.limit_exceeded if client is not None else False
                    ),
                }
            acceptance_reasons = _strict_qwen_acceptance_reasons(
                case_result,
                requested_mode=mode,
                agent_mode=settings.agent_mode,
            )
            case_result["strict_qwen_accepted"] = not acceptance_reasons
            case_result["strict_qwen_rejection_reasons"] = acceptance_reasons
            results.append(case_result)

    completed = sum(item["error"] is None for item in results)
    strict_qwen_evaluated = (
        mode == OrchestrationMode.LLM_REACT.value
        and settings.agent_mode == "llm"
    )
    strict_qwen_accepted = sum(
        bool(item["strict_qwen_accepted"]) for item in results
    )
    payload = {
        "schema_version": "1.0",
        "dataset_id": dataset_id,
        "run_kind": "formal_rca_blind_execution",
        "case_count": len(results),
        "completed_case_count": completed,
        "failed_case_count": len(results) - completed,
        "strict_qwen_acceptance_evaluated": strict_qwen_evaluated,
        "strict_qwen_accepted_case_count": strict_qwen_accepted,
        "strict_qwen_rejected_case_count": len(results) - strict_qwen_accepted,
        "results": results,
        "notice": "This execution artifact contains no Ground Truth and is not a score.",
    }
    (output_dir / "run_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Formal RCA Blind Execution",
        "",
        f"- Dataset: `{dataset_id}`",
        f"- Cases completed: {completed}/{len(results)}",
        (
            f"- Strict Qwen accepted: {strict_qwen_accepted}/{len(results)}"
            if strict_qwen_evaluated
            else "- Strict Qwen accepted: not evaluated for this configuration"
        ),
        f"- Requested orchestration: `{mode}`",
        f"- Agent mode: `{settings.agent_mode}`",
        "- Ground Truth loaded: **No**",
        "",
        "Run `score_formal_blind_rca.py` in a separate, explicitly approved step "
        "to score this output.",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--orchestration-mode",
        choices=[item.value for item in OrchestrationMode],
        default=OrchestrationMode.CONTROLLED_REACT.value,
    )
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--max-llm-calls-per-case", type=int, default=20)
    parser.add_argument("--confirm-paid-qwen", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_llm_calls_per_case < 1:
        raise ValueError("--max-llm-calls-per-case must be positive")
    result = run_formal_blind(args)
    print(
        "Formal blind execution: "
        f"completed={result['completed_case_count']}/{result['case_count']}; "
        f"failed={result['failed_case_count']}; "
        + (
            "strict_qwen="
            f"{result['strict_qwen_accepted_case_count']}/{result['case_count']}"
            if result["strict_qwen_acceptance_evaluated"]
            else "strict_qwen=not_evaluated"
        )
    )
    print(f"Results: {args.output_dir.resolve() / 'run_results.json'}")
    execution_failed = result["failed_case_count"] != 0
    strict_qwen_failed = (
        result["strict_qwen_acceptance_evaluated"]
        and result["strict_qwen_rejected_case_count"] != 0
    )
    return 1 if execution_failed or strict_qwen_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
