import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listWorkflows } from "@features/workflow/lib/workflow-storage";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage.types";
import { useWorkflowGalleryState } from "./use-workflow-gallery-state";

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  listWorkflows: vi.fn(),
  WORKFLOW_STORAGE_EVENT: "workflow-storage-updated",
}));

const mockedListWorkflows = vi.mocked(listWorkflows);

const STORED_WORKFLOWS: StoredWorkflow[] = [
  {
    id: "workflow-1",
    name: "Agent Ops",
    description: "Favorite internal workflow",
    createdAt: "2026-01-01T00:00:00.000Z",
    updatedAt: "2026-01-04T00:00:00.000Z",
    owner: { id: "user-1", name: "Owner", avatar: "" },
    tags: ["favorite"],
    nodes: [],
    edges: [],
    versions: [],
  },
  {
    id: "workflow-2",
    name: "Shared Inbox",
    description: "Shared with the team",
    createdAt: "2026-01-02T00:00:00.000Z",
    updatedAt: "2026-01-03T00:00:00.000Z",
    owner: { id: "user-2", name: "Teammate", avatar: "" },
    tags: [],
    nodes: [],
    edges: [],
    versions: [],
  },
  {
    id: "workflow-3",
    name: "Reporter",
    description: "Daily reporting",
    createdAt: "2026-01-03T00:00:00.000Z",
    updatedAt: "2026-01-02T00:00:00.000Z",
    owner: { id: "user-1", name: "Owner", avatar: "" },
    tags: [],
    nodes: [],
    edges: [],
    versions: [],
  },
];

describe("useWorkflowGalleryState", () => {
  beforeEach(() => {
    mockedListWorkflows.mockReset();
    mockedListWorkflows.mockResolvedValue(STORED_WORKFLOWS);
  });

  it("computes tab counts for workspace workflows and candidates", async () => {
    const { result } = renderHook(() => useWorkflowGalleryState());

    await waitFor(() => {
      expect(result.current.isLoadingWorkflows).toBe(false);
    });

    expect(result.current.tabCounts).toEqual({
      all: 3,
      pinned: 1,
      templates: 20,
    });

    act(() => {
      result.current.setSearchQuery("market");
    });

    await waitFor(() => {
      expect(result.current.tabCounts).toEqual({
        all: 0,
        pinned: 0,
        templates: 3,
      });
    });
  });
});
