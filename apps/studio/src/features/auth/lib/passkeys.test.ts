import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authFetch: vi.fn(),
  startRegistration: vi.fn(),
  sendSignal: vi.fn(),
  supported: vi.fn(() => true),
  platform: vi.fn(() => Promise.resolve(true)),
}));

vi.mock("@/lib/auth-fetch", () => ({ authFetch: mocks.authFetch }));

vi.mock("@simplewebauthn/browser", () => ({
  startRegistration: mocks.startRegistration,
  sendSignal: mocks.sendSignal,
  startAuthentication: vi.fn(),
  browserSupportsWebAuthn: () => mocks.supported(),
  browserSupportsWebAuthnAutofill: () => Promise.resolve(false),
  platformAuthenticatorIsAvailable: () => mocks.platform(),
  WebAuthnAbortService: { cancelCeremony: vi.fn() },
}));

import {
  deletePasskey,
  dismissPasskeyOffer,
  listPasskeys,
  registerPasskey,
  renamePasskey,
  shouldOfferPasskey,
  syncAcceptedPasskeys,
  type PasskeyList,
} from "./passkeys";

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const passkey = {
  id: "p-1",
  name: "Laptop",
  credential_id: "cred-1",
  transports: ["internal"],
  backup_eligible: true,
  backed_up: true,
  created_at: "2026-10-06T12:00:00Z",
  last_used_at: null,
};

const list = (overrides: Partial<PasskeyList> = {}): PasskeyList => ({
  rp_id: "orcheo.example.com",
  user_handle: "handle",
  passkeys: [passkey],
  ...overrides,
});

beforeEach(() => {
  window.localStorage.clear();
  mocks.sendSignal.mockResolvedValue(undefined);
  mocks.supported.mockReturnValue(true);
  mocks.platform.mockResolvedValue(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

const call = (index: number) => {
  const [url, init, options] = mocks.authFetch.mock.calls[index] as [
    string,
    RequestInit,
    { includeWorkspaceHeaders: boolean },
  ];
  return { url: String(url), init, options };
};

describe("passkey management", () => {
  it("lists passkeys without workspace headers", async () => {
    mocks.authFetch.mockResolvedValueOnce(jsonResponse(list()));

    await expect(listPasskeys()).resolves.toEqual(list());

    const { url, options } = call(0);
    expect(url).toContain("/api/auth/passkeys");
    expect(options).toEqual({ includeWorkspaceHeaders: false });
  });

  it("creates and registers a passkey", async () => {
    const options = { challenge: "abc", rp: { id: "orcheo.example.com" } };
    const attestation = { id: "cred-1", rawId: "cred-1", response: {} };
    mocks.authFetch
      .mockResolvedValueOnce(
        jsonResponse({ challenge_id: "challenge-1", options }),
      )
      .mockResolvedValueOnce(jsonResponse(passkey, 201));
    mocks.startRegistration.mockResolvedValueOnce(attestation);

    await expect(registerPasskey("Laptop")).resolves.toEqual(passkey);

    expect(call(0).url).toContain("/api/auth/passkey/register/options");
    expect(call(0).init).toEqual({ method: "POST" });
    expect(mocks.startRegistration).toHaveBeenCalledWith({
      optionsJSON: options,
    });
    expect(call(1).url).toContain("/api/auth/passkey/register/verify");
    expect(JSON.parse(call(1).init.body as string)).toEqual({
      challenge_id: "challenge-1",
      credential: attestation,
      name: "Laptop",
    });
  });

  it("asks for a fresh sign-in before adding a passkey", async () => {
    mocks.authFetch.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: "auth.reauthentication_required",
            message: "Confirm it's you.",
          },
        },
        403,
      ),
    );

    await expect(registerPasskey()).rejects.toMatchObject({
      status: 403,
      code: "auth.reauthentication_required",
    });
    expect(mocks.startRegistration).not.toHaveBeenCalled();
  });

  it("renames and removes passkeys", async () => {
    mocks.authFetch
      .mockResolvedValueOnce(jsonResponse({ ...passkey, name: "Desk" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(renamePasskey("p 1", "Desk")).resolves.toMatchObject({
      name: "Desk",
    });
    await expect(deletePasskey("p 1")).resolves.toBeUndefined();

    expect(call(0).url).toContain("/api/auth/passkeys/p%201");
    expect(call(0).init.method).toBe("PATCH");
    expect(JSON.parse(call(0).init.body as string)).toEqual({ name: "Desk" });
    expect(call(1).init).toEqual({ method: "DELETE" });
  });

  it("surfaces failures with the backend message", async () => {
    mocks.authFetch.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: "auth.passkey_not_found",
            message: "Passkey not found.",
          },
        },
        404,
      ),
    );

    await expect(deletePasskey("missing")).rejects.toThrow(
      "Passkey not found.",
    );
  });

  it("tells the browser which passkeys still work", () => {
    syncAcceptedPasskeys(list());
    syncAcceptedPasskeys(list({ rp_id: null }));

    expect(mocks.sendSignal).toHaveBeenCalledTimes(1);
    expect(mocks.sendSignal).toHaveBeenCalledWith({
      signalName: "allAcceptedCredentials",
      rpID: "orcheo.example.com",
      userID: "handle",
      allAcceptedCredentialIDs: ["cred-1"],
    });
  });

  it("ignores browsers without the signal API", async () => {
    mocks.sendSignal.mockRejectedValueOnce(new Error("unsupported"));

    expect(() => syncAcceptedPasskeys(list())).not.toThrow();
    await Promise.resolve();
  });
});

describe("shouldOfferPasskey", () => {
  it("offers a passkey to people without one on capable devices", async () => {
    mocks.authFetch.mockResolvedValueOnce(jsonResponse(list({ passkeys: [] })));

    await expect(shouldOfferPasskey()).resolves.toBe(true);
  });

  it("does not offer a passkey to people who already have one", async () => {
    mocks.authFetch.mockResolvedValueOnce(jsonResponse(list()));

    await expect(shouldOfferPasskey()).resolves.toBe(false);
  });

  it("does not offer a passkey when the server can't use them", async () => {
    mocks.authFetch.mockResolvedValueOnce(
      jsonResponse(list({ rp_id: null, passkeys: [] })),
    );

    await expect(shouldOfferPasskey()).resolves.toBe(false);
  });

  it("does not offer a passkey when listing fails", async () => {
    mocks.authFetch.mockRejectedValueOnce(new TypeError("network down"));

    await expect(shouldOfferPasskey()).resolves.toBe(false);
  });

  it("does not offer a passkey on devices that can't make one", async () => {
    mocks.platform.mockResolvedValueOnce(false);

    await expect(shouldOfferPasskey()).resolves.toBe(false);
    expect(mocks.authFetch).not.toHaveBeenCalled();
  });

  it("remembers when the offer was declined", async () => {
    dismissPasskeyOffer();

    await expect(shouldOfferPasskey()).resolves.toBe(false);
    expect(mocks.authFetch).not.toHaveBeenCalled();
  });

  it("copes without browser storage", async () => {
    const blocked = vi
      .spyOn(window, "localStorage", "get")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    mocks.authFetch.mockResolvedValueOnce(jsonResponse(list({ passkeys: [] })));

    expect(() => dismissPasskeyOffer()).not.toThrow();
    await expect(shouldOfferPasskey()).resolves.toBe(true);

    blocked.mockRestore();
  });
});
