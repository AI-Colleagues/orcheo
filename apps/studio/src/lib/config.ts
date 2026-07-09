const VITE_DEV_ORIGINS = new Set([
  "http://localhost:2026",
  "http://127.0.0.1:2026",
  // `vite preview`'s default port. It serves the built SPA standalone (not
  // colocated with the backend), so it must fall back to the dev backend
  // default rather than treating itself as the backend's own origin.
  "http://localhost:4173",
  "http://127.0.0.1:4173",
]);

const getDefaultBackendUrl = (): string => {
  if (typeof window === "undefined") {
    return "http://localhost:2025";
  }

  const origin = window.location.origin;
  if (origin && !VITE_DEV_ORIGINS.has(origin)) {
    return origin;
  }

  return "http://localhost:2025";
};

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, "");

const isPermittedProtocol = (protocol: string): boolean =>
  ["http:", "https:", "ws:", "wss:"].includes(protocol);

const isValidUrl = (value: string): boolean => {
  if (!value.trim()) {
    return false;
  }
  try {
    const parsed = new URL(value);
    return isPermittedProtocol(parsed.protocol);
  } catch {
    return false;
  }
};

const normaliseBaseUrl = (value: string): string => {
  if (!value) {
    return DEFAULT_BACKEND_URL;
  }
  const trimmed = value.trim();
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimTrailingSlash(trimmed);
  }
  if (trimmed.startsWith("ws://") || trimmed.startsWith("wss://")) {
    return trimTrailingSlash(trimmed);
  }
  return trimTrailingSlash(`http://${trimmed}`);
};

export const getBackendBaseUrl = (): string => {
  const fromEnv = (import.meta.env?.VITE_ORCHEO_BACKEND_URL ?? "") as string;
  const defaultBackendUrl = getDefaultBackendUrl();
  const candidate = fromEnv || defaultBackendUrl;
  const normalised = normaliseBaseUrl(candidate);

  if (fromEnv && !isValidUrl(normalised)) {
    console.warn(
      "Invalid VITE_ORCHEO_BACKEND_URL provided, falling back to default backend URL.",
    );
    return normaliseBaseUrl(defaultBackendUrl);
  }

  return normalised;
};

const ensureHttpProtocol = (baseUrl: string): string => {
  if (baseUrl.startsWith("http://") || baseUrl.startsWith("https://")) {
    return baseUrl;
  }
  if (baseUrl.startsWith("ws://")) {
    return `http://${baseUrl.slice(5)}`;
  }
  if (baseUrl.startsWith("wss://")) {
    return `https://${baseUrl.slice(6)}`;
  }
  return `http://${baseUrl}`;
};

export const buildBackendHttpUrl = (path: string, baseUrl?: string): string => {
  const resolved = ensureHttpProtocol(baseUrl ?? getBackendBaseUrl());
  const normalised = trimTrailingSlash(resolved);
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${normalised}${suffix}`;
};

export const getStudioVersion = (): string => __ORCHEO_STUDIO_VERSION__;

export const buildWorkflowWebSocketUrl = (
  workflowId: string,
  baseUrl?: string,
  authToken?: string | null,
): string => {
  const resolvedId = workflowId.trim();
  if (!resolvedId) {
    throw new Error("workflowId is required to create a WebSocket URL");
  }
  const resolved = normaliseBaseUrl(baseUrl ?? getBackendBaseUrl());
  const search = authToken
    ? `?${new URLSearchParams({ access_token: authToken }).toString()}`
    : "";
  if (resolved.startsWith("ws://") || resolved.startsWith("wss://")) {
    return `${trimTrailingSlash(resolved)}/ws/workflow/${resolvedId}${search}`;
  }
  const protocol = resolved.startsWith("https://") ? "wss://" : "ws://";
  const host = resolved.replace(/^https?:\/\//, "").replace(/^ws?:\/\//, "");
  return `${protocol}${trimTrailingSlash(host)}/ws/workflow/${resolvedId}${search}`;
};

const WEBSOCKET_AUTH_PROTOCOL = "orcheo-auth";
const WEBSOCKET_AUTH_PREFIX = "bearer.";

export const buildWorkflowWebSocketProtocols = (
  token?: string | null,
): string[] | undefined => {
  if (!token) {
    return undefined;
  }
  return [WEBSOCKET_AUTH_PROTOCOL, `${WEBSOCKET_AUTH_PREFIX}${token}`];
};
