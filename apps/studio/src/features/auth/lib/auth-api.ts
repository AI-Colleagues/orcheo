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
  setAuthTokens({
    accessToken: payload.access_token,
    refreshToken: payload.refresh_token,
    expiresAt:
      typeof payload.expires_in === "number"
        ? Date.now() + payload.expires_in * 1000
        : undefined,
  });
};

const readErrorMessage = async (
  response: Response,
  fallback: string,
): Promise<string> => {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) {
        return message;
      }
    }
  } catch {
    // fall through to the fallback message
  }
  return fallback;
};

/**
 * Request a passwordless challenge for an email. The backend responds
 * identically whether or not the account exists, so a resolved promise only
 * means the request was accepted — never that an email was definitely sent.
 */
export const startEmailChallenge = async (
  email: string,
  intent: "login" | "signup" = "login",
  redirectTo?: string,
): Promise<void> => {
  const response = await fetch(authUrl("/email/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      intent,
      ...(redirectTo ? { redirect_to: redirectTo } : {}),
    }),
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

const verify = async (
  body: Record<string, string>,
): Promise<AuthUserProfile | undefined> => {
  const response = await fetch(authUrl("/email/verify"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "This sign-in link or code is invalid or has expired.",
      ),
    );
  }
  const payload = (await response.json()) as SessionPayload;
  persistTokens(payload);
  return payload.user;
};

/** Verify a magic-link token and start an authenticated session. */
export const verifyEmailToken = (
  token: string,
): Promise<AuthUserProfile | undefined> => verify({ token });

/** Verify an OTP code for an email and start an authenticated session. */
export const verifyEmailCode = (
  email: string,
  code: string,
): Promise<AuthUserProfile | undefined> => verify({ email, code });

/**
 * Rotate the stored refresh token into a fresh access token. Returns true on
 * success; clears the local session and returns false when the refresh token
 * is missing, invalid, or revoked.
 */
export const refreshSession = async (): Promise<boolean> => {
  const tokens = getAuthTokens();
  if (!tokens?.refreshToken) {
    return false;
  }
  let response: Response;
  try {
    response = await fetch(authUrl("/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: tokens.refreshToken }),
    });
  } catch {
    return false;
  }
  if (!response.ok) {
    clearAuthSession();
    return false;
  }
  try {
    persistTokens((await response.json()) as TokenPayload);
  } catch {
    clearAuthSession();
    return false;
  }
  return true;
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
