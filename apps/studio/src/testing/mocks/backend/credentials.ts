import type { CredentialVaultEntryResponse } from "@features/workflow/types/credential-vault";

import {
  emptyResponse,
  jsonResponse,
  parseRequestBody,
} from "@/testing/mocks/backend/request-utils";

const credentialStore = new Map<string, CredentialVaultEntryResponse>();
const credentialSecretStore = new Map<string, string>();

let credentialCounter = 0;

export const handleCredentialRequest = async (
  request: Request,
  url: URL,
): Promise<Response> => {
  const segments = url.pathname.split("/");
  const targetId = segments.at(-1) ?? null;
  const isSecretEndpoint = segments.at(-1) === "secret";
  const credentialIdForSecret = isSecretEndpoint ? segments.at(-2) : null;

  if (request.method === "GET" && isSecretEndpoint && credentialIdForSecret) {
    const secret = credentialSecretStore.get(credentialIdForSecret);
    if (!secret) {
      return jsonResponse({ detail: "Credential not found" }, { status: 404 });
    }
    return jsonResponse({
      id: credentialIdForSecret,
      secret,
    });
  }

  if (request.method === "GET") {
    return jsonResponse(Array.from(credentialStore.values()));
  }

  if (request.method === "POST") {
    const payload = await parseRequestBody<{
      name?: string;
      provider?: string;
      secret?: string;
      actor?: string;
      access?: CredentialVaultEntryResponse["access"];
    }>(request);

    const now = new Date().toISOString();
    const id = `mock-credential-${++credentialCounter}`;
    const entry: CredentialVaultEntryResponse = {
      id,
      name: payload?.name ?? `Credential ${credentialCounter}`,
      provider: payload?.provider ?? "custom",
      kind: "secret",
      workflow_id: null,
      created_at: now,
      updated_at: now,
      last_rotated_at: null,
      owner: payload?.actor ?? null,
      access: payload?.access ?? "scoped",
      status: "healthy",
      secret_preview: payload?.secret ? "••••••" : null,
    };

    credentialStore.set(id, entry);
    if (payload?.secret) {
      credentialSecretStore.set(id, payload.secret);
    }
    return jsonResponse(entry, { status: 201 });
  }

  if (request.method === "PATCH" && targetId) {
    const existing = credentialStore.get(targetId);
    if (!existing) {
      return jsonResponse({ detail: "Credential not found" }, { status: 404 });
    }
    const payload = await parseRequestBody<{
      name?: string;
      provider?: string;
      secret?: string;
      access?: CredentialVaultEntryResponse["access"];
    }>(request);
    const updated: CredentialVaultEntryResponse = {
      ...existing,
      name: payload?.name ?? existing.name,
      provider: payload?.provider ?? existing.provider,
      access: payload?.access ?? existing.access,
      updated_at: new Date().toISOString(),
    };
    credentialStore.set(targetId, updated);
    if (payload?.secret) {
      credentialSecretStore.set(targetId, payload.secret);
    }
    return jsonResponse(updated);
  }

  if (request.method === "DELETE") {
    if (targetId) {
      credentialStore.delete(targetId);
      credentialSecretStore.delete(targetId);
    }
    return emptyResponse({ status: 204 });
  }

  return emptyResponse({ status: 405 });
};
