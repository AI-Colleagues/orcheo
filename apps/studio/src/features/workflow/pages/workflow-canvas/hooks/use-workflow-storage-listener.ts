import type { Dispatch, SetStateAction } from "react";
import { useEffect } from "react";

import {
  WORKFLOW_STORAGE_EVENT,
  getWorkflowById,
} from "@features/workflow/lib/workflow-storage";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";

interface UseWorkflowStorageListenerParams {
  currentWorkflowId: string | null;
  setWorkflowName: Dispatch<SetStateAction<string>>;
  setWorkflowDescription: Dispatch<SetStateAction<string>>;
  setWorkflowVersions: Dispatch<SetStateAction<StoredWorkflow["versions"]>>;
  setWorkflowTags: Dispatch<SetStateAction<string[]>>;
  setChatkitStartScreenPrompts: Dispatch<
    SetStateAction<ChatKitStartScreenPrompt[] | null>
  >;
  setChatkitSupportedModels: Dispatch<
    SetStateAction<ChatKitSupportedModel[] | null>
  >;
}

export function useWorkflowStorageListener({
  currentWorkflowId,
  setWorkflowName,
  setWorkflowDescription,
  setWorkflowVersions,
  setWorkflowTags,
  setChatkitStartScreenPrompts,
  setChatkitSupportedModels,
}: UseWorkflowStorageListenerParams) {
  useEffect(() => {
    if (!currentWorkflowId) {
      return;
    }

    const targetWindow = typeof window !== "undefined" ? window : undefined;
    if (!targetWindow) {
      return;
    }

    const handleStorageUpdate = async () => {
      try {
        const updated = await getWorkflowById(currentWorkflowId);
        if (updated) {
          setWorkflowName(updated.name);
          setWorkflowDescription(updated.description ?? "");
          setWorkflowVersions(updated.versions ?? []);
          setWorkflowTags(updated.tags ?? ["draft"]);
          setChatkitStartScreenPrompts(
            updated.chatkitStartScreenPrompts ?? null,
          );
          setChatkitSupportedModels(updated.chatkitSupportedModels ?? null);
        }
      } catch (error) {
        console.error("Failed to reload workflow", error);
      }
    };

    targetWindow.addEventListener(WORKFLOW_STORAGE_EVENT, handleStorageUpdate);
    return () => {
      targetWindow.removeEventListener(
        WORKFLOW_STORAGE_EVENT,
        handleStorageUpdate,
      );
    };
  }, [
    currentWorkflowId,
    setWorkflowDescription,
    setChatkitStartScreenPrompts,
    setChatkitSupportedModels,
    setWorkflowName,
    setWorkflowTags,
    setWorkflowVersions,
  ]);
}
