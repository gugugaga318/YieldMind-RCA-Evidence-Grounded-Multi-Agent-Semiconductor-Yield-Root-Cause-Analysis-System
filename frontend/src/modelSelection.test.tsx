import { Children, isValidElement, type ReactElement, type ChangeEvent } from "react";
import { JobComposer } from "./components/JobComposer";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi, afterEach } from "vitest";
import { canSubmitModel, chooseProvider, modelIdentityView } from "./modelSelection";
import { ModelSelector } from "./components/ModelSelector";
import { ModelIdentity } from "./components/ModelIdentity";
import { buildRCAJobRequest } from "./selectors";
import { createRCAJob, getModelOptions } from "./api";
import type { ModelOptionsResponse, ModelSelection } from "./types";

const catalog: ModelOptionsResponse = {
  catalog_version: "web-model-selection-v1",
  default_selection: { provider: "dashscope", model: "qwen-plus" },
  providers: [
    { provider: "dashscope", display_name: "Qwen", configured: true,
      models: [{ model: "qwen-plus", display_name: "Qwen Plus", available: true }] },
    { provider: "deepseek", display_name: "DeepSeek", configured: true,
      models: [{ model: "deepseek-v4-pro", display_name: "DeepSeek V4 Pro", available: true }] },
  ],
};
const deepseek = { provider: "deepseek", model: "deepseek-v4-pro" };
const metadata = {
  model_selection: { requested_provider: "deepseek", requested_model: "deepseek-v4-pro",
    resolved_provider: "deepseek", resolved_model: "deepseek-v4-pro",
    actual_provider: "deepseek", actual_model: "deepseek-v4-pro",
    identity_match: true, source: "explicit" as const },
};
const props = { catalog, status: "ready" as const, selection: null,
  onChange: vi.fn(), onReload: vi.fn(), disabled: false, agentMode: "llm" as const };

afterEach(() => vi.unstubAllGlobals());

describe("backend model catalog and request integration", () => {
  it("loads catalog from the backend endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalog)));
    vi.stubGlobal("fetch", fetch);
    expect(await getModelOptions()).toEqual(catalog);
    expect(fetch.mock.calls[0][0]).toBe("/api/rca/model-options");
  });
  it.each([catalog.default_selection, deepseek])("submits canonical pair %j", async (selection) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ job_id: "job" })));
    vi.stubGlobal("fetch", fetch);
    const payload = buildRCAJobRequest("lot", "  scratch  ", " lot_a ", selection);
    await createRCAJob(payload);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      investigation_mode: "lot", user_query: "scratch", lot_id: "LOT_A", model_selection: selection,
    });
    selection = { ...selection, model: "changed-later" };
    expect(payload.model_selection?.model).not.toBe(selection.model);
  });
  it("omits selection for unchanged server default and preserves old payload", () => {
    expect(buildRCAJobRequest("product_window", " query ", "", null)).toEqual({
      investigation_mode: "product_window", user_query: "query",
    });
  });
  it("does not auto-switch an explicit selection after catalog refresh", () => {
    const changed = { ...catalog, providers: catalog.providers.slice(0, 1) };
    expect(canSubmitModel(changed, "ready", deepseek, "llm")).toBe(false);
    expect(deepseek.provider).toBe("deepseek");
  });
  it("keeps noncatalog server defaults without sending arbitrary model strings", () => {
    const legacy = { ...catalog, default_selection: { provider: "dashscope", model: "server-private" } };
    expect(canSubmitModel(legacy, "ready", null, "llm")).toBe(true);
    expect(canSubmitModel(legacy, "ready", legacy.default_selection, "llm")).toBe(false);
  });
  it("updates the model when provider changes and rejects disabled/cross-pair values", () => {
    expect(chooseProvider(catalog, "dashscope")).toEqual(catalog.default_selection);
    expect(chooseProvider(catalog, "deepseek")).toEqual(deepseek);
    const unavailable = { ...catalog, providers: catalog.providers.map(p => ({ ...p, configured: false })) };
    expect(chooseProvider(unavailable, "deepseek")).toBeNull();
    expect(canSubmitModel(catalog, "ready", { provider: "dashscope", model: deepseek.model }, "llm")).toBe(false);
  });
  it("keeps deterministic and fake defaults without selecting a paid provider", () => {
    expect(canSubmitModel(catalog, "ready", null, "deterministic")).toBe(true);
    expect(canSubmitModel(catalog, "ready", deepseek, "fake")).toBe(false);
  });
});

