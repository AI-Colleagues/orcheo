interface AuthTokens {
  accessToken: string;
  idToken?: string;
  refreshToken?: string;
  tokenType?: string;
  expiresAt?: number;
}

interface DevAuthSession {
  provider: string;
  subject: string;
  displayName: string;
}

export interface AuthenticatedUserProfile {
  subject: string | null;
  name: string;
  email: string | null;
  avatar: string | null;
  role: string | null;
}

const AUTH_TOKENS_KEY = "orcheo_canvas_auth_tokens";
const DEV_AUTH_SESSION_KEY = "orcheo_canvas_dev_auth_session";
const DEV_AUTH_SESSION_COOKIE = "orcheo_canvas_dev_auth_session";
const DEV_AUTH_SESSION_GLOBAL_KEY = "__ORCHEO_DEV_AUTH_SESSION__";
const TOKEN_EXPIRY_SKEW_MS = 60_000;

const safeLocalStorageGet = (key: string): string | null => {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const safeLocalStorageSet = (key: string, value: string | null): void => {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (value === null) {
      window.localStorage.removeItem(key);
      return;
    }

    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
};

const readDocumentCookie = (name: string): string | null => {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${name}=`;
  const entries = document.cookie.split(";").map((entry) => entry.trim());
  for (const entry of entries) {
    if (entry.startsWith(prefix)) {
      return entry.slice(prefix.length);
    }
  }
  return null;
};

const readUrlDevSession = (): string | null => {
  if (typeof window === "undefined") {
    return null;
  }
  const params = new URLSearchParams(window.location.search);
  const value = params.get("dev_session");
  if (!value) {
    return null;
  }
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

const writeDocumentCookie = (name: string, value: string | null): void => {
  if (typeof document === "undefined") {
    return;
  }
  const base = `${name}=`;
  if (value === null) {
    document.cookie = `${base}; Max-Age=0; path=/`;
    return;
  }
  document.cookie = `${base}${value}; Max-Age=${7 * 24 * 60 * 60}; path=/`;
};

const parseTokens = (raw: string | null): AuthTokens | null => {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<AuthTokens>;
    if (!parsed.accessToken) {
      return null;
    }
    return {
      accessToken: parsed.accessToken,
      idToken: parsed.idToken,
      refreshToken: parsed.refreshToken,
      tokenType: parsed.tokenType,
      expiresAt: parsed.expiresAt,
    };
  } catch {
    return null;
  }
};

const parseDevAuthSession = (raw: string | null): DevAuthSession | null => {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<DevAuthSession>;
    if (
      typeof parsed.provider !== "string" ||
      typeof parsed.subject !== "string" ||
      typeof parsed.displayName !== "string"
    ) {
      return null;
    }
    const provider = parsed.provider.trim();
    const subject = parsed.subject.trim();
    const displayName = parsed.displayName.trim();
    if (!provider || !subject || !displayName) {
      return null;
    }
    return { provider, subject, displayName };
  } catch {
    return null;
  }
};

const readDevAuthSessionGlobal = (): DevAuthSession | null => {
  if (typeof globalThis === "undefined") {
    return null;
  }
  const candidate = (globalThis as Record<string, unknown>)[
    DEV_AUTH_SESSION_GLOBAL_KEY
  ];
  if (typeof candidate !== "string") {
    return null;
  }
  return parseDevAuthSession(candidate);
};

const writeDevAuthSessionGlobal = (session: DevAuthSession | null): void => {
  if (typeof globalThis === "undefined") {
    return;
  }
  if (session === null) {
    delete (globalThis as Record<string, unknown>)[DEV_AUTH_SESSION_GLOBAL_KEY];
    return;
  }
  (globalThis as Record<string, unknown>)[DEV_AUTH_SESSION_GLOBAL_KEY] =
    JSON.stringify(session);
};

const isTokenFresh = (tokens: AuthTokens | null): boolean => {
  if (!tokens?.accessToken) {
    return false;
  }
  if (!tokens.expiresAt) {
    return true;
  }
  return Date.now() < tokens.expiresAt - TOKEN_EXPIRY_SKEW_MS;
};

export const getAuthTokens = (): AuthTokens | null =>
  parseTokens(safeLocalStorageGet(AUTH_TOKENS_KEY));

export const setAuthTokens = (tokens: AuthTokens): void => {
  safeLocalStorageSet(AUTH_TOKENS_KEY, JSON.stringify(tokens));
};

export const getDevAuthSession = (): DevAuthSession | null =>
  readDevAuthSessionGlobal() ??
  parseDevAuthSession(
    safeLocalStorageGet(DEV_AUTH_SESSION_KEY) ??
      (() => {
        const cookie = readDocumentCookie(DEV_AUTH_SESSION_COOKIE);
        if (!cookie) {
          return readUrlDevSession();
        }
        try {
          return decodeURIComponent(cookie);
        } catch {
          return cookie;
        }
      })(),
  );

export const getDevAuthSessionHeaderValue = (): string | null => {
  const session = getDevAuthSession();
  if (!session) {
    return null;
  }
  return JSON.stringify(session);
};

export const setDevAuthSession = (session: DevAuthSession): void => {
  const serialized = JSON.stringify(session);
  writeDevAuthSessionGlobal(session);
  safeLocalStorageSet(DEV_AUTH_SESSION_KEY, serialized);
  writeDocumentCookie(DEV_AUTH_SESSION_COOKIE, encodeURIComponent(serialized));
};

export const clearDevAuthSession = (): void => {
  writeDevAuthSessionGlobal(null);
  safeLocalStorageSet(DEV_AUTH_SESSION_KEY, null);
  writeDocumentCookie(DEV_AUTH_SESSION_COOKIE, null);
};

export const clearAuthSession = (): void => {
  safeLocalStorageSet(AUTH_TOKENS_KEY, null);
  clearDevAuthSession();
};

export const getAccessToken = (): string | null => {
  const tokens = getAuthTokens();
  if (!isTokenFresh(tokens)) {
    clearAuthSession();
    return null;
  }
  return tokens.accessToken;
};

const decodeBase64Url = (value: string): string | null => {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  try {
    const binary = atob(`${base64}${padding}`);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
};

const parseJwtPayload = (token?: string): Record<string, unknown> | null => {
  if (!token) {
    return null;
  }
  const parts = token.split(".");
  if (parts.length < 2) {
    return null;
  }

  const payload = decodeBase64Url(parts[1]);
  if (!payload) {
    return null;
  }

  try {
    const parsed = JSON.parse(payload) as unknown;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
};

const readStringClaim = (
  claims: Record<string, unknown> | null,
  key: string,
): string | null => {
  if (!claims) {
    return null;
  }
  const value = claims[key];
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed || null;
};

const firstStringClaim = (
  claimSets: Array<Record<string, unknown> | null>,
  keys: string[],
): string | null => {
  for (const claims of claimSets) {
    for (const key of keys) {
      const value = readStringClaim(claims, key);
      if (value) {
        return value;
      }
    }
  }
  return null;
};

const resolveCompositeName = (
  claimSets: Array<Record<string, unknown> | null>,
): string | null => {
  for (const claims of claimSets) {
    const givenName = readStringClaim(claims, "given_name");
    const familyName = readStringClaim(claims, "family_name");
    if (givenName && familyName) {
      return `${givenName} ${familyName}`;
    }
    if (givenName) {
      return givenName;
    }
    if (familyName) {
      return familyName;
    }
  }
  return null;
};

const resolveRole = (
  claimSets: Array<Record<string, unknown> | null>,
): string | null => {
  const stringRole = firstStringClaim(claimSets, [
    "role",
    "https://orcheo.ai/role",
  ]);
  if (stringRole) {
    return stringRole;
  }

  for (const claims of claimSets) {
    if (!claims) {
      continue;
    }
    const candidates = [
      claims.roles,
      claims["https://orcheo.ai/roles"],
    ] as const;

    for (const candidate of candidates) {
      if (!Array.isArray(candidate)) {
        continue;
      }
      for (const entry of candidate) {
        if (typeof entry !== "string") {
          continue;
        }
        const trimmed = entry.trim();
        if (trimmed) {
          return trimmed;
        }
      }
    }
  }

  return null;
};

export const getAuthenticatedUserProfile =
  (): AuthenticatedUserProfile | null => {
  const accessToken = getAccessToken();
  if (!accessToken) {
    const devSession = getDevAuthSession();
    if (!devSession) {
      return null;
    }
    return {
      subject: devSession.subject,
      name: devSession.displayName,
      email: devSession.subject.includes("@") ? devSession.subject : null,
      avatar: null,
      role: "member",
    };
  }

    const tokens = getAuthTokens();
    const claimSets = [
      parseJwtPayload(tokens?.idToken),
      parseJwtPayload(accessToken),
    ];

    const subject = firstStringClaim(claimSets, ["sub"]);
    const email = firstStringClaim(claimSets, ["email"]);
    const name =
      firstStringClaim(claimSets, ["name", "preferred_username", "nickname"]) ??
      resolveCompositeName(claimSets) ??
      email ??
      subject;

    if (!name) {
      return null;
    }

    return {
      subject,
      name,
      email,
      avatar: firstStringClaim(claimSets, ["picture", "avatar", "avatar_url"]),
      role: resolveRole(claimSets),
    };
  };

export const getAccessTokenSubject = (): string | null => {
  const token = getAccessToken();
  if (!token) {
    return null;
  }

  const payload = parseJwtPayload(token);
  if (!payload) {
    return null;
  }

  const subject = payload.sub;
  if (typeof subject !== "string") {
    return null;
  }
  const trimmed = subject.trim();
  return trimmed || null;
};

export const isAuthenticated = (): boolean =>
  Boolean(getAccessToken() || getDevAuthSession());
