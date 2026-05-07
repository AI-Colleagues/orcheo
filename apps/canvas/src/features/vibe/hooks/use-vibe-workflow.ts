import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ExternalAgentProviderStatus } from "@/lib/api";
import { getWorkflowTemplateDefinition } from "@features/workflow/data/workflow-data";
import { WORKFLOW_STORAGE_EVENT, listWorkflows } from "@features/workflow/lib/workflow-storage";
import {
  fetchWorkflowVersions,
  request,
} from "@features/workflow/lib/workflow-storage-api";
import { type ChatKitSupportedModel } from "@features/workflow/lib/workflow-storage.types";
import {
  VIBE_AGENT_TAG,
  VIBE_WORKFLOW_HANDLE,
  VIBE_WORKFLOW_NAME,
  VIBE_WORKFLOW_TEMPLATE_ID,
} from "@features/vibe/constants";
import { buildVibeSupportedModels } from "@features/vibe/lib/vibe-models";

const VIBE_TEMPLATE = getWorkflowTemplateDefinition(VIBE_WORKFLOW_TEMPLATE_ID);
const TEMPLATE_SYNC_ACTOR = "canvas-app";
const TEMPLATE_SUMMARY = { added: 0, removed: 0, modified: 0 };
const WORKFLOW_ID_STORAGE_KEY = "orcheo:vibe-workflow-id";

interface VibeWorkflowState {
  workflowId: string | null;
  isProvisioning: boolean;
  error: string | null;
}

const readCachedWorkflowId = (): string | null => {
  try {
    const cached = localStorage.getItem(WORKFLOW_ID_STORAGE_KEY);
    return cached && cached.trim() ? cached : null;
  } catch {
    return null;
  }
};

const writeCachedWorkflowId = (id: string): void => {
  try {
    localStorage.setItem(WORKFLOW_ID_STORAGE_KEY, id);
  } catch {
    // Silently ignore storage errors.
  }
};

const clearCachedWorkflowId = (): void => {
  cachedWorkflowId = null;
  try {
    localStorage.removeItem(WORKFLOW_ID_STORAGE_KEY);
  } catch {
    // Silently ignore storage errors.
  }
};

let cachedWorkflowId: string | null = readCachedWorkflowId();

export const syncCachedWorkflowIdFromStorage = (): string | null => {
  cachedWorkflowId = readCachedWorkflowId();
  return cachedWorkflowId;
};

