import {
  WebAuthnAbortService,
  browserSupportsWebAuthn,
  browserSupportsWebAuthnAutofill,
  platformAuthenticatorIsAvailable,
} from "@simplewebauthn/browser";
import { AuthApiError } from "@features/auth/lib/auth-api";

/**
 * Whether this browser can use passkeys at all. Browsers only expose WebAuthn
 * in secure contexts, so plain-http deployments (other than localhost) can't.
 */
export const passkeysSupported = (): boolean => browserSupportsWebAuthn();

/** Whether saved passkeys can be offered in the email field's autofill. */
export const passkeyAutofillSupported = async (): Promise<boolean> =>
  passkeysSupported() && (await browserSupportsWebAuthnAutofill());

/** Whether this device can create a passkey with its own screen lock. */
export const platformPasskeyAvailable = async (): Promise<boolean> => {
  if (!passkeysSupported()) {
    return false;
  }
  try {
    return await platformAuthenticatorIsAvailable();
  } catch {
    return false;
  }
};

/** Cancel a pending passkey prompt, such as the autofill request. */
export const cancelPasskeyPrompt = (): void => {
  WebAuthnAbortService.cancelCeremony();
};

// Passkey failures are WebAuthnErrors (from @simplewebauthn/browser, with a
// `code` saying why) or DOMExceptions, which are not Error instances in every
// environment, so read the fields from any error-like object.
const errorField = (
  error: unknown,
  field: "name" | "code",
): string | undefined => {
  if (typeof error !== "object" || error === null) {
    return undefined;
  }
  const value = (error as Record<string, unknown>)[field];
  return typeof value === "string" ? value : undefined;
};

/** True when the app itself cancelled a pending passkey prompt. */
export const isPasskeyPromptCancelled = (error: unknown): boolean =>
  errorField(error, "name") === "AbortError" ||
  errorField(error, "code") === "ERROR_CEREMONY_ABORTED";

/**
 * True when the passkey prompt can never work on this page, for example when
 * Studio is opened on a different host than the one the server expects.
 */
export const isPasskeyOriginMismatch = (error: unknown): boolean => {
  const code = errorField(error, "code");
  return code === "ERROR_INVALID_RP_ID" || code === "ERROR_INVALID_DOMAIN";
};

/**
 * A message for a failed passkey prompt, or null when there is nothing to tell
 * the person (the app cancelled the prompt itself).
 */
export const passkeyErrorMessage = (
  error: unknown,
  fallback: string,
): string | null => {
  if (isPasskeyPromptCancelled(error)) {
    return null;
  }
  if (error instanceof AuthApiError) {
    return error.message;
  }
  if (errorField(error, "name") === "NotAllowedError") {
    return "The passkey prompt was closed or timed out.";
  }
  if (
    errorField(error, "code") === "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED"
  ) {
    return "This device already has a passkey for your account.";
  }
  if (isPasskeyOriginMismatch(error)) {
    return "Passkeys can't be used at this address.";
  }
  return fallback;
};
