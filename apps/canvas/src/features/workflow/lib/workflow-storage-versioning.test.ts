import { beforeEach, describe, expect, it } from "vitest";

import {
  ensureWorkflow,
  invalidateWorkflowCache,
} from "./workflow-storage-versioning";
import {
  getFetchMock,
  jsonResponse,
  queueResponses,
  setupFetchMock,
} from "./workflow-storage.test-helpers";

setupFetchMock();

describe("workflow-storage-versioning", () => {
  beforeEach(() => {
    invalidateWorkflowCache();
  });

  it("deduplicates concurrent workflow canvas loads", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      jsonResponse({
        workflow: {
          id: "wf-1",
          handle: "wf-1",
          name: "Canvas Flow",
          slug: "canvas-flow",
          description: "Test",
          tags: ["draft"],
          is_archived: false,
          is_public: false,
          require_login: false,
          published_at: null,
          published_by: null,
          created_at: "2026-03-10T09:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
          share_url: null,
        },
        versions: [
          {
            id: "v1",
            workflow_id: "wf-1",
            version: 1,
            mermaid: "graph TD; A-->B",
            metadata: {
              canvas: {
                snapshot: {
                  name: "Canvas Flow",
                  description: "Test",
                  nodes: [],
                  edges: [],
                },
                summary: { added: 0, removed: 0, modified: 0 },
              },
            },
            runnable_config: null,
            notes: null,
            created_by: "canvas",
            created_at: "2026-03-10T10:00:00Z",
            updated_at: "2026-03-10T10:00:00Z",
          },
        ],
      }),
    ]);

    const [first, second] = await Promise.all([
      ensureWorkflow("wf-1"),
      ensureWorkflow("wf-1"),
    ]);

    expect(first?.id).toBe("wf-1");
    expect(second?.id).toBe("wf-1");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/workflows/wf-1/canvas",
    );
  });

  it("preserves cron-trigger capability from compact canvas version summaries", async () => {
    queueResponses([
      jsonResponse({
        workflow: {
          id: "wf-1",
          handle: "wf-1",
          name: "Canvas Flow",
          slug: "canvas-flow",
          description: "Test",
          tags: ["draft"],
          is_archived: false,
          is_public: false,
          require_login: false,
          published_at: null,
          published_by: null,
          created_at: "2026-03-10T09:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
          share_url: null,
        },
        versions: [
          {
            id: "v1",
            workflow_id: "wf-1",
            version: 1,
            mermaid: "graph TD; A-->B",
            has_cron_trigger: true,
            metadata: {},
            runnable_config: null,
            notes: null,
            created_by: "cli",
            created_at: "2026-03-10T10:00:00Z",
            updated_at: "2026-03-10T10:00:00Z",
          },
        ],
      }),
    ]);

    const workflow = await ensureWorkflow("wf-1");

    expect(workflow?.versions[0]?.hasCronTrigger).toBe(true);
  });

  it("hydrates configurable schemas from version metadata", async () => {
    queueResponses([
      jsonResponse({
        workflow: {
          id: "wf-1",
          handle: "wf-1",
          name: "Simple Agent",
          slug: "simple-agent",
          description: "Test",
          tags: ["draft"],
          is_archived: false,
          is_public: false,
          require_login: false,
          published_at: null,
          published_by: null,
          created_at: "2026-03-10T09:00:00Z",
          updated_at: "2026-03-10T10:00:00Z",
          share_url: null,
        },
        versions: [
          {
            id: "v1",
            workflow_id: "wf-1",
            version: 1,
            mermaid: "graph TD; A-->B",
            metadata: {
              configurable_schema: {
                ai_model: {
                  type: "string",
                  enum: ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
                  title: "Model",
                  default: "openai:gpt-4.1-mini",
                },
              },
              canvas: {
                snapshot: {
                  name: "Simple Agent",
                  description: "Test",
                  nodes: [],
                  edges: [],
                },
                summary: { added: 0, removed: 0, modified: 0 },
              },
            },
            runnable_config: {
              configurable: {
                ai_model: "openai:gpt-4.1-mini",
              },
            },
            notes: null,
            created_by: "cli",
            created_at: "2026-03-10T10:00:00Z",
            updated_at: "2026-03-10T10:00:00Z",
          },
        ],
      }),
    ]);

    const workflow = await ensureWorkflow("wf-1");

    expect(workflow?.versions[0]?.configurableSchemas).toEqual({
      ai_model: {
        type: "string",
        enum: ["openai:gpt-4.1-mini", "openai:gpt-5.4-mini"],
        title: "Model",
        default: "openai:gpt-4.1-mini",
      },
    });
    expect(workflow?.versions[0]?.runnableConfig).toEqual({
      configurable: {
        ai_model: "openai:gpt-4.1-mini",
      },
    });
  });
});
