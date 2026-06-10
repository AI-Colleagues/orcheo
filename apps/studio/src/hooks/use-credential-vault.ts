import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "@/hooks/use-toast";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl, getBackendBaseUrl } from "@/lib/config";
import type {
  Credential,
  CredentialInput,
  CredentialSecretResponse,
  CredentialUpdateInput,
  CredentialVaultEntryResponse,
} from "@features/workflow/types/credential-vault";

interface UseCredentialVaultOptions {
  actorName?: string;
  workflowId?: string | null;
}

interface UseCredentialVaultResult {
  credentials: Credential[];
  isLoading: boolean;
  onAddCredential: (credential: CredentialInput) => Promise<void>;
  onUpdateCredential: (
    id: string,
    updates: CredentialUpdateInput,
  ) => Promise<void>;
  onDeleteCredential: (id: string) => Promise<void>;
  onRevealCredentialSecret: (id: string) => Promise<string | null>;
}

const DEFAULT_ACTOR = "system";

const mapEntryToCredential = (
  entry: CredentialVaultEntryResponse,
): Credential => ({
  id: entry.id,
  name: entry.name,
  provider: entry.provider ?? entry.kind,
  type: entry.provider ?? entry.kind,
  workflowId: entry.workflow_id ?? null,
  createdAt: entry.created_at,
  updatedAt: entry.updated_at,
  owner: entry.owner,
  access: entry.access,
  secrets: undefined,
  status: entry.status,
  secretPreview: entry.secret_preview ?? null,
});

const resolveCredentialWorkflowId = (
  credentials: Credential[],
  credentialId: string,
  fallbackWorkflowId: string | null,
): string | null =>
  fallbackWorkflowId ??
  credentials.find((credential) => credential.id === credentialId)
    ?.workflowId ??
  null;

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

export function useCredentialVault(
  options: UseCredentialVaultOptions = {},
): UseCredentialVaultResult {
  const { workflowId = null } = options;
  const backendBaseUrl = useMemo(() => getBackendBaseUrl(), []);
  const actorName = options.actorName?.trim() || DEFAULT_ACTOR;

  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    let isCancelled = false;

    const fetchCredentials = async () => {
      setIsLoading(true);
      try {
        const url = new URL(
          buildBackendHttpUrl("/api/credentials", backendBaseUrl),
        );
        if (workflowId) {
          url.searchParams.set("workflow_id", workflowId);
        }

        const response = await authFetch(url.toString(), {
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(
            `Failed to load credentials (status ${response.status})`,
          );
        }

        const payload =
          (await response.json()) as CredentialVaultEntryResponse[];

        if (isCancelled) {
          return;
        }

        setCredentials(payload.map(mapEntryToCredential));
      } catch (error) {
        if (controller.signal.aborted || isCancelled) {
          return;
        }

        console.error("Failed to load credential vault", error);
        toast({
          title: "Unable to load credentials",
          description:
            error instanceof Error
              ? error.message
              : "An unexpected error occurred while loading credentials.",
          variant: "destructive",
        });
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    };

    void fetchCredentials();

    return () => {
      isCancelled = true;
      controller.abort();
    };
  }, [backendBaseUrl, workflowId]);

  const onAddCredential = useCallback(
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
            provider: credential.provider?.trim() || "custom",
            secret,
            actor: actorName,
            access: credential.access,
            workflow_id: workflowId,
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
            typeof (payload.detail as { message?: unknown }).message ===
              "string"
          ) {
            detail = (payload.detail as { message?: string }).message as string;
          }
        } catch (parseError) {
          console.warn("Failed to parse credential creation error", parseError);
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
        workflowId: payload.workflow_id ?? null,
        createdAt: payload.created_at,
        updatedAt: payload.updated_at,
        owner: payload.owner,
        access: payload.access,
        secrets: { secret },
        status: payload.status,
        secretPreview: payload.secret_preview ?? null,
      };

      setCredentials((previous) => {
        const withoutDuplicate = previous.filter(
          (existing) => existing.id !== credentialRecord.id,
        );
        return [...withoutDuplicate, credentialRecord];
      });

      toast({
        title: "Credential added to vault",
        description: `${credentialRecord.name} is now available for nodes that require secure access.`,
      });
    },
    [actorName, backendBaseUrl, workflowId],
  );

  const onUpdateCredential = useCallback(
    async (id: string, updates: CredentialUpdateInput) => {
      const secret = getCredentialSecret(updates.secrets);
      const credentialWorkflowId = resolveCredentialWorkflowId(
        credentials,
        id,
        workflowId,
      );
      const payload: Record<string, string> = {
        actor: actorName,
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
      if (credentialWorkflowId) {
        payload.workflow_id = credentialWorkflowId;
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
        } catch (parseError) {
          console.warn("Failed to parse credential update error", parseError);
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
            workflowId: updated.workflow_id ?? credential.workflowId ?? null,
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
    },
    [actorName, backendBaseUrl, credentials, workflowId],
  );

  const onDeleteCredential = useCallback(
    async (id: string) => {
      const credentialWorkflowId = resolveCredentialWorkflowId(
        credentials,
        id,
        workflowId,
      );
      const url = new URL(
        buildBackendHttpUrl(`/api/credentials/${id}`, backendBaseUrl),
      );
      if (credentialWorkflowId) {
        url.searchParams.set("workflow_id", credentialWorkflowId);
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

        setCredentials((previous) =>
          previous.filter((credential) => credential.id !== id),
        );
        toast({
          title: "Credential removed",
          description:
            "Nodes referencing this credential will require reconfiguration before publish.",
        });
      } catch (error) {
        console.error("Failed to delete credential", error);
        const message =
          error instanceof Error ? error.message : "Credential removal failed.";
        toast({
          title: "Unable to delete credential",
          description: message,
          variant: "destructive",
        });
        return;
      }
    },
    [backendBaseUrl, credentials, workflowId],
  );

  const onRevealCredentialSecret = useCallback(
    async (id: string) => {
      const credentialWorkflowId = resolveCredentialWorkflowId(
        credentials,
        id,
        workflowId,
      );
      const url = new URL(
        buildBackendHttpUrl(`/api/credentials/${id}/secret`, backendBaseUrl),
      );
      if (credentialWorkflowId) {
        url.searchParams.set("workflow_id", credentialWorkflowId);
      }

      const response = await authFetch(url.toString());
      if (!response.ok) {
        let detail = `Failed to reveal credential secret (status ${response.status})`;
        try {
          const payload = (await response.json()) as { detail?: unknown };
          if (typeof payload?.detail === "string") {
            detail = payload.detail;
          }
        } catch (parseError) {
          console.warn("Failed to parse credential reveal error", parseError);
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
    },
    [backendBaseUrl, credentials, workflowId],
  );

  return {
    credentials,
    isLoading,
    onAddCredential,
    onUpdateCredential,
    onDeleteCredential,
    onRevealCredentialSecret,
  };
}

export default useCredentialVault;
