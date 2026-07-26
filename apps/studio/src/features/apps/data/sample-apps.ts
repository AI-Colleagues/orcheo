import { getAppsBaseDomain } from "@/lib/config";

export type AppVisibility = "public" | "private";
export type AppState =
  "draft" | "published" | "unpublished" | "suspended" | "archived";
export type AppHealth = "healthy" | "unknown" | "error";
export type AppBindingAccess = "anonymous" | "authenticated";
export type AppCollectionAccess = "shared" | "private";

export interface AppDeployment {
  id: string;
  version: string;
  digest: string;
  size: string;
  files: number;
  created: string;
  active: boolean;
  status?: "pending" | "validating" | "ready" | "failed" | "expired";
  manifestBindings?: AppBinding[] | null;
}

export interface AppBinding {
  name: string;
  workflow: string;
  version: string;
  rate: string;
  access: AppBindingAccess;
  digest?: string;
}

export interface AppCollection {
  name: string;
  read: string;
  write: string;
  access: AppCollectionAccess;
}

export interface HostedApp {
  id: string;
  name: string;
  alias: string;
  visibility: AppVisibility;
  state: AppState;
  health: AppHealth;
  updated: string;
  deployments: AppDeployment[];
  bindings: AppBinding[];
  collections: AppCollection[];
  permissionRevision: number;
  publishedPermissionRevision?: number | null;
  audit?: { id: string; action: string; actor: string; created: string }[];
}

const getAppsPort = (baseDomain: string): string => {
  const configured = import.meta.env.VITE_ORCHEO_APPS_PORT?.trim();
  if (configured) return configured;
  return baseDomain === "localhost" || baseDomain.endsWith(".localhost")
    ? "2030"
    : "";
};

export const getHostedAppAddress = (alias: string): string => {
  const baseDomain = getAppsBaseDomain();
  const port = getAppsPort(baseDomain);
  return `${alias}.${baseDomain}${port ? `:${port}` : ""}`;
};

export const getHostedAppUrl = (alias: string): string => {
  const baseDomain = getAppsBaseDomain();
  const isLocal =
    baseDomain === "localhost" || baseDomain.endsWith(".localhost");
  return `${isLocal ? "http" : "https"}://${getHostedAppAddress(alias)}/`;
};

export const SAMPLE_APPS: HostedApp[] = [
  {
    id: "app-research-digest",
    name: "Research Digest",
    alias: "research-digest",
    visibility: "public",
    state: "published",
    health: "healthy",
    updated: "2 days ago",
    deployments: [
      {
        id: "dep-3",
        version: "v3",
        digest: "sha256:9f2a1c",
        size: "1.4 MB",
        files: 18,
        created: "2 days ago",
        active: true,
      },
      {
        id: "dep-2",
        version: "v2",
        digest: "sha256:7b0e44",
        size: "1.3 MB",
        files: 17,
        created: "9 days ago",
        active: false,
      },
      {
        id: "dep-1",
        version: "v1",
        digest: "sha256:1d88af",
        size: "1.1 MB",
        files: 14,
        created: "21 days ago",
        active: false,
      },
    ],
    bindings: [
      {
        name: "summarize",
        workflow: "Research Analyst",
        version: "4",
        rate: "30/min",
        access: "anonymous",
      },
    ],
    collections: [
      {
        name: "subscribers",
        read: "owner",
        write: "owner",
        access: "private",
      },
    ],
    permissionRevision: 1,
  },
  {
    id: "app-status-page",
    name: "Status Page",
    alias: "status",
    visibility: "public",
    state: "published",
    health: "healthy",
    updated: "1 week ago",
    deployments: [
      {
        id: "dep-1",
        version: "v1",
        digest: "sha256:44ac02",
        size: "480 KB",
        files: 6,
        created: "1 week ago",
        active: true,
      },
    ],
    bindings: [],
    collections: [],
    permissionRevision: 1,
  },
  {
    id: "app-internal-ops",
    name: "Internal Ops Console",
    alias: "internal-ops",
    visibility: "private",
    state: "unpublished",
    health: "unknown",
    updated: "3 weeks ago",
    deployments: [
      {
        id: "dep-2",
        version: "v2",
        digest: "sha256:0af921",
        size: "2.1 MB",
        files: 32,
        created: "3 weeks ago",
        active: false,
      },
      {
        id: "dep-1",
        version: "v1",
        digest: "sha256:c93a10",
        size: "1.9 MB",
        files: 28,
        created: "2 months ago",
        active: false,
      },
    ],
    bindings: [
      {
        name: "run-checks",
        workflow: "Ops Runbook",
        version: "2",
        rate: "10/min",
        access: "authenticated",
      },
      {
        name: "notify",
        workflow: "Incident Notifier",
        version: "1",
        rate: "5/min",
        access: "authenticated",
      },
    ],
    collections: [
      {
        name: "incidents",
        read: "member",
        write: "member",
        access: "shared",
      },
    ],
    permissionRevision: 1,
  },
  {
    id: "app-onboarding-demo",
    name: "Onboarding Demo",
    alias: "onboarding-demo",
    visibility: "public",
    state: "draft",
    health: "unknown",
    updated: "yesterday",
    deployments: [],
    bindings: [],
    collections: [],
    permissionRevision: 1,
  },
];
