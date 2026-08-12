import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AsyncJobProgress } from "./AsyncJobProgress";

describe("AsyncJobProgress", () => {
  it("renders ordered Agent evidence events and cancellation", () => {
    const html = renderToStaticMarkup(
      <AsyncJobProgress
        jobId="RCA_ASYNC"
        status="running"
        queue={{
          priority: 0,
          attempt_count: 1,
          max_attempts: 3,
          next_attempt_at: null,
          lease_expires_at: "2026-08-12T00:03:00Z",
          cancel_requested_at: null,
          started_at: "2026-08-12T00:00:00Z",
          completed_at: null,
          error: null,
          version: 3,
        }}
        events={[
          {
            job_id: "RCA_ASYNC",
            sequence: 1,
            event_type: "job_queued",
            payload: { status: "queued" },
            created_at: "2026-08-12T00:00:00Z",
          },
          {
            job_id: "RCA_ASYNC",
            sequence: 4,
            event_type: "action_completed",
            payload: {
              agent: "defect_wat",
              summary: "Radial scratch was observed.",
              evidence_ids: ["EV_DEFECT_1"],
            },
            created_at: "2026-08-12T00:00:05Z",
          },
        ]}
        connection="live"
        cancelling={false}
        onCancel={() => undefined}
      />,
    );

    expect(html).toContain("Asynchronous RCA progress");
    expect(html).toContain("Radial scratch was observed.");
    expect(html).toContain("1 Evidence");
    expect(html).toContain("Cancel investigation");
    expect(html).toContain("live");
  });
});
