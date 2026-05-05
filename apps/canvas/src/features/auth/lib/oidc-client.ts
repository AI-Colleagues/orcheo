import { setAuthTokens } from "@features/auth/lib/auth-session";

type AuthProvider = "google" | "github";

interface OidcInviteContext {
  invitation?: string;
  organization?: string;
  organizationName?: string;
  loginHint?: string;
  screenHint?: string;
}

interface OidcDiscovery {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

interface OidcAuthConfig {
  issuer: string;
  clientId: string;
  redirectUri: string;
  scopes: string;
  audience?: string;
  organization?: string;
  providerParam?: string;
  providerValues: Partial<Record<AuthProvider, string>>;
}

const AUTH_STATE_KEY = "orcheo_canvas_oidc_state";
const AUTH_VERIFIER_KEY = "orcheo_canvas_oidc_verifier";
const AUTH_REDIRECT_KEY = "orcheo_canvas_oidc_redirect";
const AUTH_STATE_ISSUED_AT_KEY = "orcheo_canvas_oidc_state_issued_at";

const DEFAULT_STATE_BYTES = 32;
const DEFAULT_VERIFIER_BYTES = 64;
const MIN_STATE_BYTES = 16;
const MAX_STATE_BYTES = 96;
const MIN_VERIFIER_BYTES = 32;
const MAX_VERIFIER_BYTES = 96;
const AUTH_STATE_TTL_MS = 10 * 60 * 1000;

const BASE64_URL_PATTERN = /^[A-Za-z0-9_-]+$/;

const readEnv = (key: string): string | undefined => {
  const value = (import.meta.env?.[key] ?? "") as string;
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return undefined;
};

const readEnvInt = (key: string): number | undefined => {
  const value = readEnv(key);
  if (!value) {
    return undefined;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return undefined;
  }
  return parsed;
};

const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, "");

const isLocalhostHost = (hostname: string): boolean =>
  hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";

const parseUrl = (value: string, label: string): URL => {
  try {
    return new URL(value);
  } catch {
    throw new Error(`${label} must be a valid URL.`);
  }
};

const validateUrl = (
  value: string,
  label: string,
  { allowHttpLocalhost }: { allowHttpLocalhost: boolean },
): string => {
  const parsed = parseUrl(value, label);
  const isHttp = parsed.protocol === "http:";
  const isHttps = parsed.protocol === "https:";
  if (
    !isHttps &&
    !(allowHttpLocalhost && isHttp && isLocalhostHost(parsed.hostname))
  ) {
    throw new Error(`${label} must use https (or http for localhost).`);
  }
  return value.trim();
};

const readEnvUrl = (
  key: string,
  label: string,
  { allowHttpLocalhost }: { allowHttpLocalhost: boolean },
): string | undefined => {
  const value = readEnv(key);
  if (!value) {
    return undefined;
  }
  return validateUrl(value, label, { allowHttpLocalhost });
};

const resolveRandomByteLength = (
  key: string,
  fallback: number,
  min: number,
  max: number,
): number => {
  const configured = readEnvInt(key);
  if (configured === undefined) {
    return fallback;
  }
  if (configured < min || configured > max) {
    console.warn(
      `${key} must be between ${min} and ${max}. Falling back to ${fallback}.`,
    );
    return fallback;
  }
  return configured;
};

const base64UrlLength = (bytes: number): number => Math.ceil((bytes * 4) / 3);

const STATE_BYTES = resolveRandomByteLength(
  "VITE_ORCHEO_AUTH_STATE_BYTES",
  DEFAULT_STATE_BYTES,
  MIN_STATE_BYTES,
  MAX_STATE_BYTES,
);
const VERIFIER_BYTES = resolveRandomByteLength(
  "VITE_ORCHEO_AUTH_VERIFIER_BYTES",
  DEFAULT_VERIFIER_BYTES,
  MIN_VERIFIER_BYTES,
  MAX_VERIFIER_BYTES,
);
const MIN_STATE_LENGTH = base64UrlLength(STATE_BYTES);
const MIN_VERIFIER_LENGTH = base64UrlLength(VERIFIER_BYTES);

const assertValidBase64Url = (
  value: string,
  label: string,
  minLength: number,
): void => {
  if (!BASE64_URL_PATTERN.test(value) || value.length < minLength) {
    throw new Error(`${label} is invalid.`);
  }
};

const resolveRedirectUri = (): string => {
  const fromEnv = readEnvUrl("VITE_ORCHEO_AUTH_REDIRECT_URI", "Redirect URI", {
    allowHttpLocalhost: true,
  });
  if (fromEnv) {
    return fromEnv;
  }
  if (typeof window !== "undefined") {
    return `${window.location.origin}/auth/callback`;
  }
  return "http://localhost:5173/auth/callback";
};

