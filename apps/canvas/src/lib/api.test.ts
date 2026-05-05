import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getActiveWorkspace,
  disconnectExternalAgent,
  executeNode,
  getExternalAgentLoginSession,
  getExternalAgents,
  getSystemInfo,
  refreshExternalAgents,
  startExternalAgentLogin,
  submitExternalAgentLoginInput,
} from "./api";

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
      canvas: {
        package: "orcheo-canvas",
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

  it("should fetch external agent status", async () => {
    const mockResponse = {
      providers: [
        {
          provider: "gemini",
          display_name: "Gemini CLI",
          state: "needs_login",
          installed: true,
          authenticated: false,
          supports_oauth: true,
          resolved_version: "0.36.0",
          executable_path: "/data/gemini/bin/gemini",
          checked_at: "2026-03-31T10:00:00Z",
          last_auth_ok_at: null,
          detail: "OAuth login is required on the worker.",
          active_session_id: null,
        },
        {
          provider: "codex",
          display_name: "Codex",
          state: "needs_login",
          installed: true,
          authenticated: false,
          supports_oauth: true,
          resolved_version: "0.30.0",
          executable_path: "/data/codex/bin/codex",
          checked_at: "2026-03-31T10:00:00Z",
          last_auth_ok_at: null,
          detail: "OAuth login is required on the worker.",
          active_session_id: null,
        },
      ],
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await getExternalAgents();
    expect(result.providers[0].provider).toBe("gemini");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/external-agents"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should request an external agent status refresh", async () => {
    const mockResponse = { providers: [] };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    await refreshExternalAgents();

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/external-agents/refresh"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("should start an external agent login session", async () => {
    const mockResponse = {
      session_id: "session-1",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "pending",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:00Z",
      completed_at: null,
      auth_url: null,
      device_code: null,
      detail: "Preparing the worker-side OAuth flow.",
      recent_output: null,
      resolved_version: null,
      executable_path: null,
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await startExternalAgentLogin("claude_code");
    expect(result.session_id).toBe("session-1");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/external-agents/claude_code/login"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("should submit input to an external agent login session", async () => {
    const mockResponse = {
      session_id: "session-1",
      provider: "claude_code",
      display_name: "Claude Code",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:00Z",
      completed_at: null,
      auth_url: "https://example.com",
      device_code: null,
      detail: "Auth code submitted to the worker. Waiting for completion.",
      recent_output: null,
      resolved_version: "2.1.89",
      executable_path: "/data/claude/bin/claude",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await submitExternalAgentLoginInput("session-1", {
      input_text: "ABCD-1234",
    });
    expect(result.session_id).toBe("session-1");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/system/external-agents/sessions/session-1/input",
      ),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input_text: "ABCD-1234" }),
      }),
    );
  });

  it("should fetch an external agent login session", async () => {
    const mockResponse = {
      session_id: "session-2",
      provider: "codex",
      display_name: "Codex",
      state: "awaiting_oauth",
      created_at: "2026-03-31T10:00:00Z",
      updated_at: "2026-03-31T10:00:10Z",
      completed_at: null,
      auth_url: "https://auth.openai.com/codex/device",
      device_code: "ABCD-1234",
      detail: "Complete the browser sign-in.",
      recent_output: "Visit the URL to continue.",
      resolved_version: "0.30.0",
      executable_path: "/data/codex/bin/codex",
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await getExternalAgentLoginSession("session-2");
    expect(result.auth_url).toContain("auth.openai.com");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/external-agents/sessions/session-2"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("should disconnect an external agent", async () => {
    const mockResponse = {
      provider: "gemini",
      display_name: "Gemini CLI",
      state: "checking",
      installed: true,
      authenticated: false,
      supports_oauth: true,
      resolved_version: "0.36.0",
      executable_path: "/data/gemini/bin/gemini",
      checked_at: "2026-03-31T10:00:00Z",
      last_auth_ok_at: null,
      detail: "Disconnecting worker auth state.",
      active_session_id: null,
    };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse,
    });

    const result = await disconnectExternalAgent("gemini");
    expect(result.provider).toBe("gemini");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/external-agents/gemini/disconnect"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
