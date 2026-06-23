import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAuthSession,
  setAuthTokens,
} from "@features/auth/lib/auth-session";
import { authFetch } from "./auth-fetch";

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
  clearAuthSession();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("authFetch", () => {
  it("refreshes an expired access token before sending the request", async () => {
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
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const response = await authFetch("/api/protected");

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/auth/refresh");
    expect(
      (fetchMock.mock.calls[1][1].headers as Headers).get("Authorization"),
    ).toBe("Bearer fresh-access");
  });

  it("retries once with a fresh token after a 401", async () => {
    setAuthTokens({
      accessToken: "stale",
      refreshToken: "refresh-1",
      expiresAt: Date.now() + 120_000,
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(
        jsonResponse({
          access_token: "fresh-access",
          refresh_token: "fresh-refresh",
          expires_in: 900,
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ ok: true }));

    const response = await authFetch("/api/protected");

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      (fetchMock.mock.calls[2][1].headers as Headers).get("Authorization"),
    ).toBe("Bearer fresh-access");
  });
});
