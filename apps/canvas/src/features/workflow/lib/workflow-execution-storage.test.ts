import { describe, expect, it } from "vitest";

import { loadWorkflowExecutions } from "./workflow-execution-storage";
import {
  getFetchMock,
  jsonResponse,
  queueResponses,
  setupFetchMock,
} from "./workflow-storage.test-helpers";

setupFetchMock();

describe("workflow execution storage", () => {
  it("attaches remediation records to matching failed executions", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse([
        {
          execution_id: "run-1",
          workflow_id: "wf-1",
          status: "error",
          started_at: "2026-05-03T10:00:00Z",
          completed_at: "2026-05-03T10:01:00Z",
          error: "boom",
          inputs: {},
          steps: [],
        },
      ]),
      jsonResponse([
        {
          id: "rem-1",
          workflow_id: "wf-1",
          workflow_version_id: "v1",
          run_id: "run-1",
          status: "note_only",
          fingerprint: "fp",
          version_checksum: "checksum",
          attempt_count: 1,
          classification: "runtime_or_platform",
          action: "note_only",
          developer_note: "Runtime issue needs review.",
        },
      ]),
    ]);

    const executions = await loadWorkflowExecutions("wf-1", { limit: 10 });

    expect(executions).toHaveLength(1);
    expect(executions[0]?.remediations?.[0]?.id).toBe("rem-1");
    expect(executions[0]?.remediations?.[0]?.developer_note).toBe(
      "Runtime issue needs review.",
    );
    expect(String(mockFetch.mock.calls[1]?.[0])).toContain(
      "/api/workflow-remediations?workflow_id=wf-1&limit=50",
    );
  });
});
