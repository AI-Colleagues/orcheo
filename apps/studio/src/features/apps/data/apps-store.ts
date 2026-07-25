import { useEffect, useState } from "react";
import {
  createHostedApp,
  getHostedApp,
  listAppAudit,
  listBindings,
  listCollections,
  listDeployments,
  listHostedApps,
  publishHostedApp,
  unpublishHostedApp,
  uploadHostedAppBundle,
  type AppDeploymentApi,
  type AppAuditApi,
  type AppBindingApi,
  type AppCollectionApi,
  type HostedAppApi,
} from "./apps-api";
import type { HostedApp } from "./sample-apps";

const listeners = new Set<() => void>();

const notify = () => {
  for (const listener of listeners) listener();
};

const relativeTime = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "unknown" : date.toLocaleString();
};

const deployment = (item: AppDeploymentApi, index: number) => ({
  id: item.id,
  version: `deployment ${index + 1}`,
  digest: item.manifest_sha256
    ? `sha256:${item.manifest_sha256.slice(0, 12)}`
    : item.status,
  size: "—",
  files: 0,
  created: relativeTime(item.created_at),
  active: false,
  status: item.status,
});

const appFromApi = (
  item: HostedAppApi,
  deployments: AppDeploymentApi[] = [],
  bindings: AppBindingApi[] = [],
  collections: AppCollectionApi[] = [],
  audit: AppAuditApi[] = [],
): HostedApp => ({
  id: item.id,
  name: item.name,
  alias: item.alias,
  visibility: item.visibility,
  state: item.state,
  health: item.state === "suspended" ? "error" : "unknown",
  updated: relativeTime(item.updated_at),
  deployments: deployments.map(deployment),
  bindings: bindings.map((binding) => ({
    name: binding.name,
    workflow: binding.workflow_id,
    version: binding.workflow_version_id.slice(0, 8),
    rate: binding.limits.per_app_per_minute
      ? `${binding.limits.per_app_per_minute}/min`
      : "workspace limits",
    access: binding.access_mode,
    digest: binding.workflow_execution_sha256,
  })),
  collections: collections.map((collection) => ({
    name: collection.name,
    read: collection.read_access,
    write: collection.write_access,
    access: collection.scope === "shared" ? "shared" : "private",
  })),
  permissionRevision: item.permission_revision,
  publishedPermissionRevision: item.published_permission_revision,
  audit: audit.map((event) => ({
    id: event.id,
    action: event.action,
    actor: event.actor,
    created: relativeTime(event.created_at),
  })),
});

export const createApp = async (
  name: string,
  alias: string,
): Promise<HostedApp> => {
  const created = appFromApi(await createHostedApp(name, alias));
  notify();
  return created;
};

export const toggleAppPublish = async (app: HostedApp): Promise<void> => {
  if (app.state === "published") {
    await unpublishHostedApp(app.id);
  } else {
    const ready = app.deployments.find((item) => item.status === "ready");
    if (!ready) {
      throw new Error("Upload and validate a deployment before publishing.");
    }
    await publishHostedApp(app.id, ready.id, app.permissionRevision);
  }
  notify();
};

export const uploadAppBundle = async (
  appId: string,
  bundle: File,
): Promise<void> => {
  await uploadHostedAppBundle(appId, bundle);
  notify();
};

export function useApps(workspaceKey: string | undefined): {
  apps: HostedApp[];
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<{
    apps: HostedApp[];
    loading: boolean;
    error: string | null;
  }>({ apps: [], loading: true, error: null });
  useEffect(() => {
    let active = true;
    const load = () => {
      setState((current) => ({ ...current, loading: true, error: null }));
      void listHostedApps()
        .then((items) => {
          if (active)
            setState({
              apps: items.map((item) => appFromApi(item)),
              loading: false,
              error: null,
            });
        })
        .catch((error: unknown) => {
          if (active)
            setState({
              apps: [],
              loading: false,
              error:
                error instanceof Error ? error.message : "Unable to load apps.",
            });
        });
    };
    listeners.add(load);
    load();
    return () => {
      active = false;
      listeners.delete(load);
    };
  }, [workspaceKey]);
  return state;
}

export function useApp(
  id: string | undefined,
  workspaceKey: string | undefined,
): {
  app: HostedApp | undefined;
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<{
    app: HostedApp | undefined;
    loading: boolean;
    error: string | null;
  }>({ app: undefined, loading: true, error: null });
  useEffect(() => {
    let active = true;
    const load = () => {
      if (!id) {
        setState({ app: undefined, loading: false, error: null });
        return;
      }
      setState((current) => ({ ...current, loading: true, error: null }));
      void Promise.all([
        getHostedApp(id),
        listDeployments(id),
        listBindings(id),
        listCollections(id),
        listAppAudit(id),
      ])
        .then(([item, deployments, bindings, collections, audit]) => {
          if (active)
            setState({
              app: appFromApi(item, deployments, bindings, collections, audit),
              loading: false,
              error: null,
            });
        })
        .catch((error: unknown) => {
          if (active)
            setState({
              app: undefined,
              loading: false,
              error:
                error instanceof Error ? error.message : "Unable to load app.",
            });
        });
    };
    listeners.add(load);
    load();
    return () => {
      active = false;
      listeners.delete(load);
    };
  }, [id, workspaceKey]);
  return state;
}
