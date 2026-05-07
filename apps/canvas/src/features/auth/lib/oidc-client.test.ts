import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  completeOidcLogin,
  startOidcLogin,
  tryRefreshTokens,
} from "@features/auth/lib/oidc-client";
import {
  clearAuthSession,
  getAuthTokens,
  setAuthTokens,
} from "@features/auth/lib/auth-session";

const mutableEnv = import.meta.env as unknown as Record<string, unknown>;
const originalEnv = { ...mutableEnv };

const setEnv = (key: string, value: string | undefined): void => {
  if (value === undefined) {
    delete mutableEnv[key];
    return;
  }
  mutableEnv[key] = value;
};

const restoreEnv = (): void => {
  Object.keys(mutableEnv).forEach((key) => {
    if (!(key in originalEnv)) {
      delete mutableEnv[key];
    }
  });
  Object.entries(originalEnv).forEach(([key, value]) => {
    mutableEnv[key] = value;
  });
};

const toBase64Url = (value: Record<string, unknown>): string => {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  encoded.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
};

const createJwt = (payload: Record<string, unknown>): string =>
  `${toBase64Url({ alg: "HS256", typ: "JWT" })}.${toBase64Url(payload)}.sig`;

const oidcDiscoveryResponse = {
  authorization_endpoint: "https://issuer.example.com/authorize",
  token_endpoint: "https://issuer.example.com/token",
};

describe("startOidcLogin organization precedence", () => {
  beforeEach(() => {
    setEnv("VITE_ORCHEO_AUTH_ISSUER", "https://issuer.example.com");
    setEnv("VITE_ORCHEO_AUTH_CLIENT_ID", "canvas-client");
    setEnv(
      "VITE_ORCHEO_AUTH_REDIRECT_URI",
      "https://canvas.example.com/auth/callback",
    );
    setEnv("VITE_ORCHEO_AUTH_SCOPES", "openid profile email");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => oidcDiscoveryResponse,
      }),
    );
  });

  afterEach(() => {
    restoreEnv();
    clearAuthSession();
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses configured organization even when a different one is passed", async () => {
    setEnv("VITE_ORCHEO_AUTH_ORGANIZATION", "org_configured");
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({
      organization: "org_attacker",
      organizationName: "attacker-name",
    });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("organization")).toBe("org_configured");
    expect(authUrl.searchParams.get("organization_name")).toBeNull();
  });

  it("uses runtime organization when no configured organization is set", async () => {
    setEnv("VITE_ORCHEO_AUTH_ORGANIZATION", undefined);
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({
      organization: "org_runtime",
      organizationName: "runtime-name",
    });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("organization")).toBe("org_runtime");
    expect(authUrl.searchParams.get("organization_name")).toBe("runtime-name");
  });

  it("passes through the signup screen hint", async () => {
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({ screenHint: "signup", signup: true });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("screen_hint")).toBe("signup");
  });

  it("does not force a GitHub connection during sign-up", async () => {
    setEnv("VITE_ORCHEO_AUTH_PROVIDER_PARAM", "connection");
    setEnv("VITE_ORCHEO_AUTH_PROVIDER_GITHUB", "github");
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({ screenHint: "signup", signup: true });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("connection")).toBeNull();
    expect(authUrl.searchParams.get("screen_hint")).toBe("signup");
  });

  it("includes the prompt parameter when supplied", async () => {
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({ prompt: "login" });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("prompt")).toBe("login");
  });

  it("omits the prompt parameter when not supplied", async () => {
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({});

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.has("prompt")).toBe(false);
  });

  it("uses the configured signup provider when available", async () => {
    setEnv("VITE_ORCHEO_AUTH_PROVIDER_PARAM", "connection");
    setEnv("VITE_ORCHEO_AUTH_PROVIDER_SIGNUP", "Username-Password-Authentication");
    const assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });

    await startOidcLogin({ screenHint: "signup", signup: true });

    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const authUrl = new URL(redirectUrl);

    expect(authUrl.searchParams.get("connection")).toBe(
      "Username-Password-Authentication",
    );
  });
});

