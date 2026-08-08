import { afterEach, describe, expect, it, vi } from "vitest";

import { ingestKnowledge, lookupKnowledge } from "./api";

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("knowledge API transport", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not set Content-Type for FormData uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ candidate: {} }));
    vi.stubGlobal("fetch", fetchMock);
    const formData = new FormData();
    formData.set("document_type", "SOP");

    await ingestKnowledge(formData);

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(headers.has("Content-Type")).toBe(false);
    expect(options.body).toBe(formData);
  });

  it("keeps JSON Content-Type for typed lookup requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({}));
    vi.stubGlobal("fetch", fetchMock);

    await lookupKnowledge({
      query: "scratch case",
      question_kind: "historical_match",
    });

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(options.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