const getAuthConfig = (): OidcAuthConfig => {
  const issuer = readEnvUrl("VITE_ORCHEO_AUTH_ISSUER", "Issuer", {
    allowHttpLocalhost: true,
  });
  const clientId = readEnv("VITE_ORCHEO_AUTH_CLIENT_ID");
  if (!issuer || !clientId) {
    throw new Error(
      "OAuth is not configured. Set VITE_ORCHEO_AUTH_ISSUER and VITE_ORCHEO_AUTH_CLIENT_ID.",
    );
  }
  const scopes = readEnv("VITE_ORCHEO_AUTH_SCOPES") ?? "openid profile email";
  const audience = readEnv("VITE_ORCHEO_AUTH_AUDIENCE");
  const organization = readEnv("VITE_ORCHEO_AUTH_ORGANIZATION");
  const providerParam = readEnv("VITE_ORCHEO_AUTH_PROVIDER_PARAM");
  const providerValues: Partial<Record<AuthProvider, string>> = {
    google: readEnv("VITE_ORCHEO_AUTH_PROVIDER_GOOGLE"),
    github: readEnv("VITE_ORCHEO_AUTH_PROVIDER_GITHUB"),
  };

  return {
    issuer: trimTrailingSlash(issuer),
    clientId,
    redirectUri: resolveRedirectUri(),
    scopes,
    audience,
    organization,
    providerParam,
    providerValues,
  };
};

const loadDiscovery = async (issuer: string): Promise<OidcDiscovery> => {
  const url = `${trimTrailingSlash(issuer)}/.well-known/openid-configuration`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load OAuth discovery metadata.");
  }
  return (await response.json()) as OidcDiscovery;
};

const base64UrlEncode = (input: ArrayBuffer): string => {
  const bytes = new Uint8Array(input);
  let binary = "";
  bytes.forEach((value) => {
    binary += String.fromCharCode(value);
  });
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
};

const createRandomString = (length = 32): string => {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes.buffer);
};

const sha256 = async (value: string): Promise<ArrayBuffer> => {
  const encoder = new TextEncoder();
  const data = encoder.encode(value);
  return crypto.subtle.digest("SHA-256", data);
};

interface JwtPayloadWithExpiry {
  exp?: unknown;
}

const isJwtPayloadWithExpiry = (
  value: unknown,
): value is JwtPayloadWithExpiry => typeof value === "object" && value !== null;

const parseJwtExpiry = (token?: string): number | undefined => {
  if (!token) {
    return undefined;
  }
  const parts = token.split(".");
  if (parts.length < 2) {
    return undefined;
  }
  try {
    const payload = JSON.parse(
      atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")),
    ) as unknown;
    if (!isJwtPayloadWithExpiry(payload)) {
      return undefined;
    }
    const exp = payload.exp;
    if (typeof exp !== "number" || !Number.isFinite(exp)) {
      return undefined;
    }
    return exp * 1000;
  } catch {
    return undefined;
  }
};

const storeOidcState = (state: string, verifier: string): void => {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(AUTH_STATE_KEY, state);
  window.sessionStorage.setItem(AUTH_VERIFIER_KEY, verifier);
  window.sessionStorage.setItem(
    AUTH_STATE_ISSUED_AT_KEY,
    Date.now().toString(),
  );
};

const storePostLoginRedirect = (redirectTo?: string): void => {
  if (typeof window === "undefined" || !redirectTo) {
    return;
  }
  window.sessionStorage.setItem(AUTH_REDIRECT_KEY, redirectTo);
};

export const consumePostLoginRedirect = (): string | null => {
  if (typeof window === "undefined") {
    return null;
  }
  const redirectTo = window.sessionStorage.getItem(AUTH_REDIRECT_KEY);
  if (redirectTo) {
    window.sessionStorage.removeItem(AUTH_REDIRECT_KEY);
  }
  return redirectTo;
};

const readOidcState = (): { state: string; verifier: string } => {
  if (typeof window === "undefined") {
    throw new Error("OAuth state missing.");
  }
  const state = window.sessionStorage.getItem(AUTH_STATE_KEY);
  const verifier = window.sessionStorage.getItem(AUTH_VERIFIER_KEY);
  const issuedAtRaw = window.sessionStorage.getItem(AUTH_STATE_ISSUED_AT_KEY);
  const issuedAt = issuedAtRaw ? Number.parseInt(issuedAtRaw, 10) : NaN;
  if (
    !state ||
    !verifier ||
    !Number.isFinite(issuedAt) ||
    Date.now() - issuedAt > AUTH_STATE_TTL_MS
  ) {
    clearOidcState();
    throw new Error("OAuth state missing.");
  }
  assertValidBase64Url(state, "OAuth state", MIN_STATE_LENGTH);
  assertValidBase64Url(verifier, "OAuth verifier", MIN_VERIFIER_LENGTH);
  return { state, verifier };
};

