import { afterEach, describe, expect, it, vi } from "vitest";

import {
  WORKFLOW_STORAGE_EVENT,
  onboardCandidateAsWorkflow,
} from "./workflow-storage";
import { setCandidateBadges } from "@features/workflow/data/templates/candidate-badges";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";

const { getFetchMock, queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

afterEach(() => {
  setCandidateBadges([]);
});

const TIMESTAMP = new Date().toISOString();

const makeWorkflowResponse = (id: string, name: string) => ({
  id,
  name,
  slug: id,
  description: `${name} colleague.`,
  tags: ["langgraph"],
  handle: name.toLowerCase().replace(/\s+/g, "-"),
  is_archived: false,
  created_at: TIMESTAMP,
  updated_at: TIMESTAMP,
});

const makeCanvasResponse = (workflowId: string, name: string) => ({
  workflow: makeWorkflowResponse(workflowId, name),
  versions: [
    {
      id: `${workflowId}-v1`,
      workflow_id: workflowId,
      version: 1,
      graph: {
        format: "langgraph-script",
        source: "from langgraph.graph import StateGraph\ngraph = StateGraph(dict)",
        entrypoint: "graph",
        index: { cron: [] },
      },
      metadata: { source: "candidate-onboard", candidate_id: "python-agent" },
      notes: null,
      created_by: "onboard",
      created_at: TIMESTAMP,
      updated_at: TIMESTAMP,
    },
  ],
});

describe("workflow-storage API integration - candidate onboarding", () => {
  it("onboards a candidate by posting to /api/candidates/onboard", async () => {
    setCandidateBadges([
      {
        id: "template-python-agent",
        candidateId: "python-agent",
        handle: "python-agent",
        name: "Simple Agent",
        notes: "Seeded from Simple Agent template.",
      },
    ]);
    const mockFetch = getFetchMock();

    queueResponses([
      jsonResponse(makeWorkflowResponse("workflow-1", "Simple Agent")),
      jsonResponse(makeCanvasResponse("workflow-1", "Simple Agent")),
    ]);

    const result = await onboardCandidateAsWorkflow("python-agent");

    expect(result.id).toBe("workflow-1");
    expect(result.name).toBe("Simple Agent");

    // First call: POST /api/candidates/onboard
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(String(mockFetch.mock.calls[0]?.[0])).toContain(
      "/api/candidates/onboard",
    );
    const onboardBody = JSON.parse(
      String(mockFetch.mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { id?: string };
    expect(onboardBody.id).toBe("python-agent");

    // Second call: GET /api/workflows/{id}/canvas
    expect(String(mockFetch.mock.calls[1]?.[0])).toContain(
      "/api/workflows/workflow-1/canvas",
    );
  });

  it("emits a storage event after successful onboarding", async () => {
    setCandidateBadges([
      {
        id: "template-telegram-agent",
        candidateId: "telegram-agent",
        handle: "telegram-agent",
        name: "Telegram Agent",
        notes: null,
      },
    ]);

    queueResponses([
      jsonResponse(makeWorkflowResponse("workflow-2", "Telegram Agent")),
      jsonResponse(makeCanvasResponse("workflow-2", "Telegram Agent")),
    ]);

    const listener = vi.fn();
    window.addEventListener(WORKFLOW_STORAGE_EVENT, listener);

    await onboardCandidateAsWorkflow("telegram-agent");

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(WORKFLOW_STORAGE_EVENT, listener);
  });

  it("returns a stored workflow with versions populated", async () => {
    setCandidateBadges([
      {
        id: "template-insight-analyst",
        candidateId: "insight-analyst",
        handle: "insight-analyst",
        name: "Insight Analyst",
        notes: null,
      },
    ]);

    queueResponses([
      jsonResponse(makeWorkflowResponse("workflow-3", "Insight Analyst")),
      jsonResponse(makeCanvasResponse("workflow-3", "Insight Analyst")),
    ]);

    const result = await onboardCandidateAsWorkflow("insight-analyst");

    expect(result.versions).toHaveLength(1);
    expect(result.versions[0].id).toBe("workflow-3-v1");
  });

  it("sends candidateId (not the template badge id) in the onboard request", async () => {
    // The badge id has "template-" prefix; the candidateId is the raw server id.
    setCandidateBadges([
      {
        id: "template-my-colleague",
        candidateId: "category/my-colleague",
        handle: "my-colleague",
        name: "My Colleague",
        notes: null,
      },
    ]);
    const mockFetch = getFetchMock();

    queueResponses([
      jsonResponse(makeWorkflowResponse("workflow-4", "My Colleague")),
      jsonResponse(makeCanvasResponse("workflow-4", "My Colleague")),
    ]);

    await onboardCandidateAsWorkflow("category/my-colleague");

    const onboardBody = JSON.parse(
      String(mockFetch.mock.calls[0]?.[1]?.body ?? "{}"),
    ) as { id?: string };
    expect(onboardBody.id).toBe("category/my-colleague");
  });
});
