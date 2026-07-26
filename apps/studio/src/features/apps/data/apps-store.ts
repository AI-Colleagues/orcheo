import { useCallback, useSyncExternalStore } from "react";
import {
  SAMPLE_APPS,
  SAMPLE_APPS_WORKSPACE_SLUG,
  type HostedApp,
} from "./sample-apps";

const RESERVED_ALIASES = new Set([
  "admin",
  "api",
  "auth",
  "mail",
  "studio",
  "support",
  "www",
]);
const EMPTY_APPS: readonly HostedApp[] = Object.freeze([]);

let appsByWorkspace = new Map<string, readonly HostedApp[]>();
let listenersByWorkspace = new Map<string, Set<() => void>>();
let fallbackIdSequence = 0;

const cloneApp = (app: HostedApp): HostedApp => ({
  ...app,
  deployments: app.deployments.map((deployment) => ({ ...deployment })),
  bindings: app.bindings.map((binding) => ({ ...binding })),
  collections: app.collections.map((collection) => ({ ...collection })),
});

const freezeApp = (app: HostedApp): HostedApp => {
  app.deployments.forEach(Object.freeze);
  app.bindings.forEach(Object.freeze);
  app.collections.forEach(Object.freeze);
  Object.freeze(app.deployments);
  Object.freeze(app.bindings);
  Object.freeze(app.collections);
  return Object.freeze(app);
};

const normalizeWorkspaceSlug = (
  workspaceSlug: string | undefined,
): string | null => {
  const normalized = workspaceSlug?.trim();
  return normalized || null;
};

const ensureWorkspaceApps = (workspaceSlug: string): readonly HostedApp[] => {
  const existing = appsByWorkspace.get(workspaceSlug);
  if (existing) {
    return existing;
  }

  const initialApps =
    workspaceSlug === SAMPLE_APPS_WORKSPACE_SLUG
      ? Object.freeze(SAMPLE_APPS.map((app) => freezeApp(cloneApp(app))))
      : EMPTY_APPS;
  appsByWorkspace.set(workspaceSlug, initialApps);
  return initialApps;
};

const emit = (workspaceSlug: string) => {
  for (const listener of listenersByWorkspace.get(workspaceSlug) ?? []) {
    listener();
  }
};

const subscribe = (
  workspaceSlug: string | null,
  listener: () => void,
): (() => void) => {
  if (!workspaceSlug) {
    return () => undefined;
  }
  const listeners =
    listenersByWorkspace.get(workspaceSlug) ?? new Set<() => void>();
  listeners.add(listener);
  listenersByWorkspace.set(workspaceSlug, listeners);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      listenersByWorkspace.delete(workspaceSlug);
    }
  };
};

export const getApps = (
  workspaceSlug: string | undefined,
): readonly HostedApp[] => {
  const normalized = normalizeWorkspaceSlug(workspaceSlug);
  return normalized ? ensureWorkspaceApps(normalized) : EMPTY_APPS;
};

export const getAppById = (
  workspaceSlug: string | undefined,
  id: string,
): HostedApp | undefined => getApps(workspaceSlug).find((app) => app.id === id);

export const validateAppAlias = (alias: string): string | null => {
  if (alias.length < 3 || alias.length > 48) {
    return "Alias must contain between 3 and 48 characters.";
  }
  if (!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(alias)) {
    return "Alias may contain lowercase letters, numbers, and internal hyphens.";
  }
  if (RESERVED_ALIASES.has(alias)) {
    return `"${alias}" is reserved and cannot be used.`;
  }
  return null;
};

const appAliasExists = (alias: string): boolean => {
  for (const apps of appsByWorkspace.values()) {
    if (apps.some((app) => app.alias === alias)) {
      return true;
    }
  }
  return false;
};

const createAppId = (): string => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `app-${crypto.randomUUID()}`;
  }
  fallbackIdSequence += 1;
  return `app-${Date.now().toString(36)}-${fallbackIdSequence.toString(36)}`;
};

export const createApp = (
  workspaceSlug: string | undefined,
  name: string,
  alias: string,
): HostedApp => {
  const normalizedWorkspace = normalizeWorkspaceSlug(workspaceSlug);
  if (!normalizedWorkspace) {
    throw new Error("A workspace is required to create an app.");
  }
  const normalizedName = name.trim();
  const normalizedAlias = alias.trim().toLowerCase();
  if (!normalizedName) {
    throw new Error("An app name is required.");
  }
  const aliasError = validateAppAlias(normalizedAlias);
  if (aliasError) {
    throw new Error(aliasError);
  }
  const apps = getApps(normalizedWorkspace);
  if (appAliasExists(normalizedAlias)) {
    throw new Error(`The alias "${normalizedAlias}" is already in use.`);
  }

  const app = freezeApp({
    id: createAppId(),
    name: normalizedName,
    alias: normalizedAlias,
    visibility: "public",
    state: "draft",
    health: "unknown",
    updated: "just now",
    deployments: [],
    bindings: [],
    collections: [],
  });
  appsByWorkspace.set(normalizedWorkspace, Object.freeze([app, ...apps]));
  emit(normalizedWorkspace);
  return app;
};

export const getPublishBlockedReason = (app: HostedApp): string | null => {
  if (app.state !== "draft" && app.state !== "unpublished") {
    return `A ${app.state} app cannot be published.`;
  }
  if (!app.deployments.some((deployment) => deployment.active)) {
    return "An active deployment is required before publishing.";
  }
  return null;
};

export const canPublishApp = (app: HostedApp): boolean =>
  getPublishBlockedReason(app) === null;

export const toggleAppPublish = (
  workspaceSlug: string | undefined,
  id: string,
): void => {
  const normalizedWorkspace = normalizeWorkspaceSlug(workspaceSlug);
  if (!normalizedWorkspace) {
    throw new Error("A workspace is required to change app publication.");
  }
  const existing = getAppById(normalizedWorkspace, id);
  if (!existing) {
    throw new Error(`App "${id}" was not found in this workspace.`);
  }
  if (existing.state !== "published") {
    const blockedReason = getPublishBlockedReason(existing);
    if (blockedReason) {
      throw new Error(blockedReason);
    }
  }

  const apps = getApps(normalizedWorkspace);
  const nextApps = apps.map((app) =>
    app.id === id
      ? freezeApp({
          ...app,
          state: app.state === "published" ? "unpublished" : "published",
          updated: "just now",
        })
      : app,
  );
  appsByWorkspace.set(normalizedWorkspace, Object.freeze(nextApps));
  emit(normalizedWorkspace);
};

export function useApps(
  workspaceSlug: string | undefined,
): readonly HostedApp[] {
  const normalizedWorkspace = normalizeWorkspaceSlug(workspaceSlug);
  const subscribeToWorkspace = useCallback(
    (listener: () => void) => subscribe(normalizedWorkspace, listener),
    [normalizedWorkspace],
  );
  const getSnapshot = useCallback(
    () => getApps(normalizedWorkspace ?? undefined),
    [normalizedWorkspace],
  );
  return useSyncExternalStore(subscribeToWorkspace, getSnapshot, getSnapshot);
}

export function useApp(
  workspaceSlug: string | undefined,
  id: string | undefined,
): HostedApp | undefined {
  const list = useApps(workspaceSlug);
  return id ? list.find((app) => app.id === id) : undefined;
}

export const resetAppsStoreForTests = (): void => {
  appsByWorkspace = new Map();
  listenersByWorkspace = new Map();
  fallbackIdSequence = 0;
};
