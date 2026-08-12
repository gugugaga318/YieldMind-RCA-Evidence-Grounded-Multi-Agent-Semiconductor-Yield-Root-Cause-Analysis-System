import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelRCAJob,
  ingestKnowledge,
  lookupKnowledge,
  openRCAJobEventStream,
} from "./api";

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

  it("uses the asynchronous cancellation endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ status: "cancelled" }));
    vi.stubGlobal("fetch", fetchMock);

    await cancelRCAJob("RCA_123");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/rca/jobs/RCA_123/cancel");
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("opens an SSE cursor and parses public Job Events", () => {
    class FakeEventSource {
      static latest: FakeEventSource;
      readonly url: string;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      listener: ((event: MessageEvent<string>) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        FakeEventSource.latest = this;
      }
      addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
        this.listener = listener as (event: MessageEvent<string>) => void;
      }
      close() {}
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    const events = vi.fn();
    const connections = vi.fn();

    openRCAJobEventStream("RCA WITH SPACE", events, connections, 7);
    FakeEventSource.latest.onopen?.();
    FakeEventSource.latest.listener?.(
      new MessageEvent("job_event", {
        data: JSON.stringify({
          job_id: "RCA WITH SPACE",
          sequence: 8,
          event_type: "agent_started",
          payload: { agent: "mes" },
          created_at: "2026-08-12T00:00:00Z",
        }),
      }),
    );

    expect(FakeEventSource.latest.url).toBe(
      "/api/rca/jobs/RCA%20WITH%20SPACE/events?after=7",
    );
    expect(connections).toHaveBeenCalledWith("live");
    expect(events).toHaveBeenCalledWith(expect.objectContaining({ sequence: 8 }));
  });
});
