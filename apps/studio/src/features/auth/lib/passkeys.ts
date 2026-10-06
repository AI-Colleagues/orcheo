import {
  sendSignal,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
} from "@simplewebauthn/browser";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import { toAuthApiError } from "@features/auth/lib/auth-api";
import { platformPasskeyAvailable } from "@features/auth/lib/passkey-support";

export interface PasskeySummary {
  id: string;
  name: string;
  credential_id: string;
  transports: string[];
  backup_eligible: boolean;
  backed_up: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface PasskeyList {
  /** Null when the server cannot run passkey ceremonies. */
  rp_id: string | null;
  user_handle: string;
  passkeys: PasskeySummary[];
}

const OFFER_DISMISSED_KEY = "orcheo_studio_passkey_offer_dismissed";

const request = async (
  path: string,
  init: RequestInit,
  fallback: string,
): Promise<Response> => {
  const response = await authFetch(
    buildBackendHttpUrl(`/api/auth${path}`),
    init,
    { includeWorkspaceHeaders: false },
  );
  if (!response.ok) {
    throw await toAuthApiError(response, fallback);
  }
  return response;
};

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** List the signed-in user's passkeys. */
export const listPasskeys = async (): Promise<PasskeyList> => {
  const response = await request(
    "/passkeys",
    {},
    "Unable to load your passkeys.",
  );
  return (await response.json()) as PasskeyList;
};

/**
 * Create a passkey on this device and add it to the signed-in account. Adding
 * one needs a recent sign-in: otherwise this rejects with an `AuthApiError`
 * coded `auth.reauthentication_required`, and the caller should have the
 * person confirm it's them (sign in again) and retry.
 */
export const registerPasskey = async (
  name?: string,
): Promise<PasskeySummary> => {
  const begun = (await (
    await request(
      "/passkey/register/options",
      { method: "POST" },
      "Unable to start adding a passkey.",
    )
  ).json()) as {
    challenge_id: string;
    options: PublicKeyCredentialCreationOptionsJSON;
  };
  const credential = await startRegistration({ optionsJSON: begun.options });
  const response = await request(
    "/passkey/register/verify",
    jsonBody("POST", { challenge_id: begun.challenge_id, credential, name }),
    "Your passkey could not be added.",
  );
  return (await response.json()) as PasskeySummary;
};

/** Rename one of the signed-in user's passkeys. */
export const renamePasskey = async (
  id: string,
  name: string,
): Promise<PasskeySummary> => {
  const response = await request(
    `/passkeys/${encodeURIComponent(id)}`,
    jsonBody("PATCH", { name }),
    "Unable to rename the passkey.",
  );
  return (await response.json()) as PasskeySummary;
};

/** Remove one of the signed-in user's passkeys. */
export const deletePasskey = async (id: string): Promise<void> => {
  await request(
    `/passkeys/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    "Unable to remove the passkey.",
  );
};

/**
 * Tell the browser's password manager which passkeys the account still
 * accepts, so it can stop offering removed ones. Best effort.
 */
export const syncAcceptedPasskeys = (list: PasskeyList): void => {
  if (!list.rp_id) {
    return;
  }
  void sendSignal({
    signalName: "allAcceptedCredentials",
    rpID: list.rp_id,
    userID: list.user_handle,
    allAcceptedCredentialIDs: list.passkeys.map(
      (passkey) => passkey.credential_id,
    ),
  }).catch(() => undefined);
};

const offerDismissed = (): boolean => {
  try {
    return window.localStorage.getItem(OFFER_DISMISSED_KEY) === "true";
  } catch {
    return false;
  }
};

/** Stop offering to create a passkey after sign-in on this browser. */
export const dismissPasskeyOffer = (): void => {
  try {
    window.localStorage.setItem(OFFER_DISMISSED_KEY, "true");
  } catch {
    // Without storage the offer simply shows again next time.
  }
};

/**
 * Whether to offer creating a passkey right after an emailed-code sign-in:
 * the device can make one, the server supports them, the person has none yet,
 * and they haven't declined the offer on this browser.
 */
export const shouldOfferPasskey = async (): Promise<boolean> => {
  if (offerDismissed() || !(await platformPasskeyAvailable())) {
    return false;
  }
  try {
    const list = await listPasskeys();
    return list.rp_id !== null && list.passkeys.length === 0;
  } catch {
    return false;
  }
};