const clearOidcState = (): void => {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(AUTH_STATE_KEY);
  window.sessionStorage.removeItem(AUTH_VERIFIER_KEY);
  window.sessionStorage.removeItem(AUTH_STATE_ISSUED_AT_KEY);
};

const normalizeOptionalParam = (value?: string): string | undefined => {
  const normalized = value?.trim();
  return normalized || undefined;
};

export const startOidcLogin = async ({
  provider,
  redirectTo,
  invitation,
  organization,
  organizationName,
  loginHint,
  screenHint,
}: {
  provider?: AuthProvider;
  redirectTo?: string;
} & OidcInviteContext): Promise<void> => {
  const normalizedInvitation = normalizeOptionalParam(invitation);
  const normalizedOrganization = normalizeOptionalParam(organization);
  const normalizedOrganizationName = normalizeOptionalParam(organizationName);
  const normalizedLoginHint = normalizeOptionalParam(loginHint);
  const normalizedScreenHint = normalizeOptionalParam(screenHint);

  const config = getAuthConfig();
  // Configured workspace restriction must remain authoritative over URL params.
  const effectiveOrganization =
    (config.organization ?? normalizedOrganization) || undefined;
  const effectiveOrganizationName = config.organization
    ? undefined
    : normalizedOrganizationName || undefined;
  const discovery = await loadDiscovery(config.issuer);
  const state = createRandomString(STATE_BYTES);
  const verifier = createRandomString(VERIFIER_BYTES);
  const challenge = base64UrlEncode(await sha256(verifier));
  storeOidcState(state, verifier);
  storePostLoginRedirect(redirectTo);

  const url = new URL(discovery.authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("scope", config.scopes);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  if (effectiveOrganization) {
    url.searchParams.set("organization", effectiveOrganization);
  }
  if (effectiveOrganizationName) {
    url.searchParams.set("organization_name", effectiveOrganizationName);
  }
  if (normalizedInvitation) {
    url.searchParams.set("invitation", normalizedInvitation);
  }
  if (normalizedLoginHint) {
    url.searchParams.set("login_hint", normalizedLoginHint);
  }
  if (normalizedScreenHint) {
    url.searchParams.set("screen_hint", normalizedScreenHint);
  }
  if (config.audience) {
    url.searchParams.set("audience", config.audience);
  }
  if (provider && config.providerParam) {
    const providerValue = config.providerValues[provider] ?? provider;
    url.searchParams.set(config.providerParam, providerValue);
  }

  window.location.assign(url.toString());
};

export const completeOidcLogin = async ({
  code,
  state,
}: {
  code: string;
  state: string;
}): Promise<void> => {
  const config = getAuthConfig();
  const discovery = await loadDiscovery(config.issuer);
  assertValidBase64Url(state, "OAuth state", MIN_STATE_LENGTH);
  const stored = readOidcState();
  if (stored.state !== state) {
    clearOidcState();
    throw new Error("OAuth state mismatch.");
  }

  const body = new URLSearchParams();
  body.set("grant_type", "authorization_code");
  body.set("client_id", config.clientId);
  body.set("code", code);
  body.set("redirect_uri", config.redirectUri);
  body.set("code_verifier", stored.verifier);

  const response = await fetch(discovery.token_endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  clearOidcState();

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw new Error(
      errorText || "OAuth token exchange failed. Check your IdP settings.",
    );
  }

  const payload = (await response.json()) as {
    access_token?: string;
    id_token?: string;
    refresh_token?: string;
    token_type?: string;
    expires_in?: number;
  };

  if (!payload.access_token) {
    throw new Error("OAuth token response missing access token.");
  }

  const expiresIn = payload.expires_in;
  const expiryFromToken =
    parseJwtExpiry(payload.access_token) ?? parseJwtExpiry(payload.id_token);
  const expiresAt =
    typeof expiresIn === "number"
      ? Date.now() + expiresIn * 1000
      : expiryFromToken;

  setAuthTokens({
    accessToken: payload.access_token,
    idToken: payload.id_token,
    refreshToken: payload.refresh_token,
    tokenType: payload.token_type,
    expiresAt,
  });
};
