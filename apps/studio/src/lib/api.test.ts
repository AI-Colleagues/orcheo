import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  addWorkspaceMember,
  getActiveWorkspace,
  createWorkspace,
  executeNode,
  getSystemInfo,
  listWorkspaceMembers,
  removeWorkspaceMember,
  updateWorkspaceMemberRole,
} from "./api";
import {
  clearSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "./workspace-session";

describe("executeNode", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("should successfully execute a node", async () => {
    const mockResponse = {
      status: "success",
      result: { foo: "bar", count: 42 },
      error: null,
    };

    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await executeNode({
      node_config: {
        type: "SetVariableNode",
        name: "test_node",
        variables: { foo: "bar", count: 42 },
      },
      inputs: {},
    });

    expect(result).toEqual(mockResponse);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/nodes/execute"),
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("SetVariableNode"),
      }),
    );
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    const headers = options?.headers as Headers;
    expect(headers).toBeInstanceOf(Headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("should handle error responses", async () => {
    const mockErrorResponse = {
      status: "error",
      result: null,
      error: "Node execution failed",
    };

    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockErrorResponse,
    });

    const result = await executeNode({
      node_config: {
        type: "InvalidNode",
        name: "test",
      },
      inputs: {},
    });

    expect(result.status).toBe("error");
    expect(result.error).toBe("Node execution failed");
  });

  it("should throw error on HTTP failure", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ detail: "Bad Request" }),
    });

    await expect(
      executeNode({
        node_config: { type: "Test", name: "test" },
        inputs: {},
      }),
    ).rejects.toThrow("Bad Request");
  });

  it("should include workflow_id when provided", async () => {
    const mockResponse = {
      status: "success",
      result: {},
      error: null,
    };

    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const workflowId = "550e8400-e29b-41d4-a716-446655440000";
    await executeNode({
      node_config: { type: "Test", name: "test" },
      inputs: {},
      workflow_id: workflowId,
    });

    const callArgs = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    const body = JSON.parse(callArgs[1].body);
    expect(body.workflow_id).toBe(workflowId);
  });

  it("should use custom base URL when provided", async () => {
    const mockResponse = {
      status: "success",
      result: {},
      error: null,
    };

    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    await executeNode(
      {
        node_config: { type: "Test", name: "test" },
        inputs: {},
      },
      "http://custom-backend:9000",
    );

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("http://custom-backend:9000"),
      expect.any(Object),
    );
  });

  it("should fetch system info", async () => {
    const mockResponse = {
      backend: {
        package: "orcheo-backend",
        current_version: "0.1.0",
        latest_version: "0.2.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      cli: {
        package: "orcheo-sdk",
        current_version: "0.1.0",
        latest_version: "0.2.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      workflow: {
        package: "orcheo-studio",
        current_version: "0.1.0",
        latest_version: "0.2.0",
        minimum_recommended_version: null,
        release_notes_url: null,
        update_available: true,
      },
      checked_at: "2026-02-21T12:00:00Z",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await getSystemInfo();
    expect(result.backend.package).toBe("orcheo-backend");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/info"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should fetch active workspace summary", async () => {
    const mockResponse = {
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "owner",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await getActiveWorkspace();
    expect(result.slug).toBe("acme");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/workspaces/active"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should create a workspace through the self-service endpoint", async () => {
    setSelectedWorkspaceSlug("stale-workspace");
    const mockResponse = {
      id: "workspace-1",
      slug: "acme",
      name: "Acme",
      status: "active",
      quotas: {
        max_workflows: 100,
        max_concurrent_runs: 25,
        max_credentials: 200,
        max_storage_rows: 1000000,
      },
      deleted_at: null,
      created_at: "2026-02-21T12:00:00Z",
      updated_at: "2026-02-21T12:00:00Z",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await createWorkspace({ slug: "acme", name: "Acme" });
    expect(result.slug).toBe("acme");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/workspaces"),
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    const headers = options?.headers as Headers;
    expect(headers.get("X-Orcheo-Workspace")).toBeNull();
    clearSelectedWorkspaceSlug();
  });

  it("should list workspace members", async () => {
    const mockMembers = [
      {
        id: "membership-1",
        workspace_id: "workspace-1",
        user_id: "user-1",
        role: "owner",
        created_at: "2026-05-17T12:00:00Z",
      },
    ];
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMembers,
    });

    const result = await listWorkspaceMembers("acme");
    expect(result).toEqual(mockMembers);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/workspaces/acme/members"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should throw with backend detail when listing members fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: "Forbidden" }),
    });

    await expect(listWorkspaceMembers("acme")).rejects.toThrow("Forbidden");
  });

  it("should extract message from structured error detail", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({
        detail: {
          error: { code: "workspace.slug_conflict", message: "Workspace slug already exists: acme" },
        },
      }),
    });

    await expect(createWorkspace({ slug: "acme", name: "Acme" })).rejects.toThrow(
      "Workspace slug already exists: acme",
    );
  });

  it("should add a workspace member", async () => {
    const mockMember = {
      id: "membership-2",
      workspace_id: "workspace-1",
      user_id: "user-2",
      role: "editor",
      created_at: "2026-05-17T12:00:00Z",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMember,
    });

    const result = await addWorkspaceMember("acme", {
      user_id: "user-2",
      role: "editor",
    });
    expect(result.user_id).toBe("user-2");
    const [, options] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body as string)).toEqual({
      user_id: "user-2",
      role: "editor",
    });
  });

  it("should update a workspace member's role", async () => {
    const mockMember = {
      id: "membership-2",
      workspace_id: "workspace-1",
      user_id: "user-2",
      role: "admin",
      created_at: "2026-05-17T12:00:00Z",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockMember,
    });

    const result = await updateWorkspaceMemberRole("acme", "user-2", "admin");
    expect(result.role).toBe("admin");
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toContain("/api/workspaces/acme/members/user-2");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body as string)).toEqual({ role: "admin" });
  });

  it("should remove a workspace member", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 204,
    });

    await removeWorkspaceMember("acme", "user-2");
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toContain("/api/workspaces/acme/members/user-2");
    expect(options.method).toBe("DELETE");
  });

  it("should throw when remove member fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Membership not found" }),
    });

    await expect(removeWorkspaceMember("acme", "ghost")).rejects.toThrow(
      "Membership not found",
    );
  });

});
