import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const webauthn = vi.hoisted(() => ({
  startAuthentication: vi.fn(),
  sendSignal: vi.fn(),
}));

vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: webauthn.startAuthentication,
  sendSignal: webauthn.sendSignal,
}));

import {
  AuthApiError,
  beginPasskeySignIn,
  completePasskeySignIn,
  toAuthApiError,
} from "./auth-api";
import { getAuthTokens } from "./auth-session";

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const fetchMock = vi.fn();
const assertion = { id: "cred-1", rawId: "cred-1", response: {} };
const request = {
  challengeId: "challenge-1",
  options: { challenge: "abc", rpId: "orcheo.example.com" },
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
  webauthn.startAuthentication.mockResolvedValue(assertion);
  webauthn.sendSignal.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("passkey sign-in", () => {
  it("starts a usernameless sign-in", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ challenge_id: "challenge-1", options: request.options }),
    );
    const controller = new AbortController();

    const begun = await beginPasskeySignIn(controller.signal);

    expect(begun).toEqual(request);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/auth/passkey/login/options");
    expect(init).toEqual({ method: "POST", signal: controller.signal });
  });

  it("reports when the server can't use passkeys", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: "auth.passkeys_unavailable",
            message: "Passkeys are not available on this server.",
          },
        },
        404,
      ),
    );

    const error = await beginPasskeySignIn().catch((err: unknown) => err);

    expect(error).toBeInstanceOf(AuthApiError);
    expect(error).toMatchObject({
      status: 404,
      code: "auth.passkeys_unavailable",
      message: "Passkeys are not available on this server.",
    });
  });

  it("verifies the passkey and stores the session", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        access_token: "access-1",
        refresh_token: "refresh-1",
        expires_in: 900,
        user: { id: "u1", email: "alice@example.com", email_verified: true },
      }),
    );

    const user = await completePasskeySignIn(request, {
      useBrowserAutofill: true,
    });

    expect(user?.email).toBe("alice@example.com");
    expect(webauthn.startAuthentication).toHaveBeenCalledWith({
      optionsJSON: request.options,
      useBrowserAutofill: true,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/auth/passkey/login/verify");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      challenge_id: "challenge-1",
      credential: assertion,
    });
    expect(getAuthTokens()?.accessToken).toBe("access-1");
  });

  it("asks the browser to forget passkeys Orcheo doesn't know", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: "auth.passkey_unknown",
            message: "This passkey is not registered with Orcheo.",
          },
        },
        400,
      ),
    );
    webauthn.sendSignal.mockRejectedValueOnce(new Error("unsupported"));

    await expect(completePasskeySignIn(request)).rejects.toMatchObject({
      code: "auth.passkey_unknown",
      status: 400,
    });
    expect(webauthn.startAuthentication).toHaveBeenCalledWith({
      optionsJSON: request.options,
      useBrowserAutofill: false,
    });
    expect(webauthn.sendSignal).toHaveBeenCalledWith({
      signalName: "unknownCredential",
      rpID: "orcheo.example.com",
      credentialID: "cred-1",
    });
    expect(getAuthTokens()).toBeNull();
  });

  it("only signals unknown passkeys when the relying party is known", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: { code: "auth.passkey_unknown" } }, 400),
    );

    await expect(
      completePasskeySignIn({
        challengeId: "challenge-1",
        options: { challenge: "abc" },
      }),
    ).rejects.toMatchObject({
      message: "That passkey could not be verified. Please try again.",
    });
    expect(webauthn.sendSignal).not.toHaveBeenCalled();
  });

  it("does not signal other verification failures", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: { code: "auth.passkey_invalid", message: "Nope." } },
        400,
      ),
    );

    await expect(completePasskeySignIn(request)).rejects.toThrow("Nope.");
    expect(webauthn.sendSignal).not.toHaveBeenCalled();
  });
});

describe("toAuthApiError", () => {
  it("uses a friendly message when rate limited", async () => {
    const error = await toAuthApiError(
      jsonResponse(
        {
          detail: {
            code: "auth.rate_limited.ip",
            message: "Too many authentication attempts from IP 10.0.0.1",
          },
        },
        429,
      ),
      "fallback",
    );

    expect(error.message).toBe(
      "Too many attempts. Please wait a moment and try again.",
    );
    expect(error.code).toBe("auth.rate_limited.ip");
  });

  it("reads plain-string and unreadable error bodies", async () => {
    const plain = await toAuthApiError(
      jsonResponse({ detail: "Not found" }, 404),
      "fallback",
    );
    const unreadable = await toAuthApiError(
      new Response("<html>", { status: 502 }),
      "fallback",
    );

    expect(plain).toMatchObject({ message: "Not found", code: undefined });
    expect(unreadable).toMatchObject({ message: "fallback", status: 502 });
  });
});
