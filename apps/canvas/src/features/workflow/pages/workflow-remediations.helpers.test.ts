import { describe, expect, it } from "vitest";

import type { ApiWorkflowRunRemediation } from "@features/workflow/lib/workflow-storage.types";
import {
  filterRemediations,
  sortRemediationsByUpdatedAt,
  summarizeRemediationNote,
} from "./workflow-remediations.helpers";

const makeRemediation = (
  overrides: Partial<ApiWorkflowRunRemediation>,
): ApiWorkflowRunRemediation => ({
  id: "rem-1",
  workflow_id: "wf-1",
  workflow_version_id: "ver-1",
  run_id: "run-1",
  status: "fixed",
  fingerprint: "fingerprint",
  version_checksum: "checksum",
  attempt_count: 1,
  context: {},
  artifacts: {},
  created_at: "2026-05-01T10:00:00.000Z",
  updated_at: "2026-05-01T10:30:00.000Z",
  ...overrides,
});

describe("workflow remediation helpers", () => {
  it("sorts remediations by most recent update first", () => {
    const sorted = sortRemediationsByUpdatedAt([
      makeRemediation({
        id: "older",
        updated_at: "2026-05-01T10:00:00.000Z",
      }),
      makeRemediation({
        id: "newer",
        updated_at: "2026-05-02T10:00:00.000Z",
      }),
    ]);

    expect(sorted.map((item) => item.id)).toEqual(["newer", "older"]);
  });

  it("filters remediations by status and search text", () => {
    const remediations = [
      makeRemediation({
        id: "match",
        workflow_id: "wf-1",
        developer_note: "Fix applied to the Slack notifier",
        status: "note_only",
      }),
      makeRemediation({
        id: "skip",
        workflow_id: "wf-2",
        developer_note: "Different issue",
        status: "fixed",
      }),
    ];

    const filtered = filterRemediations(remediations, {
      query: "slack",
      statusFilter: "note_only",
      workflowNamesById: new Map([["wf-1", "Slack workflow"]]),
    });

    expect(filtered.map((item) => item.id)).toEqual(["match"]);
  });

  it("summarizes remediation notes using the first non-empty line", () => {
    expect(
      summarizeRemediationNote("\n  First line\nSecond line\n"),
    ).toBe("First line");
    expect(summarizeRemediationNote("   \n\t")).toBe(
      "No developer note recorded.",
    );
  });
});
