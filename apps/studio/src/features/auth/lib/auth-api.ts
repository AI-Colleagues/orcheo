import {
  sendSignal,
  startAuthentication,
  type AuthenticationResponseJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";
import { buildBackendHttpUrl } from "@/lib/config";
import {
  clearAuthSession,
  getAuthTokens,
  setAuthTokens,
} from "@features/auth/lib/auth-session";

export interface AuthUserProfile {
  id: string;
  email: string;
  email_verified: boolean;
  name: string | null;
}

interface SessionPayload {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user?: AuthUserProfile;
}

interface TokenPayload {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

const authUrl = (path: string): string =>
  buildBackendHttpUrl(`/api/auth${path}`);

const persistTokens = (payload: TokenPayload): void => {
  if (
    !payload ||
    typeof payload.access_token !== "string" ||
    !payload.access_token.trim() ||
    typeof payload.refresh_token !== "string" ||
    !payload.refresh_token.trim()
  ) {
    throw new Error("Incomplete sign-in response.");
  }
  setAuthTokens({
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresAt:
      typeof payload.expires_in === "number"
        ? Date.now() + payload.expires_in * 1000
        : undefined,
  });
};

export interface RefreshResult {
  ok: boolean;
  response?: Response;
}

let refreshInFlight: Promise<RefreshResult> | null = null;

/** An auth API failure with its HTTP status and the backend's error code. */
export class AuthApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "AuthApiError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorDetail {
  message: string;
  code?: string;
}

const readErrorDetail = async (
  response: Response,
  fallback: string,
): Promise<ErrorDetail> => {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") {
      return { message: detail };
    }
    if (detail && typeof detail === "object") {
      const { message, code } = detail as { message?: unknown; code?: unknown };
      return {
        message:
          typeof message === "string" && message.trim() ? message : fallback,
        code: typeof code === "string" ? code : undefined,
      };
    }
  } catch {
    // fall through to the fallback message
  }
  return { message: fallback };
};

const readErrorMessage = async (
  response: Response,
  fallback: string,
): Promise<string> => (await readErrorDetail(response, fallback)).message;

/** Build an {@link AuthApiError} from a failed auth API response. */
export const toAuthApiError = async (
  response: Response,
  fallback: string,
): Promise<AuthApiError> => {
  const detail = await readErrorDetail(response, fallback);
  const message =
    response.status === 429
      ? "Too many attempts. Please wait a moment and try again."
      : detail.message;
  return new AuthApiError(message, response.status, detail.code);
};

/**
 * Email a one-time sign-in code. The backend responds identically whether or
 * not the account exists, so a resolved promise only means the request was
 * accepted — never that an email was definitely sent.
 */
export const startEmailChallenge = async (
  email: string,
  intent: "login" | "signup" = "login",
): Promise<void> => {
  const response = await fetch(authUrl("/email/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, intent }),
  });
  if (response.status === 429) {
    throw new Error("Too many attempts. Please wait a moment and try again.");
  }
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Unable to send the sign-in email."),
    );
  }
};

/** Verify an emailed sign-in code and start an authenticated session. */
export const verifyEmailCode = async (
  email: string,
  code: string,
): Promise<AuthUserProfile | undefined> => {
  const response = await fetch(authUrl("/email/verify"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "This sign-in code is invalid or has expired.",
      ),
    );
  }
  const payload = (await response.json()) as SessionPayload;
  persistTokens(payload);
  return payload.user;
};

/** A passkey sign-in whose challenge has been issued by the backend. */
export interface PasskeySignInRequest {
  challengeId: string;
  options: PublicKeyCredentialRequestOptionsJSON;
}

/**
 * Ask the backend to start a passkey sign-in. Passkey sign-in is usernameless,
 * so nothing about the account is sent. Rejects with an {@link AuthApiError}
 * (status 404) when the server cannot use passkeys.
 */
export const beginPasskeySignIn = async (
  signal?: AbortSignal,
): Promise<PasskeySignInRequest> => {
  const response = await fetch(authUrl("/passkey/login/options"), {
    method: "POST",
    signal,
  });
  if (!response.ok) {
    throw await toAuthApiError(response, "Passkey sign-in is unavailable.");
  }
  const payload = (await response.json()) as {
    challenge_id: string;
    options: PublicKeyCredentialRequestOptionsJSON;
  };
  return { challengeId: payload.challenge_id, options: payload.options };
};

