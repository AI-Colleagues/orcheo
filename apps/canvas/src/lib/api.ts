import { authFetch } from "./auth-fetch";
import { buildBackendHttpUrl } from "./config";

export interface NodeExecutionRequest {
  node_config: Record<string, unknown>;
  inputs?: Record<string, unknown>;
  workflow_id?: string;
}

export interface NodeExecutionResponse {
  status: "success" | "error";
  result?: unknown;
  error?: string;
}

export interface PackageVersionStatus {
  package: string;
  current_version: string | null;
  latest_version: string | null;
  minimum_recommended_version: string | null;
  release_notes_url: string | null;
  update_available: boolean;
}

export interface SystemInfoResponse {
  backend: PackageVersionStatus;
  cli: PackageVersionStatus;
  canvas: PackageVersionStatus;
  checked_at: string;
}

export interface ActiveWorkspaceResponse {
  workspace_id?: string;
  slug: string;
  name: string;
  role: "owner" | "admin" | "editor" | "viewer";
}

export interface WorkspaceMembershipSummary {
  workspace_id: string;
  slug: string;
  name: string;
  role: "owner" | "admin" | "editor" | "viewer";
  status: "active" | "suspended" | "deleted";
}

export interface WorkspaceMembershipsResponse {
  memberships: WorkspaceMembershipSummary[];
}

export interface WorkspaceCreateRequest {
  slug: string;
  name: string;
  owner_user_id?: string;
}

export interface WorkspaceResponse {
  id: string;
  slug: string;
  name: string;
  status: "active" | "suspended" | "deleted";
}

export interface DevLoginRequest {
  provider?: string;
  email?: string;
  name?: string;
}

export interface DevLoginResponse {
  provider: string;
  subject: string;
  display_name: string;
}

export type ExternalAgentProviderName = "claude_code" | "codex" | "gemini";

export type ExternalAgentProviderState =
  | "unknown"
  | "checking"
  | "installing"
  | "not_installed"
  | "needs_login"
  | "authenticating"
  | "ready"
  | "error";

export type ExternalAgentLoginSessionState =
  | "pending"
  | "installing"
  | "awaiting_oauth"
  | "authenticated"
  | "failed"
  | "timed_out";

export interface ExternalAgentProviderStatus {
  provider: ExternalAgentProviderName;
  display_name: string;
  state: ExternalAgentProviderState;
  installed: boolean;
  authenticated: boolean;
  supports_oauth: boolean;
  resolved_version: string | null;
  executable_path: string | null;
  checked_at: string | null;
  last_auth_ok_at: string | null;
  detail: string | null;
  active_session_id: string | null;
}

export interface ExternalAgentLoginSession {
  session_id: string;
  provider: ExternalAgentProviderName;
  display_name: string;
  state: ExternalAgentLoginSessionState;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  auth_url: string | null;
  device_code: string | null;
  detail: string | null;
  recent_output: string | null;
  resolved_version: string | null;
  executable_path: string | null;
}

export interface ExternalAgentLoginInputRequest {
  input_text: string;
}

export interface ExternalAgentsResponse {
  providers: ExternalAgentProviderStatus[];
}

/**
 * Execute a single node in isolation for testing/preview purposes.
 *
 * @param request - Node execution request containing node_config, inputs, and optional workflow_id
 * @param baseUrl - Optional backend base URL (defaults to configured backend URL)
 * @returns Promise resolving to the node execution response
 * @throws Error if the request fails
 */
export async function executeNode(
  request: NodeExecutionRequest,
  baseUrl?: string,
): Promise<NodeExecutionResponse> {
  const url = buildBackendHttpUrl("/api/nodes/execute", baseUrl);

  const response = await authFetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to execute node",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function getSystemInfo(
  baseUrl?: string,
): Promise<SystemInfoResponse> {
  const url = buildBackendHttpUrl("/api/system/info", baseUrl);
  const response = await authFetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to fetch system info",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function getActiveWorkspace(
  baseUrl?: string,
): Promise<ActiveWorkspaceResponse> {
  const url = buildBackendHttpUrl("/api/workspaces/active", baseUrl);
  const response = await authFetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to fetch active workspace",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function getMyWorkspaces(
  baseUrl?: string,
): Promise<WorkspaceMembershipsResponse> {
  const url = buildBackendHttpUrl("/api/workspaces/me", baseUrl);
  const response = await authFetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to fetch workspaces",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function startDevLogin(
  request: DevLoginRequest,
  baseUrl?: string,
): Promise<DevLoginResponse> {
  const url = buildBackendHttpUrl("/api/auth/dev/login", baseUrl);
  const response = await authFetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to start developer login",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function endDevLogin(baseUrl?: string): Promise<void> {
  const url = buildBackendHttpUrl("/api/auth/dev/logout", baseUrl);
  const response = await authFetch(url, {
    method: "POST",
  });

  if (!response.ok && response.status !== 204) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to end developer login",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }
}

export async function createWorkspace(
  request: WorkspaceCreateRequest,
  baseUrl?: string,
): Promise<WorkspaceResponse> {
  return requestSystemJson<WorkspaceResponse>(
    "/api/workspaces",
    {
      method: "POST",
      body: JSON.stringify(request),
    },
    baseUrl,
    { includeWorkspaceHeaders: false },
  );
}

async function requestSystemJson<T>(
  path: string,
  init: RequestInit,
  baseUrl?: string,
  options: { includeWorkspaceHeaders?: boolean } = {},
): Promise<T> {
  const url = buildBackendHttpUrl(path, baseUrl);
  const response = await authFetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  }, options);

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      detail: "Failed to complete request",
    }));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

export async function getExternalAgents(
  baseUrl?: string,
): Promise<ExternalAgentsResponse> {
  return requestSystemJson<ExternalAgentsResponse>(
    "/api/system/external-agents",
    { method: "GET" },
    baseUrl,
  );
}

export async function refreshExternalAgents(
  baseUrl?: string,
): Promise<ExternalAgentsResponse> {
  return requestSystemJson<ExternalAgentsResponse>(
    "/api/system/external-agents/refresh",
    { method: "POST" },
    baseUrl,
  );
}

export async function startExternalAgentLogin(
  provider: ExternalAgentProviderName,
  baseUrl?: string,
): Promise<ExternalAgentLoginSession> {
  return requestSystemJson<ExternalAgentLoginSession>(
    `/api/system/external-agents/${provider}/login`,
    { method: "POST" },
    baseUrl,
  );
}

export async function disconnectExternalAgent(
  provider: ExternalAgentProviderName,
  baseUrl?: string,
): Promise<ExternalAgentProviderStatus> {
  return requestSystemJson<ExternalAgentProviderStatus>(
    `/api/system/external-agents/${provider}/disconnect`,
    { method: "POST" },
    baseUrl,
  );
}

export async function getExternalAgentLoginSession(
  sessionId: string,
  baseUrl?: string,
): Promise<ExternalAgentLoginSession> {
  return requestSystemJson<ExternalAgentLoginSession>(
    `/api/system/external-agents/sessions/${sessionId}`,
    { method: "GET" },
    baseUrl,
  );
}

export async function submitExternalAgentLoginInput(
  sessionId: string,
  payload: ExternalAgentLoginInputRequest,
  baseUrl?: string,
): Promise<ExternalAgentLoginSession> {
  return requestSystemJson<ExternalAgentLoginSession>(
    `/api/system/external-agents/sessions/${sessionId}/input`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    baseUrl,
  );
}
