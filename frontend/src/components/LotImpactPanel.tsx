import { Network } from "lucide-react";

import type { RCAState } from "../types";

function criterion(criteria: Record<string, unknown>, key: string): string {
  const value = criteria[key];
  return typeof value === "string" ? value : "Not available";
}

export function LotImpactPanel({ state }: { state: RCAState }) {
  const criteria = state.impact_criteria;

  return (
    <section className="lot-impact-panel" aria-labelledby="lot-impact-heading">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">Shared excursion exposure</span>
          <h2 id="lot-impact-heading">Impact Lots</h2>
        </div>
        <Network size={20} aria-hidden="true" />
      </div>

      <dl className="impact-criteria">
        <div>
          <dt>Operation</dt>
          <dd>{criterion(criteria, "operation_no")}</dd>
        </div>
        <div>
          <dt>Equipment / chamber</dt>
          <dd>
            {criterion(criteria, "equipment_id")} / {criterion(criteria, "chamber_id")}
          </dd>
        </div>
        <div>
          <dt>Excursion window</dt>
          <dd>
            {criterion(criteria, "excursion_start")} to {criterion(criteria, "excursion_end")}
          </dd>
        </div>
      </dl>

      <div className="impact-lot-list" aria-label="Impact Lot identifiers">
        {state.impact_lots.length > 0 ? (
          state.impact_lots.map((lotId) => <code key={lotId}>{lotId}</code>)
        ) : (
          <span>No additional impact Lots identified.</span>
        )}
      </div>
    </section>
  );
}
