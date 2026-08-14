import { Check, CircleAlert, Wrench } from "lucide-react";

import {
  authoritativeHypothesisFor,
  getFdcShifts,
  getRecommendedActions,
} from "../selectors";
import type { RCAState } from "../types";

interface RootCausePanelProps {
  state: RCAState;
}

function formatParameter(value: string): string {
  return value.replaceAll("_", " ");
}

export function RootCausePanel({ state }: RootCausePanelProps) {
  const hypothesis = authoritativeHypothesisFor(state);
  const actions = getRecommendedActions(state);
  const shifts = getFdcShifts(state);

  return (
    <section className="root-cause-panel" aria-labelledby="root-cause-heading">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Evidence-backed conclusion</span>
          <h2 id="root-cause-heading">Root Cause</h2>
        </div>
        <CircleAlert size={20} aria-hidden="true" />
      </div>

      {hypothesis ? (
        <>
          <div className="root-cause-copy">
            <span className={`support-status support-${hypothesis.status}`}>
              <Check size={14} aria-hidden="true" />
              {hypothesis.status}
            </span>
            <h3>{hypothesis.root_cause}</h3>
            <p>{hypothesis.rationale}</p>
          </div>

          <div className="confidence-block">
            <div className="confidence-label">
              <span>Confidence</span>
              <strong>{Math.round(hypothesis.confidence * 100)}%</strong>
            </div>
            <div className="confidence-track" aria-label="Root cause confidence">
              <span style={{ width: `${hypothesis.confidence * 100}%` }} />
            </div>
          </div>

          <div className="signal-list" aria-label="FDC parameter shifts">
            {shifts.map((shift) => (
              <div className="signal-row" key={shift.parameter_name}>
                <span>{formatParameter(shift.parameter_name)}</span>
                <strong className={shift.avg_delta_percent < 0 ? "negative" : "positive"}>
                  {shift.avg_delta_percent > 0 ? "+" : ""}
                  {shift.avg_delta_percent.toFixed(1)}%
                </strong>
              </div>
            ))}
          </div>

          <div className="actions-block">
            <div className="subheading-row">
              <Wrench size={16} aria-hidden="true" />
              <h3>Recommended actions</h3>
            </div>
            <ol>
              {actions.map((item) => (
                <li key={item.action}>{item.action}</li>
              ))}
            </ol>
          </div>
        </>
      ) : (
        <p className="empty-copy">No supported hypothesis is available.</p>
      )}
    </section>
  );
}
