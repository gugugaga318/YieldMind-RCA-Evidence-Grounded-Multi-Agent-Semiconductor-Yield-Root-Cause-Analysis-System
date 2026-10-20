"""Pre-R1 authority parity plus explicitly frozen R2.1 Evidence payload deltas."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "backend")]
spec = importlib.util.spec_from_file_location(
    "copilot_provider_baseline", ROOT / "tests/integration/test_provider_workflow_baseline.py"
)
assert spec and spec.loader
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)

from yield_rca_api.app import _state_api_payload  # noqa: E402
from yield_rca_api.schemas import RCAJobStateResponse  # noqa: E402
from yield_rca_core.models import RCAState  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/copilot_r1/pre_r1_payload_hashes.json"


def capture(scenario):
    original = RCAState.to_dict
    snapshots = []

    def record(state):
        value = original(state)
        snapshots.append(copy.deepcopy(value))
        return value

    with patch("socket.socket.connect", side_effect=AssertionError("network prohibited")):
        with patch.object(RCAState, "to_dict", record):
            workflow = baseline.capture(scenario)
        state = RCAState.from_dict(snapshots[-1])
        if state.triage_result is not None:
            assert state.triage_result.writer == "python_finalizer"
        api = RCAJobStateResponse.model_validate(_state_api_payload(state)).model_dump(mode="json")
    return {
        "workflow": workflow,
        "full_state_sha256": baseline.digest(snapshots[-1]),
        "full_api_sha256": baseline.digest(api),
    }


@pytest.mark.parametrize("scenario", baseline.SCENARIOS)
def test_exact_pre_r1_payload_parity(scenario):
    import json

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"][scenario]
    from copilot_evidence_closure_support import assert_evidence_closure_delta

    assert_evidence_closure_delta(scenario, capture(scenario), expected, full=True)


@pytest.mark.parametrize("scenario", baseline.SCENARIOS)
def test_r22_legacy_projection_remains_exact_r21(scenario):
    """Disable only the new writer; every prior full State/API byte must match."""
    import json
    frozen = ROOT / "tests/fixtures/copilot_r21/evidence_closure_payload_hashes.json"
    expected = json.loads(frozen.read_text(encoding="utf-8"))["scenarios"][scenario]
    with patch("yield_rca_core.investigation_finalizer.build_triage_projection", return_value=None):
        assert capture(scenario) == expected