export const __setCachedWorkflowIdForTesting = (
  id: string | null,
): string | null => {
  cachedWorkflowId = id;
  return cachedWorkflowId;
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;

const resolveTemplateVersion = (metadata: unknown): string | null => {
  const template = asRecord(asRecord(metadata)?.template);
  const version = template?.templateVersion;
  return typeof version === "string" && version.trim() ? version : null;
};

const resolveTemplateId = (metadata: unknown): string | null => {
  const templateId = asRecord(metadata)?.template_id;
  return typeof templateId === "string" && templateId.trim()
    ? templateId
    : null;
};

export function useVibeWorkflow(
  readyProviders: ExternalAgentProviderStatus[],
): VibeWorkflowState {
  const supportedModels = useMemo(
    () => buildVibeSupportedModels(readyProviders),
    [readyProviders],
  );
  const supportedModelsSignature = useMemo(
    () => JSON.stringify(supportedModels ?? []),
    [supportedModels],
  );
  const [state, setState] = useState<VibeWorkflowState>({
    workflowId: cachedWorkflowId,
    isProvisioning: false,
    error: null,
  });
  const provisioningRef = useRef(false);
  const provisionedSignatureRef = useRef<string | null>(null);
  const syncedModelsWorkflowIdRef = useRef<string | null>(null);
  const syncedModelsSignatureRef = useRef<string | null>(null);
  const syncedTemplateRef = useRef<string | null>(null);

  const setWorkflowState = useCallback((nextState: VibeWorkflowState) => {
    setState((currentState) => {
      if (
        currentState.workflowId === nextState.workflowId &&
        currentState.isProvisioning === nextState.isProvisioning &&
        currentState.error === nextState.error
      ) {
        return currentState;
      }
      return nextState;
    });
  }, []);

  const syncSupportedModels = useCallback(
    async (workflowId: string, models: ChatKitSupportedModel[]) => {
      if (
        syncedModelsWorkflowIdRef.current === workflowId &&
        syncedModelsSignatureRef.current === supportedModelsSignature
      ) {
        return;
      }

      await request<ApiWorkflow>(`/api/workflows/${workflowId}`, {
        method: "PUT",
        body: JSON.stringify({
          actor: "canvas-app",
          chatkit: {
            supported_models: models,
          },
        }),
      });

      syncedModelsWorkflowIdRef.current = workflowId;
      syncedModelsSignatureRef.current = supportedModelsSignature;
    },
    [supportedModelsSignature],
  );

  const syncManagedTemplate = useCallback(async (workflowId: string) => {
    if (!VIBE_TEMPLATE?.metadata) {
      return;
    }

    const syncKey = `${workflowId}:${VIBE_TEMPLATE.metadata.templateVersion}`;
    if (syncedTemplateRef.current === syncKey) {
      return;
    }

    const versions = await fetchWorkflowVersions(workflowId);
    const latestMetadata = versions.at(-1)?.metadata;
    const currentTemplateId = resolveTemplateId(latestMetadata);
    const currentTemplateVersion = resolveTemplateVersion(latestMetadata);

    if (
      currentTemplateId === VIBE_WORKFLOW_TEMPLATE_ID &&
      currentTemplateVersion === VIBE_TEMPLATE.metadata.templateVersion
    ) {
      syncedTemplateRef.current = syncKey;
      return;
    }

    await request(`/api/workflows/${workflowId}/versions/ingest`, {
      method: "POST",
      body: JSON.stringify({
        script: VIBE_TEMPLATE.script,
        entrypoint: VIBE_TEMPLATE.entrypoint ?? null,
        runnable_config: VIBE_TEMPLATE.runnableConfig ?? null,
        metadata: {
          source: "canvas-template",
          template_id: VIBE_TEMPLATE.workflow.id,
          template: VIBE_TEMPLATE.metadata,
          canvas: {
            snapshot: {
              name: VIBE_TEMPLATE.workflow.name,
              description: VIBE_TEMPLATE.workflow.description,
              nodes: VIBE_TEMPLATE.workflow.nodes,
              edges: VIBE_TEMPLATE.workflow.edges,
            },
            summary: TEMPLATE_SUMMARY,
          },
        },
        notes: VIBE_TEMPLATE.notes,
        created_by: TEMPLATE_SYNC_ACTOR,
      }),
    });

    syncedTemplateRef.current = syncKey;
  }, []);

  const provision = useCallback(
    async (models: ChatKitSupportedModel[]) => {
      if (provisioningRef.current) return;

      provisioningRef.current = true;
      setState((prev) => {
        if (prev.isProvisioning && prev.error === null) {
          return prev;
        }
        return { ...prev, isProvisioning: true, error: null };
      });

      try {
        const workflows = await listWorkflows({ forceRefresh: true });
        const cachedWorkflow = cachedWorkflowId
          ? workflows.find((workflow) => workflow.id === cachedWorkflowId)
          : undefined;

        if (cachedWorkflowId && !cachedWorkflow) {
          clearCachedWorkflowId();
        }

        if (cachedWorkflow) {
          await Promise.all([
            syncManagedTemplate(cachedWorkflow.id),
            syncSupportedModels(cachedWorkflow.id, models),
          ]);
          setWorkflowState({
            workflowId: cachedWorkflow.id,
            isProvisioning: false,
            error: null,
          });
          return;
        }

        const existing = workflows.find(
          (workflow) =>
            workflow.handle === VIBE_WORKFLOW_HANDLE ||
            (workflow.name === VIBE_WORKFLOW_NAME &&
              workflow.tags?.includes(VIBE_AGENT_TAG)),
        );

        if (existing) {
          cachedWorkflowId = existing.id;
          writeCachedWorkflowId(existing.id);
          await Promise.all([
            syncManagedTemplate(existing.id),
            syncSupportedModels(existing.id, models),
          ]);
          setWorkflowState({
            workflowId: existing.id,
            isProvisioning: false,
            error: null,
          });
          return;
        }

        setWorkflowState({
          workflowId: null,
          isProvisioning: false,
          error: "Managed Orcheo Vibe workflow is unavailable.",
        });
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to provision workflow";
        setWorkflowState({
          workflowId: null,
          isProvisioning: false,
          error: message,
        });
      } finally {
        provisioningRef.current = false;
      }
    },
    [setWorkflowState, syncManagedTemplate, syncSupportedModels],
  );

  useEffect(() => {
    if (!supportedModels || supportedModels.length === 0) {
      provisionedSignatureRef.current = null;
      syncedModelsWorkflowIdRef.current = null;
      syncedModelsSignatureRef.current = null;
      syncedTemplateRef.current = null;
      setWorkflowState({
        workflowId: null,
        isProvisioning: false,
        error: null,
      });
      return;
    }

    if (provisionedSignatureRef.current !== supportedModelsSignature) {
      provisionedSignatureRef.current = supportedModelsSignature;
      void provision(supportedModels);
    }

    const targetWindow = typeof window !== "undefined" ? window : undefined;
    if (!targetWindow) {
      return;
    }

    const handleWorkflowStorageUpdate = () => {
      void provision(supportedModels);
    };

    targetWindow.addEventListener(
      WORKFLOW_STORAGE_EVENT,
      handleWorkflowStorageUpdate,
    );

    return () => {
      targetWindow.removeEventListener(
        WORKFLOW_STORAGE_EVENT,
        handleWorkflowStorageUpdate,
      );
    };
  }, [provision, setWorkflowState, supportedModels, supportedModelsSignature]);

  return state;
}