describe("ModelSelector", () => {
  it("shows backend default and model display names", () => {
    const html = renderToStaticMarkup(<ModelSelector {...props} />);
    expect(html).toContain("Qwen Plus");
    expect(html).toContain('value="dashscope" selected');
    expect(html).toContain("Server default");
  });
  it("only shows models belonging to the selected provider", () => {
    const html = renderToStaticMarkup(<ModelSelector {...props} selection={deepseek} />);
    expect(html).toContain("DeepSeek V4 Pro");
    expect(html).not.toContain("Qwen Plus");
  });
  it.each(["loading", "error"] as const)("blocks submit during catalog %s", (status) => {
    expect(canSubmitModel(catalog, status, null, "llm")).toBe(false);
    const html = renderToStaticMarkup(<ModelSelector {...props} status={status} />);
    expect(html).toContain(status === "loading" ? "Loading models" : "Unable to load models");
  });
  it("disables unavailable provider and explains server configuration", () => {
    const unavailable = { ...catalog, providers: catalog.providers.map(p => ({ ...p, configured: false })) };
    const html = renderToStaticMarkup(<ModelSelector {...props} catalog={unavailable} />);
    expect(html).toContain("Server not configured");
    expect(html).toContain('value="deepseek" disabled');
    expect(canSubmitModel(unavailable, "ready", null, "llm")).toBe(false);
  });
  it("handles empty catalog without inventing a model", () => {
    const empty = { ...catalog, providers: [] };
    expect(canSubmitModel(empty, "ready", null, "llm")).toBe(false);
    expect(renderToStaticMarkup(<ModelSelector {...props} catalog={empty} />)).toContain("No models available");
  });
  it("locks controls while a job is active", () => {
    const html = renderToStaticMarkup(<ModelSelector {...props} disabled />);
    expect(html).toMatch(/id="rca-provider"[^>]*disabled/);
    expect(html).toMatch(/id="rca-model"[^>]*disabled/);
  });
});

describe("job identity display", () => {
  it("shows requested and actual from job metadata", () => {
    const html = renderToStaticMarkup(<ModelIdentity metadata={metadata} catalog={catalog} />);
    expect(html).toContain("Requested model"); expect(html).toContain("Actual model");
    expect(html.match(/DeepSeek V4 Pro/g)?.length).toBe(2);
    expect(html).not.toContain('role="alert"');
  });
  it("makes requested/actual mismatch visible", () => {
    const mismatch = { ...metadata, model_selection: { ...metadata.model_selection,
      actual_provider: "dashscope", actual_model: "qwen-plus", identity_match: false } };
    expect(renderToStaticMarkup(<ModelIdentity metadata={mismatch} catalog={catalog} />)).toContain('role="alert"');
  });
  it("does not infer historical identity from legacy model/provider strings", () => {
    const view = modelIdentityView({ model: "qwen-plus", provider: "dashscope" }, catalog);
    expect(view.requested).toBe("Not recorded"); expect(view.actual).toBe("Unknown");
  });
  it("displays recorded actual independently of absent requested", () => {
    const view = modelIdentityView({ model_identity: { author: "model", basis: "runtime_config", ...deepseek } }, catalog);
    expect(view.requested).toBe("Not recorded"); expect(view.actual).toContain("DeepSeek V4 Pro");
  });
  it("keeps pending actual unknown instead of copying resolved", () => {
    const pending = { ...metadata, model_selection: { ...metadata.model_selection,
      actual_provider: null, actual_model: null, identity_match: null } };
    expect(modelIdentityView(pending, catalog).actual).toBe("Unknown");
  });
  it("labels an explicit deterministic runtime as No LLM", () => {
    expect(modelIdentityView({ agent_mode: "deterministic" }, catalog).actual).toBe("No LLM");
  });
});

