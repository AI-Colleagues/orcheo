import { describe, expect, it } from "vitest";
import { hasSchedulableCronTrigger } from "./build-layout-props";

describe("hasSchedulableCronTrigger", () => {
  it("returns true when the latest saved version has a cron trigger", () => {
    expect(
      hasSchedulableCronTrigger([
        {
          id: "v1",
          version: "v01",
          versionNumber: 1,
          timestamp: "2026-04-23T06:23:17Z",
          message: "Uploaded from CLI",
          author: { id: "cli", name: "cli", avatar: "" },
          summary: { added: 0, removed: 0, modified: 0 },
          snapshot: { name: "Workflow", description: "", nodes: [], edges: [] },
          hasCronTrigger: true,
        },
      ]),
    ).toBe(true);
  });

  it("returns false when only a non-latest saved version has a cron trigger", () => {
    expect(
      hasSchedulableCronTrigger([
        {
          id: "v1",
          version: "v01",
          versionNumber: 1,
          timestamp: "2026-04-23T06:23:17Z",
          message: "Uploaded from CLI",
          author: { id: "cli", name: "cli", avatar: "" },
          summary: { added: 0, removed: 0, modified: 0 },
          snapshot: { name: "Workflow", description: "", nodes: [], edges: [] },
          hasCronTrigger: true,
        },
        {
          id: "v2",
          version: "v02",
          versionNumber: 2,
          timestamp: "2026-04-23T06:30:00Z",
          message: "Removed cron node",
          author: { id: "cli", name: "cli", avatar: "" },
          summary: { added: 0, removed: 1, modified: 0 },
          snapshot: { name: "Workflow", description: "", nodes: [], edges: [] },
          hasCronTrigger: false,
        },
      ]),
    ).toBe(false);
  });

  it("returns false when no versions are provided", () => {
    expect(hasSchedulableCronTrigger([])).toBe(false);
  });
});
