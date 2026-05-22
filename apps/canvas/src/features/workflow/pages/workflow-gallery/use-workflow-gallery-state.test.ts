import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listWorkflows } from "@features/workflow/lib/workflow-storage";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage.types";
import {
  type ApiCandidate,
  fetchCandidates,
} from "@features/workflow/lib/workflow-storage-api";
import { useWorkflowGalleryState } from "./use-workflow-gallery-state";

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  listWorkflows: vi.fn(),
  WORKFLOW_STORAGE_EVENT: "workflow-storage-updated",
}));

vi.mock("@features/workflow/lib/workflow-storage-api", () => ({
  fetchCandidates: vi.fn(),
}));

const mockedListWorkflows = vi.mocked(listWorkflows);
const mockedFetchCandidates = vi.mocked(fetchCandidates);

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

const makeCandidate = (
  partial: Pick<
    ApiCandidate,
    "id" | "handle" | "name" | "description" | "emoji" | "subtitle"
  >,
): ApiCandidate => ({
  ...partial,
  script: "",
  config: null,
  entrypoint: null,
  notes: null,
  metadata: null,
  mermaid: null,
});

const CANDIDATES: ApiCandidate[] = [
  makeCandidate({
    id: "insight_analyst",
    handle: "insight-analyst",
    name: "Insight Analyst",
    description: "Detects themes from text data.",
    emoji: "👨‍🎓",
    subtitle: "AI Insights & Analytics",
  }),
  makeCandidate({
    id: "marketing_specialist",
    handle: "marketing-specialist",
    name: "Marketing Specialist",
    description: "Creates engaging content.",
    emoji: "🧑‍💼",
    subtitle: "AI Content & Campaigns",
  }),
  makeCandidate({
    id: "market_intelligence",
    handle: "market-intelligence",
    name: "Market Intelligence Analyst",
    description: "Gathers competitive intelligence.",
    emoji: "🕵️",
    subtitle: "AI Competitive Intelligence",
  }),
  makeCandidate({
    id: "market_research",
    handle: "market-research",
    name: "Market Research Interviewer",
    description: "Conducts structured interviews.",
    emoji: "🙋",
    subtitle: "AI Consumer Research",
  }),
];

describe("useWorkflowGalleryState", () => {
  beforeEach(() => {
    mockedListWorkflows.mockReset();
    mockedListWorkflows.mockResolvedValue(STORED_WORKFLOWS);
    mockedFetchCandidates.mockReset();
    mockedFetchCandidates.mockResolvedValue(CANDIDATES);
  });

  it("computes tab counts for workspace workflows and candidates", async () => {
    const { result } = renderHook(() => useWorkflowGalleryState());

    await waitFor(() => {
      expect(result.current.isLoadingWorkflows).toBe(false);
      expect(result.current.tabCounts).toEqual({
        all: 3,
        pinned: 1,
        templates: 4,
      });
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
