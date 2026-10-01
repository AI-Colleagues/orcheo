import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  logoutSession,
  refreshSession,
  startEmailChallenge,
  verifyEmailCode,
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

  it("persists tokens after verifying a sign-in code", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        access_token: "access-1",
        refresh_token: "refresh-1",
        expires_in: 900,
        user: { id: "u1", email: "alice@example.com", email_verified: true },
      }),
    );
    const user = await verifyEmailCode("alice@example.com", "123456");
    expect(user?.email).toBe("alice@example.com");
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      email: "alice@example.com",
      code: "123456",
    });
    const stored = getAuthTokens();
    expect(stored?.accessToken).toBe("access-1");
    expect(stored?.refreshToken).toBe("refresh-1");
    expect(stored?.expiresAt).toBeGreaterThan(Date.now());
  });

  it("surfaces the backend error message on verify failure", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: { message: "Invalid or expired challenge." } },
        400,
      ),
    );
    await expect(verifyEmailCode("alice@example.com", "bad")).rejects.toThrow(
      /invalid or expired/i,
    );
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

  it("shares one in-flight refresh across concurrent callers", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    let resolveRefresh: (response: Response) => void = () => undefined;
    fetchMock.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        resolveRefresh = resolve;
      }),
    );

    const first = refreshSession();
    const second = refreshSession();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveRefresh(
      jsonResponse({
        access_token: "new",
        refresh_token: "r-new",
        expires_in: 900,
      }),
    );

    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(getAuthTokens()?.refreshToken).toBe("r-new");
  });

  it("does not clear a newer session when a stale refresh fails", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockImplementationOnce(async () => {
      setAuthTokens({ accessToken: "new", refreshToken: "r-new" });
      return jsonResponse({}, 401);
    });

    expect(await refreshSession()).toBe(false);
    expect(getAuthTokens()).toMatchObject({
      accessToken: "new",
      refreshToken: "r-new",
    });
  });

  it("returns false and clears the session on refresh failure", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401));
    expect(await refreshSession()).toBe(false);
    expect(getAuthTokens()).toBeNull();
  });

  it.each([400, 403, 429, 500, 502, 503, 504, 524])(
    "preserves the session when refresh returns %s",
    async (status) => {
      setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
      fetchMock.mockResolvedValueOnce(jsonResponse({}, status));
      expect(await refreshSession()).toBe(false);
      expect(getAuthTokens()?.refreshToken).toBe("r-old");
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("preserves the session after a lost or malformed refresh response", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockResolvedValueOnce(
      new Response("incomplete JSON", { status: 200 }),
    );
    expect(await refreshSession()).toBe(false);
    expect(getAuthTokens()?.refreshToken).toBe("r-old");
  });

  it.each([{}, { access_token: "new" }, null])(
    "preserves the session when the token response is incomplete: %s",
    async (payload) => {
      setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
      fetchMock.mockResolvedValueOnce(jsonResponse(payload));
      expect(await refreshSession()).toBe(false);
      expect(getAuthTokens()?.refreshToken).toBe("r-old");
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("preserves the session on a network failure", async () => {
    setAuthTokens({ accessToken: "old", refreshToken: "r-old" });
    fetchMock.mockRejectedValueOnce(new TypeError("network error"));
    expect(await refreshSession()).toBe(false);
    expect(getAuthTokens()?.refreshToken).toBe("r-old");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("returns false without a stored refresh token", async () => {
    expect(await refreshSession()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refreshes an expired token before revoking on logout", async () => {
    setAuthTokens({
      accessToken: "expired",
      refreshToken: "refresh-1",
      expiresAt: Date.now() - 1000,
    });
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          access_token: "fresh-access",
          refresh_token: "fresh-refresh",
          expires_in: 900,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await logoutSession();

    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/auth/refresh");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/api/auth/logout");
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      Authorization: "Bearer fresh-access",
    });
    expect(getAuthTokens()).toBeNull();
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
