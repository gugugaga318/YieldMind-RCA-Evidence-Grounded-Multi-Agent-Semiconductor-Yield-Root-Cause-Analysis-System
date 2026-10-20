import { availableModel, canSubmitModel, chooseProvider } from "../modelSelection";
import type { ModelCatalogStatus, ModelOptionsResponse, ModelSelection, RuntimeInfo } from "../types";

export interface ModelSelectorProps {
  catalog: ModelOptionsResponse | null;
  status: ModelCatalogStatus;
  selection: ModelSelection | null;
  onChange: (selection: ModelSelection | null) => void;
  onReload: () => void;
  disabled: boolean;
  agentMode?: RuntimeInfo["agent_mode"];
}

export function ModelSelector({ catalog, status, selection, onChange, onReload, disabled, agentMode }: ModelSelectorProps) {
  if (status === "loading") return <p className="field-help" role="status">Loading models…</p>;
  if (status === "error" || !catalog) return <div className="model-selector-notice" role="alert">
    <span>Unable to load models. Please try again.</span>
    <button type="button" disabled={disabled} onClick={onReload}>Reload models</button>
  </div>;
  if (agentMode && agentMode !== "llm") return <p className="field-help">
    Server default — {agentMode} mode. Model selection is unavailable in this mode.
  </p>;
  if (!catalog.providers.length) return <div className="model-selector-notice" role="status">
    <span>No models available. Contact your server administrator.</span>
    <button type="button" disabled={disabled} onClick={onReload}>Reload models</button>
  </div>;
  const effective = selection ?? catalog.default_selection;
  const provider = catalog.providers.find(item => item.provider === effective.provider);
  const listed = provider?.models.some(item => item.model === effective.model);
  const serverOnlyDefault = selection === null && !listed;
  return <fieldset className="model-selector" disabled={disabled}>
    <legend>Run model</legend>
    <label htmlFor="rca-provider">Provider</label>
    <select id="rca-provider" disabled={disabled} value={serverOnlyDefault ? "" : effective.provider}
      onChange={event => {
        if (!event.target.value) { onChange(null); return; }
        const next = chooseProvider(catalog, event.target.value);
        if (next) onChange(next);
      }}>
      {serverOnlyDefault && <option value="">Server default ({effective.provider})</option>}
      {!serverOnlyDefault && !provider && <option value={effective.provider}>Selection unavailable</option>}
      {catalog.providers.map(item => <option key={item.provider} value={item.provider}
        disabled={!item.configured || !item.models.some(model => model.available)}>
        {item.display_name}{!item.configured ? " — Server not configured"
          : !item.models.some(model => model.available) ? " — Unavailable on server" : ""}
      </option>)}
    </select>
    <label htmlFor="rca-model">Model</label>
    <select id="rca-model" disabled={disabled || serverOnlyDefault} value={effective.model}
      onChange={event => {
        const next = { provider: effective.provider, model: event.target.value };
        if (availableModel(catalog, next)) onChange(next);
      }}>
      {(!listed || serverOnlyDefault) && <option value={effective.model}>{serverOnlyDefault ? effective.model : "Selection unavailable"}</option>}
      {!serverOnlyDefault && provider?.models.map(item => <option key={item.model} value={item.model}
        disabled={!provider.configured || !item.available}>
        {item.display_name}{!item.available ? " — Unavailable on server" : ""}
      </option>)}
    </select>
    <small className="field-help">{selection ? "This selection applies to the next RCA job." : "Server default. Change a selection to choose a model for this job."}</small>
    {!canSubmitModel(catalog, status, selection, agentMode) && <small className="field-help" role="status">
      Choose an available model. Unavailable options cannot be submitted.
    </small>}
    <button type="button" className="model-reload" disabled={disabled} onClick={onReload}>Refresh model availability</button>
  </fieldset>;
}
