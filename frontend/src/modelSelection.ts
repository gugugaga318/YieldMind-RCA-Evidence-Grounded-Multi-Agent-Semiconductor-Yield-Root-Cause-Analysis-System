import type {
  ExecutionMetadata, ModelCatalogStatus, ModelOptionsResponse, ModelSelection, RuntimeInfo,
} from "./types";

export function availableModel(catalog: ModelOptionsResponse, selection: ModelSelection): boolean {
  const provider = catalog.providers.find(item => item.provider === selection.provider);
  return !!provider?.configured && !!provider.models.find(item => item.model === selection.model)?.available;
}

export function chooseProvider(catalog: ModelOptionsResponse, providerId: string): ModelSelection | null {
  const provider = catalog.providers.find(item => item.provider === providerId);
  const model = provider?.configured ? provider.models.find(item => item.available) : null;
  return provider && model ? { provider: provider.provider, model: model.model } : null;
}

export function canSubmitModel(
  catalog: ModelOptionsResponse | null, status: ModelCatalogStatus,
  selection: ModelSelection | null, mode?: RuntimeInfo["agent_mode"],
): boolean {
  if (!catalog || status !== "ready") return false;
  if (mode && mode !== "llm") return selection === null;
  if (!catalog.providers.length) return false;
  if (selection) return availableModel(catalog, selection);
  const defaults = catalog.default_selection;
  if (!defaults.provider || !defaults.model) return false;
  const listed = catalog.providers.some(p => p.provider === defaults.provider &&
    p.models.some(m => m.model === defaults.model));
  // A trusted server-only default can still be used by omitting selection.
  return !listed || availableModel(catalog, defaults);
}

export function modelLabel(pair: ModelSelection, catalog?: ModelOptionsResponse | null): string {
  const provider = catalog?.providers.find(item => item.provider === pair.provider);
  const model = provider?.models.find(item => item.model === pair.model);
  return model ? `${provider!.display_name} / ${model.display_name} (${pair.provider} / ${pair.model})`
    : `${pair.provider} / ${pair.model}`;
}

export function modelIdentityView(metadata: ExecutionMetadata, catalog?: ModelOptionsResponse | null) {
  const selection = metadata.model_selection;
  const identity = metadata.model_identity;
  const requested = selection?.requested_provider && selection.requested_model
    ? { provider: selection.requested_provider, model: selection.requested_model } : null;
  const binding = identity?.basis === "runtime_config" && identity.author === "model" &&
    identity.provider && identity.model ? { provider: identity.provider, model: identity.model } : null;
  const recorded = selection?.actual_provider && selection.actual_model
    ? { provider: selection.actual_provider, model: selection.actual_model } : null;
  const actual = binding ?? recorded;
  const resolved = selection ? { provider: selection.resolved_provider, model: selection.resolved_model } : null;
  const differs = (a: ModelSelection | null, b: ModelSelection | null) => !!a && !!b &&
    (a.provider !== b.provider || a.model !== b.model);
  return {
    requested: requested ? modelLabel(requested, catalog) : "Not recorded",
    resolved: selection?.source === "server_default" && resolved ? modelLabel(resolved, catalog) : null,
    actual: actual ? modelLabel(actual, catalog)
      : metadata.agent_mode === "deterministic" ? "No LLM" : "Unknown",
    mismatch: selection?.identity_match === false || differs(requested ?? resolved, actual) || differs(binding, recorded),
  };
}
