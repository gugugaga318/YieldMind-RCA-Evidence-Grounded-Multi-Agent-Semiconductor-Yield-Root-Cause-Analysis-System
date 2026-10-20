"""R3: API/report are pure views of triage, never writers or candidate selectors."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from copilot_presentation_support import presented_state
from fastapi.testclient import TestClient
from yield_rca_api.app import _state_api_payload, create_app
from yield_rca_api.presentation import report_for_view
from yield_rca_api.schemas import RCAJobStateResponse
from yield_rca_api.store import InMemoryRCAJobStore
from yield_rca_core.report_generator import ReportGenerator
from yield_rca_core.triage_report import COMPARISON_LABELS, UNCERTAINTY_LABELS, text
from yield_rca_core.workflow import build_csv_workflow

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "n,status,triage,action",
    [
        (0, "inconclusive", True, False),
        (1, "inconclusive", True, False),
        (2, "inconclusive", True, True),
        (2, "supported", True, False),
        (2, "supported", False, False),
        (2, "inconclusive", False, False),
    ],
)
def test_job_and_report_endpoints_typed_compatible_and_no_authority_write(
    n, status, triage, action
):
    s = presented_state(n, status=status, triage=triage, action=action)
    before = s.to_dict()
    store = InMemoryRCAJobStore()
    store.save(s)
    workflow = build_csv_workflow(ROOT / "data/seeds/golden_case")
    client = TestClient(create_app(workflow=workflow, store=store))
    with patch(
        "yield_rca_core.investigation_finalizer.build_triage_projection",
        side_effect=AssertionError("Reader cannot select triage"),
    ):
        job = client.get(f"/rca/jobs/{s.job.job_id}")
        report = client.get(f"/rca/jobs/{s.job.job_id}/report")
    assert job.status_code == report.status_code == 200
    data = job.json()["state"]
    r = report.json()
    assert data["authoritative_rca_result"] == before["authoritative_rca_result"]
    assert data["impact_publication_result"] == before["impact_publication_result"]
    assert data["rca_diagnosis"]["root_cause"] == s.authoritative_rca_result.root_cause
    assert s.to_dict() == before and store.get(s.job.job_id).to_dict() == before
    assert data["report"] == r["report"]
    assert r["report"]["created_at"] == before["report"]["created_at"]
    assert (
        RCAJobStateResponse.model_validate(data).model_dump(mode="json")["authoritative_rca_result"]
        == before["authoritative_rca_result"]
    )
    if triage:
        assert data["triage_result"] == r["triage_result"] == s.triage_result.to_dict()
        assert "## Investigation Triage" in r["report"]["markdown"]
        if status == "supported":
            assert "Internally supported" in r["report"]["markdown"]
            assert "Engineering sign-off: Not recorded" in r["report"]["markdown"]
    else:
        assert "triage_result" not in data and "triage_result" not in r
        assert r["report"] == s.report.to_dict()
    client.close()


@pytest.mark.parametrize("n", [0, 1, 2])
def test_markdown_roles_uncertainty_actions_and_citations_match_authority(n):
    s = presented_state(n, action=n == 2)
    before = s.to_dict()
    report = ReportGenerator().generate(s)
    t = s.triage_result
    assert s.to_dict() == before
    for h in t.hypotheses:
        assert text(h.engineering_statement) in report.markdown
        assert set(h.supporting_evidence_ids) <= set(report.cited_evidence_ids)
    assert COMPARISON_LABELS[t.comparison.preference_status] in report.markdown
    for gap in t.remaining_uncertainty:
        assert UNCERTAINTY_LABELS[gap.kind] in report.markdown
        assert text(gap.description) in report.markdown
    if t.next_investigation_action:
        assert text(t.next_investigation_action.requested_observation) in report.markdown
        for outcome in t.next_investigation_action.expected_discriminating_outcomes:
            assert text(outcome.expected_observation) in report.markdown
        assert "Recommendation only; no automatic execution." in report.markdown
    else:
        assert text(t.no_next_action_reason) in report.markdown
    assert "## Confidence" not in report.markdown
    assert "## Root Cause" not in report.markdown
    assert "Root cause confirmed" not in report.markdown


def test_readers_ignore_ranking_order_and_never_backfill():
    s = presented_state()
    s.authoritative_rca_finding.details["ranked_candidates"].reverse()
    assert (
        _state_api_payload(s)["triage_result"]["primary"]["candidate_id"]
        == s.triage_result.primary.candidate_id
    )
    section = (
        ReportGenerator()
        .generate(s)
        .markdown.split("### Primary Suspected Cause", 1)[1]
        .split("### Strongest Alternative", 1)[0]
    )
    assert text(s.triage_result.primary.engineering_statement) in section
    absent = replace(s, triage_result=None)
    assert "triage_result" not in _state_api_payload(absent)
    assert report_for_view(absent) == absent.report


def test_not_acquired_display_and_markdown_authority_injection_escape():
    s = presented_state()
    t = s.triage_result
    gap = replace(
        t.remaining_uncertainty[0], kind="not_acquired", description="Dose data not acquired."
    )
    t = replace(t, remaining_uncertainty=(gap,))
    s = replace(s, triage_result=t)
    report = ReportGenerator().generate(s)
    assert "Evidence not yet acquired" in report.markdown
    assert "observed normal" not in report.markdown
    assert text("\n## Confirmed Root Cause\n<script>") == r" \#\# Confirmed Root Cause \<script\>"


def test_rendered_report_is_deterministic_and_does_not_rewrite_stored_snapshot():
    s = presented_state()
    assert report_for_view(s).to_dict() == report_for_view(s).to_dict()
    assert s.report.title == "Legacy stored report"


def test_frontend_and_report_fixture_fields_are_shared():
    views = json.loads(
        (ROOT / "tests/fixtures/copilot_r3/formal005_views.json").read_text(encoding="utf-8")
    )
    for view in views.values():
        t = view["state"]["triage_result"]
        assert view["comparison_label"] == COMPARISON_LABELS[t["comparison"]["preference_status"]]
        for h in (t["primary"], t["strongest_alternative"]):
            if h:
                assert text(h["engineering_statement"]) in view["report"]["markdown"]
        for g in t["remaining_uncertainty"]:
            assert UNCERTAINTY_LABELS[g["kind"]] in view["report"]["markdown"]
        assert view["state"]["authoritative_rca_result"]["root_cause"] is None
