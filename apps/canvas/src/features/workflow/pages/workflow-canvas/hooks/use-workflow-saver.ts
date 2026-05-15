import { useCallback, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { toast } from "@/hooks/use-toast";
import {
  toPersistedEdge,
  toPersistedNode,
} from "@features/workflow/pages/workflow-canvas/helpers/transformers";
import type {
  CanvasEdge,
  CanvasNode,
} from "@features/workflow/pages/workflow-canvas/helpers/types";
import type {
  WorkflowEdge as PersistedWorkflowEdge,
  WorkflowNode as PersistedWorkflowNode,
} from "@features/workflow/data/workflow-data";
import {
  getVersionSnapshot,
  saveWorkflowMetadata,
  saveWorkflow as persistWorkflow,
  type StoredWorkflow,
} from "@features/workflow/lib/workflow-storage";
import { getWorkflowRouteRef } from "@features/workflow/lib/workflow-storage-helpers";
import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import { getWorkspaceWorkflowPath } from "@/lib/workspace-routing";

interface WorkflowSaverOptions {
  createSnapshot: () => { nodes: CanvasNode[]; edges: CanvasEdge[] };
  convertPersistedNodesToCanvas: (
    nodes: PersistedWorkflowNode[],
  ) => CanvasNode[];
  convertPersistedEdgesToCanvas: (
    edges: PersistedWorkflowEdge[],
  ) => CanvasEdge[];
  setWorkflowName: Dispatch<SetStateAction<string>>;
  setWorkflowDescription: Dispatch<SetStateAction<string>>;
  setCurrentWorkflowId: Dispatch<SetStateAction<string | null>>;
  setWorkflowVersions: Dispatch<SetStateAction<StoredWorkflow["versions"]>>;
  setWorkflowTags: Dispatch<SetStateAction<string[]>>;
  workflowName: string;
  workflowDescription: string;
  workflowTags: string[];
  currentWorkflowId: string | null;
  workflowIdFromRoute?: string;
  navigate: (path: string, options?: { replace?: boolean }) => void;
  applySnapshot: (
    snapshot: { nodes: CanvasNode[]; edges: CanvasEdge[] },
    options?: { resetHistory?: boolean },
  ) => void;
}

interface WorkflowSaverHandlers {
  handleSaveWorkflowDetails: () => Promise<void>;
  handleSaveWorkflowConfig: (
    runnableConfig: WorkflowRunnableConfig | null,
  ) => Promise<void>;
  handleTagsChange: (value: string) => void;
  handleRestoreVersion: (versionId: string) => Promise<void>;
  isSavingWorkflowDetails: boolean;
}

export function useWorkflowSaver(
  options: WorkflowSaverOptions,
): WorkflowSaverHandlers {
  const {
    createSnapshot,
    convertPersistedNodesToCanvas,
    convertPersistedEdgesToCanvas,
    setWorkflowName,
    setWorkflowDescription,
    setCurrentWorkflowId,
    setWorkflowVersions,
    setWorkflowTags,
    workflowName,
    workflowDescription,
    workflowTags,
    currentWorkflowId,
    workflowIdFromRoute,
    navigate,
    applySnapshot,
  } = options;
  const [isSavingWorkflowDetails, setIsSavingWorkflowDetails] = useState(false);

  const persistCurrentWorkflow = useCallback(
    async ({
      versionMessage,
      runnableConfig,
      successTitle,
      successDescription,
    }: {
      versionMessage: string;
      runnableConfig?: WorkflowRunnableConfig | null;
      successTitle: string;
      successDescription: (saved: StoredWorkflow) => string;
    }) => {
      const snapshot = createSnapshot();
      const persistedNodes = snapshot.nodes.map(toPersistedNode);
      const persistedEdges = snapshot.edges.map(toPersistedEdge);
      const tagsToPersist = workflowTags.length > 0 ? workflowTags : ["draft"];

      const saved = await persistWorkflow(
        {
          id: currentWorkflowId ?? undefined,
          name: workflowName.trim() || "Untitled Workflow",
          description: workflowDescription.trim(),
          tags: tagsToPersist,
          nodes: persistedNodes,
          edges: persistedEdges,
        },
        {
          versionMessage,
          runnableConfig,
        },
      );

      setCurrentWorkflowId(saved.id);
      setWorkflowName(saved.name);
      setWorkflowDescription(saved.description ?? "");
      setWorkflowTags(saved.tags ?? tagsToPersist);
      setWorkflowVersions(saved.versions ?? []);

      toast({
        title: successTitle,
        description: successDescription(saved),
      });

      const nextWorkflowRouteRef = getWorkflowRouteRef(saved);
      const workspaceSlug = getSelectedWorkspaceSlug();
      if (
        !workflowIdFromRoute ||
        (workflowIdFromRoute !== saved.id &&
          workflowIdFromRoute !== nextWorkflowRouteRef)
      ) {
        navigate(
          getWorkspaceWorkflowPath(workspaceSlug, nextWorkflowRouteRef),
          {
            replace: !!workflowIdFromRoute,
          },
        );
      }

      return saved;
    },
    [
      createSnapshot,
      currentWorkflowId,
      navigate,
      setCurrentWorkflowId,
      setWorkflowDescription,
      setWorkflowName,
      setWorkflowTags,
      setWorkflowVersions,
      workflowDescription,
      workflowIdFromRoute,
      workflowName,
      workflowTags,
    ],
  );

  const handleSaveWorkflowConfig = useCallback(
    async (runnableConfig: WorkflowRunnableConfig | null) => {
      const timestampLabel = new Date().toLocaleString();

      try {
        await persistCurrentWorkflow({
          versionMessage: `Workflow config updated (${timestampLabel})`,
          runnableConfig,
          successTitle: "Workflow config saved",
          successDescription: (saved) =>
            `Saved config for "${saved.name}" without creating a new version.`,
        });
      } catch (error) {
        toast({
          title: "Failed to save workflow config",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      }
    },
    [persistCurrentWorkflow],
  );

  const handleSaveWorkflowDetails = useCallback(async () => {
    if (!currentWorkflowId) {
      toast({
        title: "Save required",
        description: "Save this workflow before updating its details.",
        variant: "destructive",
      });
      return;
    }

    setIsSavingWorkflowDetails(true);
    try {
      const tagsToPersist = workflowTags.length > 0 ? workflowTags : ["draft"];
      const saved = await saveWorkflowMetadata({
        id: currentWorkflowId,
        name: workflowName.trim() || "Untitled Workflow",
        description: workflowDescription.trim(),
        tags: tagsToPersist,
      });

      setWorkflowName(saved.name);
      setWorkflowDescription(saved.description ?? "");
      setWorkflowTags(saved.tags ?? tagsToPersist);
      setWorkflowVersions(saved.versions ?? []);

      toast({
        title: "Workflow details saved",
        description: `Saved details for "${saved.name}".`,
      });
    } catch (error) {
      toast({
        title: "Failed to save workflow details",
        description:
          error instanceof Error ? error.message : "Unknown error occurred",
        variant: "destructive",
      });
    } finally {
      setIsSavingWorkflowDetails(false);
    }
  }, [
    currentWorkflowId,
    setWorkflowDescription,
    setWorkflowName,
    setWorkflowTags,
    setWorkflowVersions,
    workflowDescription,
    workflowName,
    workflowTags,
  ]);

  const handleTagsChange = useCallback(
    (value: string) => {
      const tags = value
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);
      setWorkflowTags(tags);
    },
    [setWorkflowTags],
  );

  const handleRestoreVersion = useCallback(
    async (versionId: string) => {
      if (!currentWorkflowId) {
        toast({
          title: "Save required",
          description: "Save this workflow before restoring versions.",
          variant: "destructive",
        });
        return;
      }

      try {
        const snapshot = await getVersionSnapshot(currentWorkflowId, versionId);
        if (!snapshot) {
          toast({
            title: "Version unavailable",
            description: "We couldn't load that version. Please try again.",
            variant: "destructive",
          });
          return;
        }

        const canvasNodes = convertPersistedNodesToCanvas(snapshot.nodes ?? []);
        const canvasEdges = convertPersistedEdgesToCanvas(snapshot.edges ?? []);
        applySnapshot(
          { nodes: canvasNodes, edges: canvasEdges },
          { resetHistory: true },
        );
        setWorkflowName(snapshot.name);
        setWorkflowDescription(snapshot.description ?? "");
        toast({
          title: "Version loaded",
          description: "Review the restored version and save to keep it.",
        });
      } catch (error) {
        toast({
          title: "Failed to restore version",
          description:
            error instanceof Error ? error.message : "Unknown error occurred",
          variant: "destructive",
        });
      }
    },
    [
      applySnapshot,
      convertPersistedEdgesToCanvas,
      convertPersistedNodesToCanvas,
      currentWorkflowId,
      setWorkflowDescription,
      setWorkflowName,
    ],
  );

  return {
    handleSaveWorkflowDetails,
    handleSaveWorkflowConfig,
    handleTagsChange,
    handleRestoreVersion,
    isSavingWorkflowDetails,
  };
}
