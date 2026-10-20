"""Offline presentation fixtures from existing admitted-candidate test contracts."""

import importlib.util
from dataclasses import replace
from pathlib import Path

from yield_rca_core.models import Report

spec = importlib.util.spec_from_file_location(
    "r22_presentation_fixture", Path(__file__).parent / "contract/test_triage_finalizer_writer.py"
)
assert spec and spec.loader
r22 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r22)


def presented_state(n=2, *, status="inconclusive", action=False, triage=True):
    s = r22.publish(r22.with_action() if action else r22.fixture(n, status=status))
    if not triage:
        s = replace(s, triage_result=None)
    return replace(
        s,
        report=Report(
            report_id="STORED_REPORT",
            title="Legacy stored report",
            markdown="# Legacy stored report\n",
            cited_evidence_ids=[s.evidence[0].evidence_id],
            created_at="2026-10-01T00:00:00+00:00",
        ),
    )
