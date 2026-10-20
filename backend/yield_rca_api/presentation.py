"""Read-only report projection for saved jobs; no State or authority writes."""

from dataclasses import replace

from yield_rca_core.models import RCAState, Report
from yield_rca_core.report_generator import ReportGenerator


def report_for_view(state: RCAState) -> Report | None:
    if state.report is None or state.triage_result is None:
        return state.report
    rendered = ReportGenerator().generate(state, report_id=state.report.report_id)
    return replace(rendered, created_at=state.report.created_at)
