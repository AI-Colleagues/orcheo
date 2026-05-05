import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExternalAgentProviderStatus } from "@/lib/api";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage.types";
import {
  __setCachedWorkflowIdForTesting,
  useVibeWorkflow,
} from "./use-vibe-workflow";
import { listWorkflows } from "@features/workflow/lib/workflow-storage";
import {
  fetchWorkflowVersions,
  request,
} from "@features/workflow/lib/workflow-storage-api";
import { VIBE_WORKFLOW_HANDLE } from "@features/vibe/constants";

vi.mock("@features/workflow/lib/workflow-storage", () => ({
  listWorkflows: vi.fn(),
  WORKFLOW_STORAGE_EVENT: "orcheo:workflows-updated",
}));

vi.mock("@features/workflow/lib/workflow-storage-api", () => ({
  fetchWorkflowVersions: vi.fn(),
  request: vi.fn(),
}));

const READY_PROVIDER: ExternalAgentProviderStatus = {
  provider: "codex",
  display_name: "Codex",
  state: "ready",
  installed: true,
  authenticated: true,
  supports_oauth: false,
  resolved_version: "1.0.0",
  executable_path: "/usr/local/bin/codex",
  checked_at: "2026-04-13T09:00:00.000Z",
  last_auth_ok_at: "2026-04-13T09:00:00.000Z",
  detail: null,
  active_session_id: null,
};

const EXISTING_VIBE_WORKFLOW: StoredWorkflow = {
  id: "workflow-1",
  handle: VIBE_WORKFLOW_HANDLE,
  name: "Orcheo Vibe",
  description: "Managed sidebar workflow.",
  createdAt: "2026-04-13T09:00:00.000Z",
  updatedAt: "2026-04-13T09:00:00.000Z",
  owner: {
    id: "canvas-app",
    name: "canvas-app",
    avatar: "",
  },
  tags: ["orcheo-vibe-agent", "external-agent"],
  nodes: [],
  edges: [],
  versions: [],
};

