import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  logoutSession,
  refreshSession,
  startEmailChallenge,
  verifyEmailToken,
} from "./auth-api";
import { getAuthTokens, setAuthTokens } from "./auth-session";

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("auth-api", () => {
  it("posts to email/start and resolves on success", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "sent" }));
    await startEmailChallenge("alice@example.com", "signup");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/auth/email/start");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      email: "alice@example.com",
      intent: "signup",
    });
  });

  it("throws a friendly message on rate limit", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 429));
    await expect(startEmailChallenge("alice@example.com")).rejects.toThrow(
      /too many attempts/i,
    );
  });

  it("persists tokens after verifying a magic-link token", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        access_token: "access-1",
        refresh_token: "refresh-1",
        expires_in: 900,
        user: { id: "u1", email: "alice@example.com", email_verified: true },
      }),
    );
    const user = await verifyEmailToken("magic");
    expect(user?.email).toBe("alice@example.com");
    const stored = getAuthTokens();
    expect(stored?.accessToken).toBe("access-1");
    expect(stored?.refreshToken).toBe("refresh-1");
    expect(stored?.expiresAt).toBeGreaterThan(Date.now());
  });

  it("surfaces the backend error message on verify failure", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: { message: "Invalid or expired challenge." } }, 400),
    );
    await expect(verifyEmailToken("bad")).rejects.toThrow(/invalid or expired/i);
  });

  it("rotates tokens on refresh and returns true", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        access_token: "new",
        refresh_token: "r-new",
        expires_in: 900,
      }),
    );
    expect(await refreshSession()).toBe(true);
    expect(getAuthTokens()?.refreshToken).toBe("r-new");
  });

  it("returns false and clears the session on refresh failure", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401));
    expect(await refreshSession()).toBe(false);
    expect(getAuthTokens()).toBeNull();
  });

  it("returns false without a stored refresh token", async () => {
    expect(await refreshSession()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("revokes the server session and clears tokens on logout", async () => {
    setAuthTokens({ accessToken: "access-1", refreshToken: "refresh-1" });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await logoutSession();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/auth/logout");
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: "Bearer access-1",
    });
    expect(getAuthTokens()).toBeNull();
  });
});
