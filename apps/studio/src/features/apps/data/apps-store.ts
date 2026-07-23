import { useSyncExternalStore } from "react";
import { SAMPLE_APPS, type HostedApp } from "./sample-apps";

let apps: HostedApp[] = SAMPLE_APPS;
const listeners = new Set<() => void>();

const emit = () => {
  for (const listener of listeners) {
    listener();
  }
};

const subscribe = (listener: () => void): (() => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export const getApps = (): HostedApp[] => apps;

export const getAppById = (id: string): HostedApp | undefined =>
  apps.find((app) => app.id === id);

export const createApp = (name: string, alias: string): HostedApp => {
  const app: HostedApp = {
    id: `app-${Date.now().toString(36)}`,
    name,
    alias,
    visibility: "public",
    state: "draft",
    health: "unknown",
    updated: "just now",
    deployments: [],
    bindings: [],
    collections: [],
  };
  apps = [app, ...apps];
  emit();
  return app;
};

export const toggleAppPublish = (id: string): void => {
  apps = apps.map((app) =>
    app.id === id
      ? {
          ...app,
          state: app.state === "published" ? "unpublished" : "published",
          updated: "just now",
        }
      : app,
  );
  emit();
};

export function useApps(): HostedApp[] {
  return useSyncExternalStore(subscribe, getApps);
}

export function useApp(id: string | undefined): HostedApp | undefined {
  const list = useApps();
  return id ? list.find((app) => app.id === id) : undefined;
}