describe("useVibeWorkflow", () => {
  beforeEach(() => {
    __setCachedWorkflowIdForTesting(null);
    vi.clearAllMocks();
  });

  it("re-ingests and updates an existing vibe workflow when the stored template version is outdated, without creating a new workflow", async () => {
    vi.mocked(listWorkflows).mockResolvedValue([EXISTING_VIBE_WORKFLOW]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([
      {
        id: "workflow-1-version-1",
        workflow_id: "workflow-1",
        version: 1,
        metadata: {
          source: "canvas-template",
          template_id: "template-vibe-agent",
        },
        notes: "Seeded from the Orcheo Vibe template.",
        created_by: "canvas-app",
        created_at: "2026-04-13T09:00:00.000Z",
        updated_at: "2026-04-13T09:00:00.000Z",
        graph: {},
      },
    ]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (
        path === "/api/workflows/workflow-1/versions/ingest" &&
        options?.method === "POST"
      ) {
        return {
          id: "workflow-1-version-2",
          workflow_id: "workflow-1",
          version: 2,
        };
      }

      if (path === "/api/workflows/workflow-1" && options?.method === "PUT") {
        return {
          id: "workflow-1",
        };
      }

      throw new Error(`Unexpected request: ${path}`);
    });

    const { result } = renderHook(() => useVibeWorkflow([READY_PROVIDER]));

    await waitFor(() => {
      expect(result.current.workflowId).toBe("workflow-1");
      expect(result.current.isProvisioning).toBe(false);
      expect(result.current.error).toBeNull();
    });

    const ingestCall = vi
      .mocked(request)
      .mock.calls.find(
        ([path]) => path === "/api/workflows/workflow-1/versions/ingest",
      );

    expect(ingestCall).toBeDefined();
    const ingestBody = JSON.parse(String(ingestCall?.[1]?.body ?? "{}")) as {
      script?: string;
      metadata?: {
        template?: { templateVersion?: string };
      };
    };

    expect(ingestBody.script).toContain("Canvas context:");
    expect(ingestBody.metadata?.template?.templateVersion).toBe("1.0.1");

    expect(vi.mocked(request)).toHaveBeenCalledWith(
      "/api/workflows/workflow-1",
      {
        method: "PUT",
        body: JSON.stringify({
          actor: "canvas-app",
          chatkit: {
            supported_models: [
              {
                id: "codex",
                label: "Codex",
                default: true,
              },
            ],
          },
        }),
      },
    );
  });

  it("reuses an existing vibe workflow by handle even when its name or tags change", async () => {
    vi.mocked(listWorkflows).mockResolvedValue([
      {
        ...EXISTING_VIBE_WORKFLOW,
        name: "Renamed Vibe",
        tags: ["external-agent"],
      },
    ]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (path === "/api/workflows/workflow-1" && options?.method === "PUT") {
        return { id: "workflow-1" };
      }
      if (
        path === "/api/workflows/workflow-1/versions/ingest" &&
        options?.method === "POST"
      ) {
        return {
          id: "workflow-1-version-1",
          workflow_id: "workflow-1",
          version: 1,
        };
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    const { result } = renderHook(() => useVibeWorkflow([READY_PROVIDER]));

    await waitFor(() => {
      expect(result.current.workflowId).toBe("workflow-1");
      expect(result.current.error).toBeNull();
      expect(result.current.isProvisioning).toBe(false);
    });
  });

  it("reports an error when the managed workflow is missing after a refresh", async () => {
    __setCachedWorkflowIdForTesting("stale-workflow");

    vi.mocked(listWorkflows).mockImplementation(
      async (options?: { forceRefresh?: boolean }) =>
        options?.forceRefresh
          ? []
          : [
              {
                ...EXISTING_VIBE_WORKFLOW,
                id: "stale-workflow",
              },
            ],
    );
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      throw new Error(`Unexpected request: ${path}`);
    });

    const { result } = renderHook(() => useVibeWorkflow([READY_PROVIDER]));

    await waitFor(() => {
      expect(result.current.workflowId).toBeNull();
      expect(result.current.error).toBe(
        "Managed Orcheo Vibe workflow is unavailable.",
      );
      expect(result.current.isProvisioning).toBe(false);
    });

    expect(listWorkflows).toHaveBeenCalledWith({
      forceRefresh: true,
    });
  });

  it("clears a stale cached workflow id and falls back to discovering a valid managed workflow", async () => {
    vi.mocked(listWorkflows).mockResolvedValue([EXISTING_VIBE_WORKFLOW]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([
      {
        id: "workflow-1-version-1",
        workflow_id: "workflow-1",
        version: 1,
        metadata: {
          source: "canvas-template",
          template_id: "template-vibe-agent",
          template: {
            templateVersion: "1.0.1",
          },
        },
        notes: "Seeded from the Orcheo Vibe template.",
        created_by: "canvas-app",
        created_at: "2026-04-13T09:00:00.000Z",
        updated_at: "2026-04-13T09:00:00.000Z",
        graph: {},
      },
    ]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (path === "/api/workflows/workflow-1" && options?.method === "PUT") {
        return { id: "workflow-1" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    const initial = renderHook(() => useVibeWorkflow([READY_PROVIDER]));
    await waitFor(() => {
      expect(initial.result.current.workflowId).toBe("workflow-1");
      expect(initial.result.current.error).toBeNull();
    });
    initial.unmount();

    vi.mocked(listWorkflows).mockResolvedValue([
      {
        ...EXISTING_VIBE_WORKFLOW,
        id: "workflow-2",
      },
    ]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (path === "/api/workflows/workflow-1" && options?.method === "PUT") {
        throw new Error("Workflow not found");
      }
      if (path === "/api/workflows/workflow-2" && options?.method === "PUT") {
        return { id: "workflow-2" };
      }
      if (
        path === "/api/workflows/workflow-2/versions/ingest" &&
        options?.method === "POST"
      ) {
        return { id: "workflow-2-version-1" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    const recovered = renderHook(() => useVibeWorkflow([READY_PROVIDER]));

    await waitFor(() => {
      expect(recovered.result.current.workflowId).toBe("workflow-2");
      expect(recovered.result.current.error).toBeNull();
      expect(recovered.result.current.isProvisioning).toBe(false);
    });
  });

  it("does not recreate the vibe workflow after a storage update when it is missing", async () => {
    __setCachedWorkflowIdForTesting("workflow-1");

    vi.mocked(listWorkflows).mockResolvedValue([
      EXISTING_VIBE_WORKFLOW,
    ]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([
      {
        id: "workflow-1-version-1",
        workflow_id: "workflow-1",
        version: 1,
        metadata: {
          source: "canvas-template",
          template_id: "template-vibe-agent",
          template: {
            templateVersion: "1.0.1",
          },
        },
        notes: "Seeded from the Orcheo Vibe template.",
        created_by: "canvas-app",
        created_at: "2026-04-13T09:00:00.000Z",
        updated_at: "2026-04-13T09:00:00.000Z",
        graph: {},
      },
    ]);
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (path === "/api/workflows/workflow-1" && options?.method === "PUT") {
        return { id: "workflow-1" };
      }

      throw new Error(`Unexpected request: ${path}`);
    });

    const initial = renderHook(() => useVibeWorkflow([READY_PROVIDER]));

    await waitFor(() => {
      expect(initial.result.current.workflowId).toBe("workflow-1");
      expect(initial.result.current.error).toBeNull();
      expect(initial.result.current.isProvisioning).toBe(false);
    });

    vi.mocked(listWorkflows).mockResolvedValue([]);
    vi.mocked(fetchWorkflowVersions).mockResolvedValue([]);
    vi.mocked(request).mockImplementation(async (path) => {
      throw new Error(`Unexpected request: ${path}`);
    });

    window.dispatchEvent(new Event("orcheo:workflows-updated"));

    await waitFor(() => {
      expect(initial.result.current.workflowId).toBeNull();
      expect(initial.result.current.error).toBe(
        "Managed Orcheo Vibe workflow is unavailable.",
      );
      expect(initial.result.current.isProvisioning).toBe(false);
    });
  });
});
