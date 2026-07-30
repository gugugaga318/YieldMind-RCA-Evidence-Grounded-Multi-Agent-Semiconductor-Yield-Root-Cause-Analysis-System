import { AlertTriangle, DatabaseZap, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";

import { formatAgentName, getEvidenceChain } from "../selectors";
import type { Evidence, RCAState } from "../types";

interface EvidenceChainProps {
  state: RCAState;
}

export function EvidenceChain({ state }: EvidenceChainProps) {
  const chain = getEvidenceChain(state);
  const [selectedId, setSelectedId] = useState<string | null>(
    chain[0]?.evidence_ids[0] ?? null,
  );
  const evidenceById = useMemo(
    () => new Map(state.evidence.map((item) => [item.evidence_id, item])),
    [state.evidence],
  );
  const selected = selectedId ? evidenceById.get(selectedId) : undefined;

  return (
    <section className="evidence-section" aria-labelledby="evidence-heading">
      <div className="section-heading-row">
        <div>
          <span className="section-kicker">Traceable reasoning</span>
          <h2 id="evidence-heading">Evidence Chain</h2>
        </div>
        <span className="section-count">{state.evidence.length} records</span>
      </div>

      <div className="evidence-layout">
        <ol className="chain-list">
          {chain.map((item, index) => (
            <li
              className="chain-item"
              key={`${item.stage}:${item.evidence_ids.join("|")}`}
            >
              <div className="chain-index">{String(index + 1).padStart(2, "0")}</div>
              <div className="chain-content">
                <div className="chain-heading">
                  <strong>{formatAgentName(item.stage)}</strong>
                  <span>{Math.round(item.confidence * 100)}%</span>
                </div>
                <p>{item.claim}</p>
                <div className="evidence-id-list">
                  {item.evidence_ids.map((id) => (
                    <button
                      type="button"
                      className={selectedId === id ? "evidence-id active" : "evidence-id"}
                      onClick={() => setSelectedId(id)}
                      key={id}
                    >
                      {id}
                    </button>
                  ))}
                </div>
              </div>
            </li>
          ))}
        </ol>

        <EvidenceRecord evidence={selected} />
      </div>
    </section>
  );
}

function EvidenceRecord({ evidence }: { evidence: Evidence | undefined }) {
  const [showAllEntities, setShowAllEntities] = useState(false);

  if (!evidence) {
    return <div className="evidence-record empty-copy">Select an evidence record.</div>;
  }

  const typedText = evidence.observation ?? evidence.summary;
  const entities = evidence.entities ?? [];
  const visibleEntities = showAllEntities ? entities : entities.slice(0, 8);
  const hiddenEntityCount = Math.max(entities.length - visibleEntities.length, 0);
  const evidenceType = evidence.evidence_type ?? "legacy";
  const isDataGap = evidenceType === "data_missing" || evidenceType === "negative_signal";

  return (
    <div className="evidence-record">
      <div className="evidence-record-heading">
        {isDataGap ? (
          <AlertTriangle size={18} aria-hidden="true" />
        ) : (
          <DatabaseZap size={18} aria-hidden="true" />
        )}
        <div>
          <span className="field-label">Referenced record</span>
          <code>{evidence.evidence_id}</code>
        </div>
      </div>
      <p>{typedText}</p>
      {isDataGap && <div className="evidence-alert">{evidenceType.replaceAll("_", " ")}</div>}
      {entities.length > 0 && (
        <div className="entity-list" aria-label="Evidence entities">
          {visibleEntities.map((entity) => (
            <span className="entity-tag" key={`${entity.entity_type}:${entity.entity_id}`}>
              {entity.entity_type}: <code>{entity.entity_id}</code>
            </span>
          ))}
          {hiddenEntityCount > 0 && (
            <button
              type="button"
              className="entity-more"
              onClick={() => setShowAllEntities(true)}
            >
              +{hiddenEntityCount} more
            </button>
          )}
        </div>
      )}
      <dl>
        <div>
          <dt>Type</dt>
          <dd>{evidenceType}</dd>
        </div>
        <div>
          <dt>Agent</dt>
          <dd>{evidence.source_agent ?? evidence.source_type}</dd>
        </div>
        <div>
          <dt>Tool</dt>
          <dd>{evidence.source_tool ?? "Not available"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            {typeof evidence.confidence === "number"
              ? `${Math.round(evidence.confidence * 100)}%`
              : "Not available"}
          </dd>
        </div>
        <div>
          <dt>Timestamp</dt>
          <dd>{evidence.timestamp ? new Date(evidence.timestamp).toLocaleString() : "Not available"}</dd>
        </div>
      </dl>
      <button type="button" className="text-button" disabled>
        <ExternalLink size={14} aria-hidden="true" />
        Source record
      </button>
    </div>
  );
}
