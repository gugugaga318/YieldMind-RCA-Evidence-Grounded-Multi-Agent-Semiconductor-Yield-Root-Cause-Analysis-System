"""Explicit R2.1 expected deltas; immutable pre-R1/provider baselines stay intact."""

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures/copilot_r21/evidence_closure_payload_hashes.json"
CHANGED_HASHES = {"calls", "state", "evidence", "findings", "execution_metadata"}


def assert_evidence_closure_delta(scenario, actual, historical, *, full=False):
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"][scenario]
    triage_fixture = (
        Path(__file__).parent / "fixtures/copilot_r22/triage_projection_payload_hashes.json"
    )
    projected = json.loads(triage_fixture.read_text(encoding="utf-8"))["scenarios"][scenario]
    presentation_fixture = (
        Path(__file__).parent / "fixtures/copilot_r3/presentation_payload_hashes.json"
    )
    presented = json.loads(presentation_fixture.read_text(encoding="utf-8"))["scenarios"][scenario]
    assert actual == (presented if full else presented["workflow"])
    for key, value in projected["workflow"]["hashes"].items():
        if key != "state":
            assert presented["workflow"]["hashes"][key] == value, key
    for key, value in projected["workflow"].items():
        if key != "hashes":
            assert presented["workflow"][key] == value, key
    projected_workflow = projected["workflow"]
    for key, value in expected["workflow"].items():
        if key != "hashes":
            assert projected_workflow[key] == value, key
    for key, value in expected["workflow"]["hashes"].items():
        if key != "state":
            assert projected_workflow["hashes"][key] == value, key
    current = actual["workflow"] if full else actual
    old = historical["workflow"] if full else historical
    for key in old:
        if key != "hashes":
            assert current[key] == old[key], key
    # Tool inputs, events, hypotheses, action history, planner decisions,
    # authoritative RCA/impact publication remain byte-exact. Changed payloads
    # are still frozen by the new fixture above; no broad recursive ignore list.
    for key in old["hashes"]:
        if key not in CHANGED_HASHES:
            assert current["hashes"][key] == old["hashes"][key], key