const forgetUnknownPasskey = (
  options: PublicKeyCredentialRequestOptionsJSON,
  credential: AuthenticationResponseJSON,
): void => {
  if (!options.rpId) {
    return;
  }
  // Ask the password manager to stop offering a passkey Orcheo won't accept.
  void sendSignal({
    signalName: "unknownCredential",
    rpID: options.rpId,
    credentialID: credential.id,
  }).catch(() => undefined);
};

/**
 * Prompt for a passkey and sign in with it, storing the new session. With
 * `useBrowserAutofill`, the prompt is the email field's autofill list and the
 * promise stays pending until the person picks a passkey.
 */
export const completePasskeySignIn = async (
  request: PasskeySignInRequest,
  { useBrowserAutofill = false }: { useBrowserAutofill?: boolean } = {},
): Promise<AuthUserProfile | undefined> => {
  const credential = await startAuthentication({
    optionsJSON: request.options,
    useBrowserAutofill,
  });
  const response = await fetch(authUrl("/passkey/login/verify"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge_id: request.challengeId, credential }),
  });
  if (!response.ok) {
    const error = await toAuthApiError(
      response,
      "That passkey could not be verified. Please try again.",
    );
    if (error.code === "auth.passkey_unknown") {
      forgetUnknownPasskey(request.options, credential);
    }
    throw error;
  }
  const payload = (await response.json()) as SessionPayload;
  persistTokens(payload);
  return payload.user;
};

/**
 * Rotate the stored refresh token into a fresh access token. Returns true on
 * success; clears the local session and returns false when the refresh token
 * is missing, invalid, or revoked.
 */
export const refreshSession = async (): Promise<boolean> => {
  return (await refreshSessionResult()).ok;
};

/** Refresh once, retaining the backend failure response for API callers. */
export const refreshSessionResult = async (): Promise<RefreshResult> => {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = refreshSessionOnce().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
};

const refreshSessionOnce = async (): Promise<RefreshResult> => {
  const tokens = getAuthTokens();
  if (!tokens?.refreshToken) {
    return { ok: false };
  }
  const refreshToken = tokens.refreshToken;
  let response: Response;
  try {
    response = await fetch(authUrl("/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    return { ok: false };
  }
  if (!response.ok) {
    // Only a definitive token rejection invalidates the stored session.
    // Outages, rate limits, and ambiguous failures must preserve it.
    if (
      response.status === 401 &&
      getAuthTokens()?.refreshToken === refreshToken
    ) {
      clearAuthSession();
    }
    return { ok: false, response };
  }
  try {
    persistTokens((await response.json()) as TokenPayload);
  } catch {
    // A lost or malformed response can follow a committed token rotation.
    // Preserve the session and do not automatically repeat the operation.
    return {
      ok: false,
      response: new Response(
        JSON.stringify({
          detail: {
            message: "Invalid sign-in response. Please try again later.",
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      ),
    };
  }
  return { ok: true };
};

/**
 * Revoke the server-side session (log out everywhere) and clear local tokens.
 * Always clears the local session, even if the network call fails.
 */
export const logoutSession = async (): Promise<void> => {
  let tokens = getAuthTokens();
  const isExpired =
    typeof tokens?.expiresAt === "number" && Date.now() >= tokens.expiresAt;

  try {
    if (isExpired && tokens?.refreshToken && (await refreshSession())) {
      tokens = getAuthTokens();
    }

    if (tokens?.accessToken) {
      const response = await fetch(authUrl("/logout"), {
        method: "POST",
        headers: { Authorization: `Bearer ${tokens.accessToken}` },
      });
      if (
        response.status === 401 &&
        tokens.refreshToken &&
        (await refreshSession())
      ) {
        tokens = getAuthTokens();
        if (tokens?.accessToken) {
          await fetch(authUrl("/logout"), {
            method: "POST",
            headers: { Authorization: `Bearer ${tokens.accessToken}` },
          });
        }
      }
    }
  } catch {
    // Best effort — the local session is cleared regardless.
  } finally {
    clearAuthSession();
  }
};
