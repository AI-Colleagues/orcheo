import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  supported: vi.fn(() => true),
  autofill: vi.fn(() => Promise.resolve(true)),
  platform: vi.fn(() => Promise.resolve(true)),
  cancelCeremony: vi.fn(),
}));

vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => mocks.supported(),
  browserSupportsWebAuthnAutofill: () => mocks.autofill(),
  platformAuthenticatorIsAvailable: () => mocks.platform(),
  WebAuthnAbortService: { cancelCeremony: () => mocks.cancelCeremony() },
  startAuthentication: vi.fn(),
  sendSignal: vi.fn(),
}));

import { AuthApiError } from "./auth-api";
import {
  cancelPasskeyPrompt,
  isPasskeyOriginMismatch,
  isPasskeyPromptCancelled,
  passkeyAutofillSupported,
  passkeyErrorMessage,
  passkeysSupported,
  platformPasskeyAvailable,
} from "./passkey-support";

const webAuthnError = (name: string, code?: string) =>
  Object.assign(new Error(`${name} failure`), { name, code });

afterEach(() => {
  vi.clearAllMocks();
  mocks.supported.mockReturnValue(true);
  mocks.autofill.mockResolvedValue(true);
  mocks.platform.mockResolvedValue(true);
});

describe("passkey support", () => {
  it("detects what the browser can do", async () => {
    expect(passkeysSupported()).toBe(true);
    await expect(passkeyAutofillSupported()).resolves.toBe(true);
    await expect(platformPasskeyAvailable()).resolves.toBe(true);
  });

  it("reports nothing on browsers without WebAuthn", async () => {
    mocks.supported.mockReturnValue(false);

    expect(passkeysSupported()).toBe(false);
    await expect(passkeyAutofillSupported()).resolves.toBe(false);
    await expect(platformPasskeyAvailable()).resolves.toBe(false);
    expect(mocks.autofill).not.toHaveBeenCalled();
    expect(mocks.platform).not.toHaveBeenCalled();
  });

  it("treats a failing platform check as unavailable", async () => {
    mocks.platform.mockRejectedValueOnce(new Error("boom"));

    await expect(platformPasskeyAvailable()).resolves.toBe(false);
  });

  it("cancels a pending prompt", () => {
    cancelPasskeyPrompt();

    expect(mocks.cancelCeremony).toHaveBeenCalled();
  });

  it("recognizes cancelled prompts", () => {
    expect(isPasskeyPromptCancelled(new DOMException("x", "AbortError"))).toBe(
      true,
    );
    expect(
      isPasskeyPromptCancelled(
        webAuthnError("Error", "ERROR_CEREMONY_ABORTED"),
      ),
    ).toBe(true);
    expect(isPasskeyPromptCancelled(webAuthnError("NotAllowedError"))).toBe(
      false,
    );
    expect(isPasskeyPromptCancelled("AbortError")).toBe(false);
  });

  it("recognizes prompts this address can't run", () => {
    expect(
      isPasskeyOriginMismatch(
        webAuthnError("SecurityError", "ERROR_INVALID_RP_ID"),
      ),
    ).toBe(true);
    expect(
      isPasskeyOriginMismatch(
        webAuthnError("SecurityError", "ERROR_INVALID_DOMAIN"),
      ),
    ).toBe(true);
    expect(isPasskeyOriginMismatch(webAuthnError("SecurityError"))).toBe(false);
    expect(isPasskeyOriginMismatch({ code: "ERROR_INVALID_RP_ID" })).toBe(true);
    expect(isPasskeyOriginMismatch(null)).toBe(false);
  });

  it.each([
    [new DOMException("x", "AbortError"), null],
    [new AuthApiError("Server says no.", 400), "Server says no."],
    [
      webAuthnError("NotAllowedError", "ERROR_PASSTHROUGH_SEE_CAUSE_PROPERTY"),
      "The passkey prompt was closed or timed out.",
    ],
    [
      webAuthnError(
        "InvalidStateError",
        "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED",
      ),
      "This device already has a passkey for your account.",
    ],
    [
      webAuthnError("SecurityError", "ERROR_INVALID_RP_ID"),
      "Passkeys can't be used at this address.",
    ],
    [
      webAuthnError("UnknownError", "ERROR_AUTHENTICATOR_GENERAL_ERROR"),
      "Fallback.",
    ],
    ["not an error", "Fallback."],
  ])("describes %s", (error, expected) => {
    expect(passkeyErrorMessage(error, "Fallback.")).toBe(expected);
  });
});
