import { describe, expect, it } from "vitest";

import { saveWorkflow } from "./workflow-storage";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";

const { getFetchMock, queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

describe("workflow-storage API integration - config-only save", () => {
  it("updates runnable config on an existing version without creating a version", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    const versionsPayload = [
      {
        id: "version-1",
        workflow_id: "workflow-456",
        version: 1,
        graph: {
          format: "langgraph-script",
          source: "from langgraph.graph import StateGraph\n",
        },
        metadata: {},
        runnable_config: null,
        notes: "Uploaded from Python",
        created_by: "cli",
        created_at: timestamp,
        updated_at: timestamp,
      },
    ];

    queueResponses([
      jsonResponse({
        id: "workflow-456",
        name: "Test Workflow",
        slug: "workflow-456",
        description: "Config-only save",
        tags: [],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse(versionsPayload),
      jsonResponse({
        ...versionsPayload[0],
        runnable_config: { tags: ["canvas"] },
      }),
      jsonResponse({
        id: "workflow-456",
        name: "Test Workflow",
        slug: "workflow-456",
        description: "Config-only save",
        tags: [],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([
        {
          ...versionsPayload[0],
          runnable_config: { tags: ["canvas"] },
        },
      ]),
    ]);

    await saveWorkflow(
      {
        name: "Test Workflow",
        description: "Config-only save",
        tags: [],
        nodes: [],
        edges: [],
      },
      { runnableConfig: { tags: ["canvas"] } },
    );

    expect(mockFetch).toHaveBeenCalledTimes(5);
    expect(String(mockFetch.mock.calls[2]?.[0])).toContain(
      "/api/workflows/workflow-456/versions/1/runnable-config",
    );
    expect(String(mockFetch.mock.calls[2]?.[1]?.method)).toBe("PUT");
    expect(String(mockFetch.mock.calls[1]?.[0])).toContain(
      "/api/workflows/workflow-456/versions",
    );
  });

  it("fails with a clear error when no version exists", async () => {
    const timestamp = new Date().toISOString();

    queueResponses([
      jsonResponse({
        id: "workflow-789",
        name: "No Version Workflow",
        slug: "workflow-789",
        description: "Missing version",
        tags: [],
        is_archived: false,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      jsonResponse([]),
    ]);

    await expect(
      saveWorkflow(
        {
          name: "No Version Workflow",
          description: "Missing version",
          tags: [],
          nodes: [],
          edges: [],
        },
        { runnableConfig: { tags: ["canvas"] } },
      ),
    ).rejects.toThrow("existing Python version");
  });
});
