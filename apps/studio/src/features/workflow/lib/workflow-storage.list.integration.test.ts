import { beforeEach, describe, expect, it } from "vitest";

import { invalidateWorkflowListCache, listWorkflows } from "./workflow-storage";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";

const { getFetchMock, queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

describe("workflow-storage API integration - list workflows", () => {
  beforeEach(() => {
    invalidateWorkflowListCache();
  });

  it("lists workflows by merging backing metadata", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    queueResponses([
      jsonResponse([
        {
          id: "workflow-abc",
          name: "Support triage",
          slug: "workflow-abc",
          description: "Routes support tickets to the right queue.",
          tags: ["support"],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse([
        {
          id: "version-1",
          workflow_id: "workflow-abc",
          version: 1,
          graph: {},
          metadata: {
            workflow: {
              snapshot: {
                name: "Support triage",
                description: "Routes support tickets to the right queue.",
                nodes: [
                  {
                    id: "start",
                    type: "trigger",
                    position: { x: 0, y: 0 },
                    data: { label: "Start" },
                  },
                ],
                edges: [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: "Initial draft",
            },
          },
          notes: null,
          created_by: "studio-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const workflows = await listWorkflows();

    expect(workflows).toHaveLength(1);
    expect(workflows[0]?.nodes).toHaveLength(1);
    expect(workflows[0]?.versions[0]?.summary.modified).toBe(0);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("uses backend latest_version summary without per-workflow fetches", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    queueResponses([
      jsonResponse([
        {
          id: "workflow-catalog",
          name: "Catalog flow",
          slug: "workflow-catalog",
          description: "Summarized by backend list API.",
          tags: ["catalog"],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
          latest_version: {
            id: "version-catalog-1",
            workflow_id: "workflow-catalog",
            version: 1,
            graph: {},
            mermaid: "graph TD; A-->B;",
            metadata: {
              workflow: {
                snapshot: {
                  name: "Catalog flow",
                  description: "Summarized by backend list API.",
                  nodes: [],
                  edges: [],
                },
                summary: { added: 0, removed: 0, modified: 0 },
                message: "Catalog draft",
              },
            },
            notes: null,
            created_by: "studio-app",
            created_at: timestamp,
            updated_at: timestamp,
          },
          is_scheduled: true,
        },
      ]),
    ]);

    const workflows = await listWorkflows();

    expect(workflows).toHaveLength(1);
    expect(workflows[0]?.versions).toHaveLength(1);
    expect(workflows[0]?.versions[0]?.id).toBe("version-catalog-1");
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("returns cached workflows when called repeatedly", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    queueResponses([
      jsonResponse([
        {
          id: "workflow-cache",
          name: "Cached workflow",
          slug: "workflow-cache",
          description: "Cache test",
          tags: [],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse([
        {
          id: "version-cache",
          workflow_id: "workflow-cache",
          version: 1,
          graph: {},
          metadata: {
            workflow: {
              snapshot: {
                name: "Cached workflow",
                description: "Cache test",
                nodes: [],
                edges: [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: "Initial draft",
            },
          },
          notes: null,
          created_by: "studio-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const first = await listWorkflows();
    const second = await listWorkflows();

    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);
    expect(first[0]?.id).toBe(second[0]?.id);
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("bypasses cache when force refresh is requested", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    queueResponses([
      jsonResponse([
        {
          id: "workflow-refresh",
          name: "Before refresh",
          slug: "workflow-refresh",
          description: "Old payload",
          tags: [],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse([
        {
          id: "version-refresh-1",
          workflow_id: "workflow-refresh",
          version: 1,
          graph: {},
          metadata: {
            workflow: {
              snapshot: {
                name: "Before refresh",
                description: "Old payload",
                nodes: [],
                edges: [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: "Initial draft",
            },
          },
          notes: null,
          created_by: "studio-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse([
        {
          id: "workflow-refresh",
          name: "After refresh",
          slug: "workflow-refresh",
          description: "New payload",
          tags: [],
          is_archived: false,
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
      jsonResponse([
        {
          id: "version-refresh-2",
          workflow_id: "workflow-refresh",
          version: 2,
          graph: {},
          metadata: {
            workflow: {
              snapshot: {
                name: "After refresh",
                description: "New payload",
                nodes: [],
                edges: [],
              },
              summary: { added: 0, removed: 0, modified: 0 },
              message: "Refresh draft",
            },
          },
          notes: null,
          created_by: "studio-app",
          created_at: timestamp,
          updated_at: timestamp,
        },
      ]),
    ]);

    const first = await listWorkflows();
    const refreshed = await listWorkflows({ forceRefresh: true });

    expect(first[0]?.name).toBe("Before refresh");
    expect(refreshed[0]?.name).toBe("After refresh");
    expect(mockFetch).toHaveBeenCalledTimes(4);
  });

  it("does not reuse in-flight request when force refresh is requested", async () => {
    const mockFetch = getFetchMock();
    const timestamp = new Date().toISOString();
    let releaseFirstList: (() => void) | undefined;

    mockFetch.mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/api/workflows") && !releaseFirstList) {
        return new Promise<Response>((resolve) => {
          releaseFirstList = () => {
            resolve(
              jsonResponse([
                {
                  id: "workflow-stale",
                  name: "Stale workflow",
                  slug: "workflow-stale",
                  description: "Stale payload",
                  tags: [],
                  is_archived: false,
                  created_at: timestamp,
                  updated_at: timestamp,
                },
              ]),
            );
          };
        });
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "workflow-fresh",
              name: "Fresh workflow",
              slug: "workflow-fresh",
              description: "Fresh payload",
              tags: [],
              is_archived: false,
              created_at: timestamp,
              updated_at: timestamp,
            },
          ]),
        );
      }

      if (url.includes("workflow-fresh")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "version-fresh",
              workflow_id: "workflow-fresh",
              version: 1,
              graph: {},
              metadata: {
                workflow: {
                  snapshot: {
                    name: "Fresh workflow",
                    description: "Fresh payload",
                    nodes: [],
                    edges: [],
                  },
                  summary: { added: 0, removed: 0, modified: 0 },
                  message: "Fresh",
                },
              },
              notes: null,
              created_by: "studio-app",
              created_at: timestamp,
              updated_at: timestamp,
            },
          ]),
        );
      }

      return Promise.resolve(
        jsonResponse([
          {
            id: "version-stale",
            workflow_id: "workflow-stale",
            version: 1,
            graph: {},
            metadata: {
              workflow: {
                snapshot: {
                  name: "Stale workflow",
                  description: "Stale payload",
                  nodes: [],
                  edges: [],
                },
                summary: { added: 0, removed: 0, modified: 0 },
                message: "Stale",
              },
            },
            notes: null,
            created_by: "studio-app",
            created_at: timestamp,
            updated_at: timestamp,
          },
        ]),
      );
    });

    const stalePromise = listWorkflows();
    const refreshedPromise = listWorkflows({ forceRefresh: true });
    releaseFirstList?.();

    const [stale, refreshed] = await Promise.all([
      stalePromise,
      refreshedPromise,
    ]);

    expect(stale[0]?.name).toBe("Stale workflow");
    expect(refreshed[0]?.name).toBe("Fresh workflow");
    expect(mockFetch).toHaveBeenCalledTimes(4);
  });
});
