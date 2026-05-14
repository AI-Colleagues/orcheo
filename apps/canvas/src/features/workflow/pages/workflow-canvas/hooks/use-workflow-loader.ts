import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef } from "react";

import { toast } from "@/hooks/use-toast";
import {
  GALLERY_TEMPLATE_WORKFLOWS,
  getWorkflowTemplateDefinition,
  type WorkflowNode as PersistedWorkflowNode,
  type WorkflowEdge as PersistedWorkflowEdge,
} from "@features/workflow/data/workflow-data";
import {
  collectCredentialPlaceholderNames,
  describeCredentialVaultReadiness,
  describeRequiredCredentialPlaceholders,
  showCredentialReminderToast,
} from "@features/workflow/lib/credential-vault-reminder";
import { fetchWorkflowCredentialReadiness } from "@features/workflow/lib/workflow-storage-api";
import {
  getWorkflowById,
  type StoredWorkflow,
} from "@features/workflow/lib/workflow-storage";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";
import { loadWorkflowExecutions } from "@features/workflow/lib/workflow-execution-storage";
import type { WorkflowExecution } from "@features/workflow/pages/workflow-canvas/helpers/types";

interface UseWorkflowLoaderParams<TNode, TEdge> {
  workflowId: string | undefined;
  loadExecutionHistory: boolean;
  setCurrentWorkflowId: Dispatch<SetStateAction<string | null>>;
  setWorkflowName: Dispatch<SetStateAction<string>>;
  setWorkflowDescription: Dispatch<SetStateAction<string>>;
  setWorkflowTags: Dispatch<SetStateAction<string[]>>;
  setWorkflowVersions: Dispatch<SetStateAction<StoredWorkflow["versions"]>>;
  setChatkitStartScreenPrompts: Dispatch<
    SetStateAction<ChatKitStartScreenPrompt[] | null>
  >;
  setChatkitSupportedModels: Dispatch<
    SetStateAction<ChatKitSupportedModel[] | null>
  >;
  setIsWorkflowPublic: Dispatch<SetStateAction<boolean>>;
  setWorkflowShareUrl: Dispatch<SetStateAction<string | null>>;
  setIsWorkflowLoading: Dispatch<SetStateAction<boolean>>;
  setWorkflowLoadError: Dispatch<SetStateAction<string | null>>;
  setExecutions: Dispatch<SetStateAction<WorkflowExecution[]>>;
  setActiveExecutionId: Dispatch<SetStateAction<string | null>>;
  convertPersistedNodesToCanvas: (nodes: PersistedWorkflowNode[]) => TNode[];
  convertPersistedEdgesToCanvas: (edges: PersistedWorkflowEdge[]) => TEdge[];
  applySnapshot: (
    snapshot: { nodes: TNode[]; edges: TEdge[] },
    options?: { resetHistory?: boolean },
  ) => void;
}