describe("completeOidcLogin expiry parsing", () => {
  let assignMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setEnv("VITE_ORCHEO_AUTH_ISSUER", "https://issuer.example.com");
    setEnv("VITE_ORCHEO_AUTH_CLIENT_ID", "canvas-client");
    setEnv(
      "VITE_ORCHEO_AUTH_REDIRECT_URI",
      "https://canvas.example.com/auth/callback",
    );
    setEnv("VITE_ORCHEO_AUTH_SCOPES", "openid profile email");
    assignMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign: assignMock });
  });

  afterEach(() => {
    restoreEnv();
    clearAuthSession();
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses numeric exp claim when expires_in is missing", async () => {
    const exp = Math.floor(Date.now() / 1000) + 900;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            access_token: createJwt({ sub: "user|123", exp }),
            token_type: "Bearer",
          }),
        }),
    );

    await startOidcLogin({});
    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const state = new URL(redirectUrl).searchParams.get("state");
    expect(state).toBeTruthy();

    await completeOidcLogin({ code: "auth-code", state: state ?? "" });

    expect(getAuthTokens()?.expiresAt).toBe(exp * 1000);
  });

  it("ignores non-numeric exp claims", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            access_token: createJwt({ sub: "user|456", exp: "900" }),
            token_type: "Bearer",
          }),
        }),
    );

    await startOidcLogin({});
    const [redirectUrl] = assignMock.mock.calls[0] as [string];
    const state = new URL(redirectUrl).searchParams.get("state");
    expect(state).toBeTruthy();

    await completeOidcLogin({ code: "auth-code", state: state ?? "" });

    expect(getAuthTokens()?.expiresAt).toBeUndefined();
  });
});

describe("tryRefreshTokens", () => {
  beforeEach(() => {
    setEnv("VITE_ORCHEO_AUTH_ISSUER", "https://issuer.example.com");
    setEnv("VITE_ORCHEO_AUTH_CLIENT_ID", "canvas-client");
    setEnv(
      "VITE_ORCHEO_AUTH_REDIRECT_URI",
      "https://canvas.example.com/auth/callback",
    );
  });

  afterEach(() => {
    restoreEnv();
    clearAuthSession();
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("returns false when no refresh token is stored", async () => {
    const result = await tryRefreshTokens();
    expect(result).toBe(false);
  });

  it("returns false when OIDC is not configured", async () => {
    setEnv("VITE_ORCHEO_AUTH_ISSUER", undefined);
    setAuthTokens({ accessToken: "at", refreshToken: "rt" });

    const result = await tryRefreshTokens();
    expect(result).toBe(false);
  });

  it("returns false when discovery fetch fails", async () => {
    setAuthTokens({ accessToken: "at", refreshToken: "rt" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network")));

    const result = await tryRefreshTokens();
    expect(result).toBe(false);
  });

  it("clears the session and returns false when the token endpoint rejects the refresh token", async () => {
    setAuthTokens({ accessToken: "at_old", refreshToken: "rt_expired" });
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({ ok: false }),
    );

    const result = await tryRefreshTokens();

    expect(result).toBe(false);
    expect(getAuthTokens()).toBeNull();
  });

  it("stores new tokens and returns true on success", async () => {
    const exp = Math.floor(Date.now() / 1000) + 3600;
    setAuthTokens({ accessToken: "at_old", refreshToken: "rt_valid" });
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            access_token: createJwt({ sub: "user|1", exp }),
            refresh_token: "rt_new",
            token_type: "Bearer",
          }),
        }),
    );

    const result = await tryRefreshTokens();

    expect(result).toBe(true);
    const tokens = getAuthTokens();
    expect(tokens?.refreshToken).toBe("rt_new");
    expect(tokens?.expiresAt).toBe(exp * 1000);
  });

  it("keeps the old refresh token when the server does not rotate it", async () => {
    setAuthTokens({ accessToken: "at_old", refreshToken: "rt_static" });
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => oidcDiscoveryResponse,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            access_token: createJwt({ sub: "user|1" }),
            token_type: "Bearer",
          }),
        }),
    );

    const result = await tryRefreshTokens();

    expect(result).toBe(true);
    expect(getAuthTokens()?.refreshToken).toBe("rt_static");
  });
});
