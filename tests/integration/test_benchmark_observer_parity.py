"""Same frozen workflow with sidecar off/on, including rejected repair/failure paths."""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import test_challenge_diagnostic_workflow_parity as baseline
from benchmark_observer import capture_benchmark
from yield_rca_core import causal_adversarial


@pytest.mark.parametrize("mode", ["fixed", "controlled_react", "llm_react"])
@pytest.mark.parametrize(
    "outcome", ["valid", "repair", "invalid", "preflight", "repair_preflight", "provider_failure"]
)
def test_business_parity(mode, outcome, monkeypatch):
    captures = []
    original = baseline.capture_challenge_diagnostics

    @contextmanager
    def capture(**kwargs):
        with capture_benchmark() as sidecar, original(**kwargs) as diagnostic:
            yield diagnostic
        captures.append(sidecar.to_dict())

    monkeypatch.setattr(baseline, "before_observability", lambda: causal_adversarial)
    monkeypatch.setattr(baseline, "capture_challenge_diagnostics", capture)
    baseline.test_capture_workflow_and_planner_raw_parity(mode, outcome, monkeypatch)
    assert len(captures) == 1
    c = captures[0]
    assert c["complete"], c["dropped_count"]
    assert c["operations"]
    stages = {s["stage"] for s in c["snapshots"]}
    assert {
        "initial_raw_candidate",
        "admitted_candidate",
        "competition_survival",
        "ranking_survival",
        "confirmation_status",
        "finalizer_output",
    } <= stages
    for snapshot in c["snapshots"]:
        if snapshot["stage"] in {"competition_survival", "ranking_survival", "confirmation_status"}:
            assert snapshot["operation_id"] is not None
            assert snapshot["attempt_index"] is not None
    challenge = [o for o in c["operations"] if o["stage"] == "challenge"]
    assert challenge
    if outcome == "valid":
        assert all(len(o["attempts"]) == 1 for o in challenge)
    if outcome in {"repair", "invalid"}:
        assert all(len(o["attempts"]) == 2 for o in challenge)
    if outcome == "preflight":
        assert all(len(o["attempts"]) == 0 for o in challenge)
