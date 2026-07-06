import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef } from "react";

import { toast } from "@/hooks/use-toast";
import {
  getWorkflowById,
  type StoredWorkflow,
} from "@features/workflow/lib/workflow-storage";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";
import { loadWorkflowExecutions } from "@features/workflow/lib/workflow-execution-storage";
import type { WorkflowExecution } from "@features/workflow/pages/workflow/helpers/types";

interface UseWorkflowLoaderParams {
  workflowId: string | undefined;
  loadExecutionHistory: boolean;
  setCurrentWorkflowId: Dispatch<SetStateAction<string | null>>;
  setWorkflowName: Dispatch<SetStateAction<string>>;
  setWorkflowDescription: Dispatch<SetStateAction<string>>;
  setWorkflowHandle: Dispatch<SetStateAction<string | null>>;
  setWorkflowTeamSlug: Dispatch<SetStateAction<string | null>>;
  setWorkflowTags: Dispatch<SetStateAction<string[]>>;
  setWorkflowVersions: Dispatch<SetStateAction<StoredWorkflow["versions"]>>;
  setWorkflowUploadError: Dispatch<
    SetStateAction<StoredWorkflow["uploadError"] | null>
  >;
  setChatkitStartScreenPrompts: Dispatch<
    SetStateAction<ChatKitStartScreenPrompt[] | null>
  >;
  setChatkitSupportedModels: Dispatch<
    SetStateAction<ChatKitSupportedModel[] | null>
  >;
  setIsWorkflowPublic: Dispatch<SetStateAction<boolean>>;
  setWorkflowRequireLogin: Dispatch<SetStateAction<boolean>>;
  setWorkflowShareUrl: Dispatch<SetStateAction<string | null>>;
  setIsWorkflowLoading: Dispatch<SetStateAction<boolean>>;
  setWorkflowLoadError: Dispatch<SetStateAction<string | null>>;
  setExecutions: Dispatch<SetStateAction<WorkflowExecution[]>>;
  setActiveExecutionId: Dispatch<SetStateAction<string | null>>;
}

