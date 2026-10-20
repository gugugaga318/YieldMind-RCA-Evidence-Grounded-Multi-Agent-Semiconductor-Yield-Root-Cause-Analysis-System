import { describe, expect, it } from "vitest";
import { jobFromUrl, jobUrl } from "./jobNavigation";

describe("persisted job navigation", () => {
  it("keeps completed jobs addressable after a refresh", () => {
    const path = jobUrl("http://localhost/?view=rca#result", "JOB_123");
    expect(path).toBe("/?view=rca&job=JOB_123#result");
    expect(jobFromUrl(`http://localhost${path}`)).toBe("JOB_123");
  });
  it("replaces only the job locator when switching jobs", () => {
    expect(jobUrl("http://localhost/?job=A", "B")).toBe("/?job=B");
  });
  it.each(["", "?job=", "?job=../../secret", "?job=%3Cscript%3E"])("rejects missing or malformed locator %s", suffix => {
    expect(jobFromUrl(`http://localhost/${suffix}`)).toBeNull();
  });
});