export function useWorkflowLoader<TNode, TEdge>({
  workflowId,
  loadExecutionHistory,
  setCurrentWorkflowId,
  setWorkflowName,
  setWorkflowDescription,
  setWorkflowTags,
  setWorkflowVersions,
  setChatkitStartScreenPrompts,
  setChatkitSupportedModels,
  setIsWorkflowPublic,
  setWorkflowShareUrl,
  setIsWorkflowLoading,
  setWorkflowLoadError,
  setExecutions,
  setActiveExecutionId,
  convertPersistedNodesToCanvas,
  convertPersistedEdgesToCanvas,
  applySnapshot,
}: UseWorkflowLoaderParams<TNode, TEdge>) {
  const currentWorkflowRef = useRef<StoredWorkflow | null>(null);
  const loadedHistoryWorkflowIdRef = useRef<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    let disposeReminderToast: (() => void) | undefined;

    const resetToBlankWorkflow = () => {
      setCurrentWorkflowId(null);
      setWorkflowName("New Workflow");
      setWorkflowDescription("");
      setWorkflowTags(["draft"]);
      setWorkflowVersions([]);
      setChatkitStartScreenPrompts(null);
      setChatkitSupportedModels(null);
      setIsWorkflowPublic(false);
      setWorkflowShareUrl(null);
      setExecutions([]);
      setActiveExecutionId(null);
      applySnapshot({ nodes: [], edges: [] }, { resetHistory: true });
    };

    const loadWorkflow = async () => {
      if (!workflowId) {
        currentWorkflowRef.current = null;
        loadedHistoryWorkflowIdRef.current = null;
        setExecutions([]);
        setActiveExecutionId(null);
        setChatkitStartScreenPrompts(null);
        setChatkitSupportedModels(null);
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
          setWorkflowTags(persisted.tags ?? ["draft"]);
          setWorkflowVersions(persisted.versions ?? []);
          setChatkitStartScreenPrompts(
            persisted.chatkitStartScreenPrompts ?? null,
          );
          setChatkitSupportedModels(persisted.chatkitSupportedModels ?? null);
          setIsWorkflowPublic(persisted.isPublic ?? false);
          setWorkflowShareUrl(persisted.shareUrl ?? null);
          setExecutions([]);
          setActiveExecutionId(null);
          const canvasNodes = convertPersistedNodesToCanvas(
            persisted.nodes ?? [],
          );
          const canvasEdges = convertPersistedEdgesToCanvas(
            persisted.edges ?? [],
          );
          applySnapshot(
            { nodes: canvasNodes, edges: canvasEdges },
            { resetHistory: true },
          );
          disposeReminderToast?.();
          if (isMounted) {
            setIsWorkflowLoading(false);
          }
          void (async () => {
            const readiness = await fetchWorkflowCredentialReadiness(
              persisted.id,
            ).catch(() => undefined);
            if (!isMounted || currentWorkflowRef.current?.id !== persisted.id) {
              return;
            }
            const reminder = describeCredentialVaultReadiness(readiness);
            if (!reminder) {
              return;
            }
            disposeReminderToast?.();
            disposeReminderToast = showCredentialReminderToast({
              title: "Workflow loaded",
              description: reminder,
              highlightedCredentialNames: readiness?.missing_credentials ?? [],
            });
          })();
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
          setChatkitStartScreenPrompts(null);
          setChatkitSupportedModels(null);
          setIsWorkflowPublic(false);
          setWorkflowShareUrl(null);
          setExecutions([]);
          setActiveExecutionId(null);
          setIsWorkflowLoading(false);
        }
      }

      if (!isMounted) {
        return;
      }

      const template = GALLERY_TEMPLATE_WORKFLOWS.find(
        (w) => w.id === workflowId,
      );
      if (template) {
        const templateDefinition = getWorkflowTemplateDefinition(template.id);
        const placeholderNames = collectCredentialPlaceholderNames({
          script: templateDefinition?.script,
          runnableConfig: templateDefinition?.runnableConfig ?? null,
        });
        currentWorkflowRef.current = null;
        loadedHistoryWorkflowIdRef.current = null;
        setCurrentWorkflowId(null);
        setWorkflowName(template.name);
        setWorkflowDescription(template.description ?? "");
        setWorkflowTags(template.tags.filter((tag) => tag !== "template"));
        setWorkflowVersions([]);
        setChatkitStartScreenPrompts(null);
        setChatkitSupportedModels(null);
        setIsWorkflowPublic(false);
        setWorkflowShareUrl(null);
        setExecutions([]);
        setActiveExecutionId(null);
        const canvasNodes = convertPersistedNodesToCanvas(template.nodes);
        const canvasEdges = convertPersistedEdgesToCanvas(template.edges);
        applySnapshot(
          { nodes: canvasNodes, edges: canvasEdges },
          { resetHistory: true },
        );
        disposeReminderToast?.();
        disposeReminderToast = showCredentialReminderToast({
          title: "Template loaded",
          description: `Save to add this workflow to your workspace. ${describeRequiredCredentialPlaceholders(
            placeholderNames,
          )}`,
          highlightedCredentialNames: placeholderNames,
        });
        setIsWorkflowLoading(false);
        return;
      }

      currentWorkflowRef.current = null;
      loadedHistoryWorkflowIdRef.current = null;
      toast({
        title: "Workflow not found",
        description: "Starting a new workflow instead.",
        variant: "destructive",
      });
      setWorkflowLoadError("Workflow not found");
      resetToBlankWorkflow();
      setIsWorkflowLoading(false);
    };

    void loadWorkflow();

    return () => {
      isMounted = false;
      disposeReminderToast?.();
    };
  }, [
    applySnapshot,
    convertPersistedEdgesToCanvas,
    convertPersistedNodesToCanvas,
    setCurrentWorkflowId,
    setExecutions,
    setActiveExecutionId,
    setWorkflowDescription,
    setChatkitStartScreenPrompts,
    setChatkitSupportedModels,
    setIsWorkflowPublic,
    setWorkflowName,
    setIsWorkflowLoading,
    setWorkflowLoadError,
    setWorkflowShareUrl,
    setWorkflowTags,
    setWorkflowVersions,
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
