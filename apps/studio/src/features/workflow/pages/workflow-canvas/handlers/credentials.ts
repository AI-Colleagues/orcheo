import type React from "react";

import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import { toast } from "@/hooks/use-toast";
import type {
  Credential,
  CredentialInput,
  CredentialSecretResponse,
  CredentialUpdateInput,
  CredentialVaultEntryResponse,
} from "@features/workflow/types/credential-vault";

type AddCredentialDependencies = {
  backendBaseUrl: string | null;
  currentWorkflowId: string | null;
  userName: string;
  setCredentials: React.Dispatch<React.SetStateAction<Credential[]>>;
};

type DeleteCredentialDependencies = {
  backendBaseUrl: string | null;
  currentWorkflowId: string | null;
  setCredentials: React.Dispatch<React.SetStateAction<Credential[]>>;
};

type UpdateCredentialDependencies = {
  backendBaseUrl: string | null;
  currentWorkflowId: string | null;
  userName: string;
  setCredentials: React.Dispatch<React.SetStateAction<Credential[]>>;
};

type RevealCredentialDependencies = {
  backendBaseUrl: string | null;
  currentWorkflowId: string | null;
  setCredentials: React.Dispatch<React.SetStateAction<Credential[]>>;
};

const getCredentialSecret = (
  secrets: Record<string, string> | undefined,
): string | null => {
  if (!secrets) {
    return null;
  }
  const preferredSecret = secrets.apiKey ?? secrets.secret;
  if (preferredSecret && preferredSecret.trim().length > 0) {
    return preferredSecret.trim();
  }
  for (const value of Object.values(secrets)) {
    const candidate = value.trim();
    if (candidate.length > 0) {
      return candidate;
    }
  }
  return null;
};

export const createHandleAddCredential =
  ({
    backendBaseUrl,
    currentWorkflowId,
    userName,
    setCredentials,
  }: AddCredentialDependencies) =>
  async (credential: CredentialInput) => {
    const secret = getCredentialSecret(credential.secrets);
    if (!secret) {
      const message = "Credential secret is required to save a credential.";
      toast({
        title: "Missing credential secret",
        description: message,
        variant: "destructive",
      });
      throw new Error(message);
    }

    const response = await authFetch(
      buildBackendHttpUrl("/api/credentials", backendBaseUrl),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: credential.name,
          provider: credential.provider?.trim() || "Custom",
          secret,
          actor: userName,
          access: credential.access,
          workflow_id: currentWorkflowId,
          scopes: [],
        }),
      },
    );

    if (!response.ok) {
      let detail = `Failed to save credential (status ${response.status})`;
      try {
        const payload = (await response.json()) as { detail?: unknown };
        if (typeof payload?.detail === "string") {
          detail = payload.detail;
        } else if (
          payload?.detail &&
          typeof (payload.detail as { message?: unknown }).message === "string"
        ) {
          detail = (payload.detail as { message?: string }).message as string;
        }
      } catch (error) {
        console.warn("Failed to parse credential creation error", error);
      }

      toast({
        title: "Unable to save credential",
        description: detail,
        variant: "destructive",
      });
      throw new Error(detail);
    }

    const payload = (await response.json()) as CredentialVaultEntryResponse;

    const credentialRecord: Credential = {
      id: payload.id,
      name: payload.name,
      provider: payload.provider ?? payload.kind,
      type: payload.provider ?? payload.kind,
      createdAt: payload.created_at,
      updatedAt: payload.updated_at,
      owner: payload.owner,
      access: payload.access,
      secrets: { secret },
      status: payload.status,
      secretPreview: payload.secret_preview ?? null,
    };

    setCredentials((prev) => {
      const withoutDuplicate = prev.filter(
        (existing) => existing.id !== credentialRecord.id,
      );
      return [...withoutDuplicate, credentialRecord];
    });

    toast({
      title: "Credential added to vault",
      description: `${credentialRecord.name} is now available for nodes that require secure access.`,
    });
  };