function selectControl(element: ReactElement, id: string): ReactElement<{
  id: string; onChange: (event: ChangeEvent<HTMLSelectElement>) => void;
}> {
  const children = (element.props as { children: React.ReactNode }).children;
  return Children.toArray(children).find(child => isValidElement(child) &&
    (child.props as { id?: string }).id === id) as ReturnType<typeof selectControl>;
}

describe("selector events and composer integration", () => {
  it("provider event updates dependent model and serializes only the pair", async () => {
    let selection: ModelSelection | null = null;
    const tree = ModelSelector({ ...props, onChange: next => { selection = next; } });
    selectControl(tree, "rca-provider").props.onChange({ target: { value: "deepseek" } } as ChangeEvent<HTMLSelectElement>);
    expect(selection).toEqual(deepseek);
    const html = renderToStaticMarkup(<ModelSelector {...props} selection={selection} />);
    expect(html).toContain('value="deepseek-v4-pro" selected');
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ job_id: "selected-job" })));
    vi.stubGlobal("fetch", fetch);
    await createRCAJob(buildRCAJobRequest("product_window", "query", "", selection));
    expect(JSON.parse(fetch.mock.calls[0][1].body).model_selection).toEqual(deepseek);
  });
  it("ignores events naming unavailable providers and mismatched models", () => {
    const onChange = vi.fn();
    const unavailable = { ...catalog, providers: catalog.providers.map(p => p.provider === "deepseek" ? { ...p, configured: false } : p) };
    const tree = ModelSelector({ ...props, catalog: unavailable, onChange });
    selectControl(tree, "rca-provider").props.onChange({ target: { value: "deepseek" } } as ChangeEvent<HTMLSelectElement>);
    selectControl(tree, "rca-model").props.onChange({ target: { value: deepseek.model } } as ChangeEvent<HTMLSelectElement>);
    expect(onChange).not.toHaveBeenCalled();
  });
  it("surfaces catalog API failure without selecting a fallback", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
    await expect(getModelOptions()).rejects.toThrow("503");
  });
  it("keeps job identity independent of later composer/catalog selection", () => {
    const refreshed = { ...catalog, default_selection: catalog.default_selection, providers: catalog.providers.slice(0, 1) };
    const view = modelIdentityView(metadata, refreshed);
    expect(view.actual).toBe("deepseek / deepseek-v4-pro");
    expect(view.mismatch).toBe(false);
  });
  const composerProps = {
    workspaceMode: "product_window" as const, onWorkspaceModeChange: vi.fn(),
    query: "query", onQueryChange: vi.fn(), lotId: "LOT_A", onLotIdChange: vi.fn(),
    knowledgeQuestionKind: "historical_match" as const, onKnowledgeQuestionKindChange: vi.fn(),
    knowledgeModule: "Cu CMP", onKnowledgeModuleChange: vi.fn(),
    knowledgeExplicitModuleLimit: false, onKnowledgeExplicitModuleLimitChange: vi.fn(),
    onSubmit: vi.fn(), loading: false, currentJob: null, runtimeInfo: null,
    onCancel: vi.fn(), cancelling: false, modelSelector: props,
  };
  it("disables RCA submission on catalog failure", () => {
    const html = renderToStaticMarkup(<JobComposer {...composerProps}
      modelSelector={{ ...props, status: "error" }} />);
    expect(html).toMatch(/type="submit"[^>]*disabled/);
    expect(html).toContain("Unable to load models");
  });
  it("keeps Knowledge lookup independent of catalog failure", () => {
    const html = renderToStaticMarkup(<JobComposer {...composerProps} workspaceMode="knowledge"
      modelSelector={{ ...props, status: "error" }} />);
    expect(html).not.toContain("Unable to load models");
    expect(html).not.toMatch(/type="submit"[^>]*disabled/);
    expect(html).toContain("Search knowledge");
  });
  it("preserves Lot inputs and query validation", () => {
    const html = renderToStaticMarkup(<JobComposer {...composerProps} workspaceMode="lot" lotId="" />);
    expect(html).toContain("Abnormal Lot ID");
    expect(html).toContain("Investigation request");
    expect(html).toMatch(/type="submit"[^>]*disabled/);
  });
});
