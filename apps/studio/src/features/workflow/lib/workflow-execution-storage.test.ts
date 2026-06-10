import { describe, expect, it } from "vitest";

import { loadWorkflowExecutions } from "./workflow-execution-storage";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";

const { queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

describe("workflow execution storage", () => {
  it("maps execution history to executions", async () => {
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
    ]);

    const executions = await loadWorkflowExecutions("wf-1", { limit: 10 });

    expect(executions).toHaveLength(1);
    expect(executions[0]?.id).toBe("run-1");
  });
});