export const createHandleDeleteCredential =
  ({
    backendBaseUrl,
    currentWorkflowId,
    setCredentials,
  }: DeleteCredentialDependencies) =>
  async (id: string) => {
    const url = new URL(
      buildBackendHttpUrl(`/api/credentials/${id}`, backendBaseUrl),
    );
    if (currentWorkflowId) {
      url.searchParams.set("workflow_id", currentWorkflowId);
    }

    try {
      const response = await authFetch(url.toString(), {
        method: "DELETE",
      });

      if (!response.ok && response.status !== 404) {
        throw new Error(
          `Failed to delete credential (status ${response.status})`,
        );
      }

      setCredentials((prev) =>
        prev.filter((credential) => credential.id !== id),
      );
      toast({
        title: "Credential removed",
        description:
          "Nodes referencing this credential will require reconfiguration before publish.",
      });
    } catch (error) {
      console.error("Failed to delete credential", error);
      toast({
        title: "Unable to delete credential",
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred while removing the credential.",
        variant: "destructive",
      });
      throw error;
    }
  };

export const createHandleUpdateCredential =
  ({
    backendBaseUrl,
    currentWorkflowId,
    userName,
    setCredentials,
  }: UpdateCredentialDependencies) =>
  async (id: string, updates: CredentialUpdateInput) => {
    const secret = getCredentialSecret(updates.secrets);
    const payload: Record<string, string> = {
      actor: userName,
    };

    if (updates.name !== undefined) {
      payload.name = updates.name;
    }
    if (updates.provider !== undefined) {
      payload.provider = updates.provider;
    } else if (updates.type !== undefined) {
      payload.provider = updates.type;
    }
    if (secret !== null) {
      payload.secret = secret;
    }
    if (updates.access !== undefined) {
      payload.access = updates.access;
    }
    if (currentWorkflowId) {
      payload.workflow_id = currentWorkflowId;
    }

    const hasChanges = Object.keys(payload).some((key) => key !== "actor");
    if (!hasChanges) {
      const message = "No credential changes were provided.";
      toast({
        title: "Nothing to update",
        description: message,
        variant: "destructive",
      });
      throw new Error(message);
    }

    const response = await authFetch(
      buildBackendHttpUrl(`/api/credentials/${id}`, backendBaseUrl),
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    );

    if (!response.ok) {
      let detail = `Failed to update credential (status ${response.status})`;
      try {
        const errorPayload = (await response.json()) as { detail?: unknown };
        if (typeof errorPayload?.detail === "string") {
          detail = errorPayload.detail;
        }
      } catch (error) {
        console.warn("Failed to parse credential update error", error);
      }

      toast({
        title: "Unable to update credential",
        description: detail,
        variant: "destructive",
      });
      throw new Error(detail);
    }

    const updated = (await response.json()) as CredentialVaultEntryResponse;
    setCredentials((previous) =>
      previous.map((credential) => {
        if (credential.id !== id) {
          return credential;
        }
        return {
          ...credential,
          name: updated.name,
          provider: updated.provider ?? updated.kind,
          type: updated.provider ?? updated.kind,
          access: updated.access,
          status: updated.status,
          updatedAt: updated.updated_at,
          secrets: secret ? { secret } : credential.secrets,
          secretPreview: updated.secret_preview ?? credential.secretPreview,
        };
      }),
    );

    toast({
      title: "Credential updated",
      description: "Your credential changes were saved successfully.",
    });
  };

export const createHandleRevealCredentialSecret =
  ({
    backendBaseUrl,
    currentWorkflowId,
    setCredentials,
  }: RevealCredentialDependencies) =>
  async (id: string) => {
    const url = new URL(
      buildBackendHttpUrl(`/api/credentials/${id}/secret`, backendBaseUrl),
    );
    if (currentWorkflowId) {
      url.searchParams.set("workflow_id", currentWorkflowId);
    }

    const response = await authFetch(url.toString());
    if (!response.ok) {
      let detail = `Failed to reveal credential secret (status ${response.status})`;
      try {
        const payload = (await response.json()) as { detail?: unknown };
        if (typeof payload?.detail === "string") {
          detail = payload.detail;
        }
      } catch (error) {
        console.warn("Failed to parse credential reveal error", error);
      }

      toast({
        title: "Unable to reveal secret",
        description: detail,
        variant: "destructive",
      });
      throw new Error(detail);
    }

    const payload = (await response.json()) as CredentialSecretResponse;
    setCredentials((previous) =>
      previous.map((credential) =>
        credential.id === id
          ? { ...credential, secrets: { secret: payload.secret } }
          : credential,
      ),
    );
    return payload.secret;
  };
