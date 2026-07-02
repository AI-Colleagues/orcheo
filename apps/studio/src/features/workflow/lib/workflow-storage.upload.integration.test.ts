import { describe, expect, it, vi } from "vitest";

import {
  WORKFLOW_STORAGE_EVENT,
  WorkflowUploadFailedError,
  uploadWorkflowFromFiles,
} from "./workflow-storage";
import { jsonResponse } from "@/testing/mocks/backend/request-utils";
import { createFetchMockHarness } from "@/testing/mocks/fetch-mock";
import { readWorkflowUploadError } from "./workflow-upload-errors";

const { getFetchMock, queueResponses, setupFetchMock } =
  createFetchMockHarness();

setupFetchMock();

const workflowResponse = (id: string, name: string) =>
  jsonResponse({
    id,
    name,
    slug: id,
    description: null,
    tags: [],
    is_archived: false,
    created_at: "2026-05-17T12:00:00Z",
    updated_at: "2026-05-17T12:00:00Z",
  });

describe("uploadWorkflowFromFiles", () => {
  it("creates a workflow, ingests the script, and emits an update event", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      workflowResponse("uploaded-1", "Pipeline"),
      jsonResponse({ id: "version-1", version: 1 }),
      workflowResponse("uploaded-1", "Pipeline"),
      jsonResponse([
        {
          id: "version-1",
          workflow_id: "uploaded-1",
          version: 1,
          metadata: { source: "studio-upload" },
          notes: null,
          created_by: "studio-app",
          created_at: "2026-05-17T12:00:00Z",
          updated_at: "2026-05-17T12:00:00Z",
          graph: { format: "langgraph-script", source: "print('hi')" },
        },
      ]),
    ]);

    const listener = vi.fn();
    window.addEventListener(WORKFLOW_STORAGE_EVENT, listener);

    const stored = await uploadWorkflowFromFiles("Pipeline", "print('hi')", {
      runtime: "python",
    });

    expect(stored.id).toBe("uploaded-1");
    expect(listener).toHaveBeenCalled();

    const createPayload = JSON.parse(
      (mockFetch.mock.calls[0]?.[1]?.body ?? "{}") as string,
    ) as { name: string; actor: string };
    expect(createPayload.name).toBe("Pipeline");
    expect(createPayload.actor).toBe("studio-app");

    const ingestPayload = JSON.parse(
      (mockFetch.mock.calls[1]?.[1]?.body ?? "{}") as string,
    ) as {
      script: string;
      runnable_config: Record<string, unknown> | null;
      created_by: string;
      metadata: { source: string };
    };
    expect(ingestPayload.script).toBe("print('hi')");
    expect(ingestPayload.runnable_config).toEqual({ runtime: "python" });
    expect(ingestPayload.created_by).toBe("studio-app");
    expect(ingestPayload.metadata.source).toBe("studio-upload");

    window.removeEventListener(WORKFLOW_STORAGE_EVENT, listener);
  });

  it("passes a null runnable_config when no config file is provided", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      workflowResponse("uploaded-2", "Plain"),
      jsonResponse({ id: "version-1", version: 1 }),
      workflowResponse("uploaded-2", "Plain"),
      jsonResponse([
        {
          id: "version-1",
          workflow_id: "uploaded-2",
          version: 1,
          metadata: { source: "studio-upload" },
          notes: null,
          created_by: "studio-app",
          created_at: "2026-05-17T12:00:00Z",
          updated_at: "2026-05-17T12:00:00Z",
          graph: { format: "langgraph-script", source: "print('hi')" },
        },
      ]),
    ]);

    await uploadWorkflowFromFiles("Plain", "print('hi')", null);

    const ingestPayload = JSON.parse(
      (mockFetch.mock.calls[1]?.[1]?.body ?? "{}") as string,
    ) as { runnable_config: Record<string, unknown> | null };
    expect(ingestPayload.runnable_config).toBeNull();
  });

  it("honors an explicit actor override", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      workflowResponse("uploaded-3", "Actor"),
      jsonResponse({ id: "version-1", version: 1 }),
      workflowResponse("uploaded-3", "Actor"),
      jsonResponse([
        {
          id: "version-1",
          workflow_id: "uploaded-3",
          version: 1,
          metadata: { source: "studio-upload" },
          notes: null,
          created_by: "actor-x",
          created_at: "2026-05-17T12:00:00Z",
          updated_at: "2026-05-17T12:00:00Z",
          graph: { format: "langgraph-script", source: "print('hi')" },
        },
      ]),
    ]);

    await uploadWorkflowFromFiles("Actor", "print('hi')", null, {
      actor: "actor-x",
    });

    const createPayload = JSON.parse(
      (mockFetch.mock.calls[0]?.[1]?.body ?? "{}") as string,
    ) as { actor: string };
    const ingestPayload = JSON.parse(
      (mockFetch.mock.calls[1]?.[1]?.body ?? "{}") as string,
    ) as { created_by: string };
    expect(createPayload.actor).toBe("actor-x");
    expect(ingestPayload.created_by).toBe("actor-x");
  });

  it("keeps the workflow record visible when ingest fails", async () => {
    const mockFetch = getFetchMock();
    queueResponses([
      workflowResponse("uploaded-failed", "Broken"),
      jsonResponse({ detail: "Invalid script" }, { status: 400 }),
      workflowResponse("uploaded-failed", "Broken"),
      jsonResponse([]),
    ]);

    const listener = vi.fn();
    window.addEventListener(WORKFLOW_STORAGE_EVENT, listener);

    let thrown: unknown;
    try {
      await uploadWorkflowFromFiles("Broken", "def broken(:", null, {
        actor: "actor-x",
      });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(WorkflowUploadFailedError);
    expect((thrown as WorkflowUploadFailedError).workflow.id).toBe(
      "uploaded-failed",
    );
    expect((thrown as WorkflowUploadFailedError).workflow.uploadError).toEqual(
      expect.objectContaining({ message: "Invalid script" }),
    );
    expect(readWorkflowUploadError("uploaded-failed")).toEqual(
      expect.objectContaining({ message: "Invalid script" }),
    );
    expect(listener).toHaveBeenCalled();
    expect(mockFetch).toHaveBeenCalledTimes(4);
    expect(
      mockFetch.mock.calls.some((call) => call[1]?.method === "DELETE"),
    ).toBe(false);

    window.removeEventListener(WORKFLOW_STORAGE_EVENT, listener);
  });

  it("throws the server message when ingest fails", async () => {
    queueResponses([
      workflowResponse("uploaded-failed-message", "Broken"),
      jsonResponse({ detail: "Invalid script" }, { status: 400 }),
      workflowResponse("uploaded-failed-message", "Broken"),
      jsonResponse([]),
    ]);

    await expect(
      uploadWorkflowFromFiles("Broken", "def broken(:", null, {
        actor: "actor-x",
      }),
    ).rejects.toThrow("Invalid script");
  });
});
