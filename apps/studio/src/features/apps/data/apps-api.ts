import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";

export interface HostedAppApi {
  id: string;
  workspace_id: string;
  alias: string;
  name: string;
  description: string | null;
  visibility: "public" | "private";
  publication_state: "draft" | "published" | "unpublished";
  state: "draft" | "published" | "unpublished" | "suspended" | "archived";
  is_archived: boolean;
  active_release_id: string | null;
  active_deployment_id: string | null;
  permission_revision: number;
  published_permission_revision: number | null;
  url: string;
  created_at: string;
  updated_at: string;
}

export interface AppPublishApi {
  app_id: string;
  active_release_id: string;
  active_deployment_id: string;
  published_permission_revision: number;
  state: "published";
  url: string;
}

export interface AppDeploymentApi {
  id: string;
  status: "pending" | "validating" | "ready" | "failed" | "expired";
  archive_sha256: string | null;
  manifest_sha256: string | null;
  app_manifest: {
    schema_version: 1;
    bindings: Record<
      string,
      {
        workflow: string;
        version: number;
        access_mode: "anonymous" | "authenticated";
        input_schema: Record<string, unknown>;
        output_projection: Record<string, unknown>;
        visitor_can_read_output: boolean;
        visitor_can_read_sanitized_errors: boolean;
        limits: Record<string, number>;
      }
    >;
  } | null;
  validation_error_code: string | null;
  validation_error_message: string | null;
  created_at: string;
}

export interface AppBindingApi {
  id: string;
  name: string;
  workflow_id: string;
  workflow_version_id: string;
  workflow_execution_sha256: string;
  access_mode: "anonymous" | "authenticated";
  limits: Record<string, number>;
}

export interface AppCollectionApi {
  id: string;
  name: string;
  scope: "shared" | "user";
  read_access: "anonymous" | "authenticated";
  write_access: "anonymous" | "authenticated";
}

export interface AppAuditApi {
  id: string;
  action: string;
  actor: string;
  created_at: string;
}

const request = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
  const isMultipart = init.body instanceof FormData;
  const response = await authFetch(buildBackendHttpUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body && !isMultipart
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: { message?: string } | string;
    } | null;
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : (detail?.message ??
          `Hosted Apps request failed (${response.status}).`);
    throw new Error(message);
  }
  return response.json() as Promise<T>;
};

export const listHostedApps = async (): Promise<HostedAppApi[]> => {
  const response = await request<{ apps: HostedAppApi[] }>("/api/apps");
  return response.apps;
};

export const getHostedApp = (id: string): Promise<HostedAppApi> =>
  request<HostedAppApi>(`/api/apps/${id}`);

export const createHostedApp = (
  name: string,
  alias: string,
): Promise<HostedAppApi> =>
  request<HostedAppApi>("/api/apps", {
    method: "POST",
    body: JSON.stringify({ name, alias }),
  });

export const listDeployments = (id: string): Promise<AppDeploymentApi[]> =>
  request<AppDeploymentApi[]>(`/api/apps/${id}/deployments`);

export const uploadHostedAppBundle = (
  id: string,
  bundle: File,
): Promise<AppDeploymentApi> => {
  const body = new FormData();
  body.append("bundle", bundle);
  return request<AppDeploymentApi>(`/api/apps/${id}/deployments/upload`, {
    method: "POST",
    body,
  });
};

export const listBindings = (id: string): Promise<AppBindingApi[]> =>
  request<AppBindingApi[]>(`/api/apps/${id}/bindings`);

export const listCollections = (id: string): Promise<AppCollectionApi[]> =>
  request<AppCollectionApi[]>(`/api/apps/${id}/collections`);

export const listAppAudit = (id: string): Promise<AppAuditApi[]> =>
  request<AppAuditApi[]>(`/api/apps/${id}/audit`);

export const unpublishHostedApp = (id: string): Promise<HostedAppApi> =>
  request<HostedAppApi>(`/api/apps/${id}/unpublish`, { method: "POST" });

export const archiveHostedApp = (id: string): Promise<HostedAppApi> =>
  request<HostedAppApi>(`/api/apps/${id}/archive`, { method: "POST" });

export const publishHostedApp = (
  appId: string,
  deploymentId: string,
  permissionRevision: number,
  visibility: HostedAppApi["visibility"],
): Promise<AppPublishApi> =>
  request<AppPublishApi>(
    `/api/apps/${appId}/deployments/${deploymentId}/publish`,
    {
      method: "POST",
      body: JSON.stringify({
        acknowledged_permission_revision: permissionRevision,
        visibility,
      }),
    },
  );
