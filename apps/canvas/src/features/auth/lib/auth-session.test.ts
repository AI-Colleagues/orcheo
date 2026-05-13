import { afterEach, describe, expect, it } from "vitest";
import {
  clearAuthSession,
  getAuthenticatedUserProfile,
  getAuthTokens,
  setAuthTokens,
  setDevAuthSession,
} from "./auth-session";

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

describe("getAuthenticatedUserProfile", () => {
  afterEach(() => {
    clearAuthSession();
    window.localStorage.clear();
  });

  it("prefers identity claims from the id token when available", () => {
    setAuthTokens({
      accessToken: createJwt({
        sub: "access|123",
        name: "Access Name",
      }),
      idToken: createJwt({
        sub: "id|123",
        name: "Morgan Lee",
        email: "morgan@example.com",
        picture: "https://example.com/avatar.png",
        roles: ["Admin", "Member"],
      }),
      expiresAt: Date.now() + 5 * 60_000,
    });

    expect(getAuthenticatedUserProfile()).toEqual({
      subject: "id|123",
      name: "Morgan Lee",
      email: "morgan@example.com",
      avatar: "https://example.com/avatar.png",
      role: "Admin",
    });
  });

  it("falls back to access token claims when id token is absent", () => {
    setAuthTokens({
      accessToken: createJwt({
        sub: "auth0|abc",
        preferred_username: "canvas-user",
        email: "canvas@example.com",
        profile_picture: "https://example.com/avatar-access.png",
        role: "Editor",
      }),
      expiresAt: Date.now() + 5 * 60_000,
    });

    expect(getAuthenticatedUserProfile()).toEqual({
      subject: "auth0|abc",
      name: "canvas-user",
      email: "canvas@example.com",
      avatar: "https://example.com/avatar-access.png",
      role: "Editor",
    });
  });

  it("decodes UTF-8 names from JWT payloads", () => {
    setAuthTokens({
      accessToken: createJwt({ sub: "utf8|123" }),
      idToken: createJwt({
        sub: "utf8|123",
        name: "José Núñez",
      }),
      expiresAt: Date.now() + 5 * 60_000,
    });

    expect(getAuthenticatedUserProfile()?.name).toBe("José Núñez");
  });

  it("returns null and clears persisted tokens when access token is expired", () => {
    setAuthTokens({
      accessToken: createJwt({ sub: "expired|123", name: "Expired User" }),
      expiresAt: Date.now() - 1_000,
    });

    expect(getAuthenticatedUserProfile()).toBeNull();
    expect(getAuthTokens()).toBeNull();
  });

  it("uses the developer session when no JWT tokens are stored", () => {
    setDevAuthSession({
      provider: "local",
      subject: "local@orcheo.local",
      displayName: "Local Developer",
    });

    expect(getAuthenticatedUserProfile()).toEqual({
      subject: "local@orcheo.local",
      name: "Local Developer",
      email: "local@orcheo.local",
      avatar: null,
      role: "member",
    });
  });
});
