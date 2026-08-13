from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_formal_blind_rca import _strict_qwen_acceptance_reasons  # noqa: E402


def clean_result() -> dict[str, object]:
    return {
        "error": None,
        "job_status": "completed",
        "actual_orchestration_mode": "llm_react",
        "fallback_reason": None,
        "hypothesis_candidate_source": "qwen",
        "hypothesis_candidate_fallback_reason": None,
        "provider_failures": [],
        "llm_call_cap_exceeded": False,
    }


def test_strict_qwen_accepts_a_clean_llm_react_case() -> None:
    assert _strict_qwen_acceptance_reasons(
        clean_result(),
        requested_mode="llm_react",
        agent_mode="llm",
    ) == []


def test_process_completion_does_not_hide_internal_qwen_fallbacks() -> None:
    result = {
        **clean_result(),
        "actual_orchestration_mode": "controlled_react",
        "fallback_reason": "qwen_next_action_output_invalid",
        "hypothesis_candidate_source": "deterministic_fallback",
        "hypothesis_candidate_fallback_reason": (
            "qwen_hypothesis_candidate_generation_failed"
        ),
    }

    reasons = _strict_qwen_acceptance_reasons(
        result,
        requested_mode="llm_react",
        agent_mode="llm",
    )

    assert "orchestration_fallback" in reasons
    assert "orchestration_fallback_reason_present" in reasons
    assert "hypothesis_candidate_not_qwen" in reasons
    assert "hypothesis_candidate_fallback" in reasons


def test_strict_qwen_is_not_applied_to_non_real_qwen_configuration() -> None:
    assert _strict_qwen_acceptance_reasons(
        {"error": "failed"},
        requested_mode="controlled_react",
        agent_mode="deterministic",
    ) == []