export function useWorkflowLoader({
  workflowId,
  loadExecutionHistory,
  setCurrentWorkflowId,
  setWorkflowName,
  setWorkflowDescription,
  setWorkflowHandle,
  setWorkflowTeamSlug,
  setWorkflowTags,
  setWorkflowVersions,
  setWorkflowUploadError,
  setChatkitStartScreenPrompts,
  setChatkitSupportedModels,
  setIsWorkflowPublic,
  setWorkflowRequireLogin,
  setWorkflowShareUrl,
  setIsWorkflowLoading,
  setWorkflowLoadError,
  setExecutions,
  setActiveExecutionId,
}: UseWorkflowLoaderParams) {
  const currentWorkflowRef = useRef<StoredWorkflow | null>(null);
  const loadedHistoryWorkflowIdRef = useRef<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const resetToBlank = () => {
      setCurrentWorkflowId(null);
      setWorkflowName("New Workflow");
      setWorkflowDescription("");
      setWorkflowHandle(null);
      setWorkflowTeamSlug(null);
      setWorkflowTags(["draft"]);
      setWorkflowVersions([]);
      setWorkflowUploadError(null);
      setChatkitStartScreenPrompts(null);
      setChatkitSupportedModels(null);
      setIsWorkflowPublic(false);
      setWorkflowRequireLogin(false);
      setWorkflowShareUrl(null);
      setExecutions([]);
      setActiveExecutionId(null);
    };

    const loadWorkflow = async () => {
      if (!workflowId) {
        currentWorkflowRef.current = null;
        loadedHistoryWorkflowIdRef.current = null;
        setExecutions([]);
        setActiveExecutionId(null);
        setChatkitStartScreenPrompts(null);
        setChatkitSupportedModels(null);
        setWorkflowHandle(null);
        setWorkflowTeamSlug(null);
        setWorkflowUploadError(null);
        setIsWorkflowLoading(false);
        setWorkflowLoadError(null);
        return;
      }

      try {
        setIsWorkflowLoading(true);
        setWorkflowLoadError(null);
        const persisted = await getWorkflowById(workflowId);
        if (persisted && isMounted) {
          currentWorkflowRef.current = persisted;
          loadedHistoryWorkflowIdRef.current = null;
          setCurrentWorkflowId(persisted.id);
          setWorkflowName(persisted.name);
          setWorkflowDescription(persisted.description ?? "");
          setWorkflowHandle(persisted.handle ?? null);
          setWorkflowTeamSlug(persisted.teamSlug ?? null);
          setWorkflowTags(persisted.tags ?? ["draft"]);
          setWorkflowVersions(persisted.versions ?? []);
          setWorkflowUploadError(persisted.uploadError ?? null);
          setChatkitStartScreenPrompts(
            persisted.chatkitStartScreenPrompts ?? null,
          );
          setChatkitSupportedModels(persisted.chatkitSupportedModels ?? null);
          setIsWorkflowPublic(persisted.isPublic ?? false);
          setWorkflowRequireLogin(persisted.requireLogin ?? false);
          setWorkflowShareUrl(persisted.shareUrl ?? null);
          setExecutions([]);
          setActiveExecutionId(null);
          setIsWorkflowLoading(false);
          return;
        }
      } catch (error) {
        if (isMounted) {
          currentWorkflowRef.current = null;
          loadedHistoryWorkflowIdRef.current = null;
          toast({
            title: "Failed to load workflow",
            description:
              error instanceof Error ? error.message : "Unknown error occurred",
            variant: "destructive",
          });
          setWorkflowLoadError(
            error instanceof Error ? error.message : "Unknown error occurred",
          );
          setWorkflowUploadError(null);
          setChatkitStartScreenPrompts(null);
          setChatkitSupportedModels(null);
          setIsWorkflowPublic(false);
          setWorkflowRequireLogin(false);
          setWorkflowShareUrl(null);
          setExecutions([]);
          setActiveExecutionId(null);
          setIsWorkflowLoading(false);
        }
        return;
      }

      if (!isMounted) {
        return;
      }

      currentWorkflowRef.current = null;
      loadedHistoryWorkflowIdRef.current = null;
      toast({
        title: "Workflow not found",
        description: "The requested workflow could not be found.",
        variant: "destructive",
      });
      setWorkflowLoadError("Workflow not found");
      resetToBlank();
      setIsWorkflowLoading(false);
    };

    void loadWorkflow();

    return () => {
      isMounted = false;
    };
  }, [
    setCurrentWorkflowId,
    setExecutions,
    setActiveExecutionId,
    setWorkflowDescription,
    setWorkflowHandle,
    setWorkflowTeamSlug,
    setWorkflowUploadError,
    setChatkitStartScreenPrompts,
    setChatkitSupportedModels,
    setIsWorkflowPublic,
    setWorkflowName,
    setIsWorkflowLoading,
    setWorkflowLoadError,
    setWorkflowShareUrl,
    setWorkflowTags,
    setWorkflowVersions,
    setWorkflowRequireLogin,
    workflowId,
  ]);

  useEffect(() => {
    if (!loadExecutionHistory || !workflowId) {
      return;
    }

    if (loadedHistoryWorkflowIdRef.current === workflowId) {
      return;
    }

    let isMounted = true;

    const loadHistory = async () => {
      const persisted =
        currentWorkflowRef.current?.id === workflowId
          ? currentWorkflowRef.current
          : await getWorkflowById(workflowId);

      if (!persisted || !isMounted) {
        return;
      }

      try {
        const history = await loadWorkflowExecutions(persisted.id, {
          workflow: persisted,
        });
        if (!isMounted) {
          return;
        }
        setExecutions(history);
        setActiveExecutionId((current) => {
          if (
            current &&
            history.some((execution) => execution.id === current)
          ) {
            return current;
          }
          return history[0]?.id ?? null;
        });
        loadedHistoryWorkflowIdRef.current = workflowId;
      } catch (historyError) {
        if (!isMounted) {
          return;
        }
        setExecutions([]);
        setActiveExecutionId(null);
        toast({
          title: "Failed to load execution history",
          description:
            historyError instanceof Error
              ? historyError.message
              : "Unable to retrieve workflow runs.",
          variant: "destructive",
        });
        console.error("Failed to load workflow executions", historyError);
      }
    };

    void loadHistory();

    return () => {
      isMounted = false;
    };
  }, [loadExecutionHistory, setActiveExecutionId, setExecutions, workflowId]);
}
