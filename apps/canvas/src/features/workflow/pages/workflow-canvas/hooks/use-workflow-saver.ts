import { useCallback, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { toast } from "@/hooks/use-toast";
import {
  saveWorkflowMetadata,
  getVersionSnapshot,
  type StoredWorkflow,
} from "@features/workflow/lib/workflow-storage";
import { persistRunnableConfig } from "@features/workflow/lib/workflow-storage-versioning";
import type { WorkflowRunnableConfig } from "@features/workflow/lib/workflow-storage.types";

interface WorkflowSaverOptions {
  setWorkflowName: Dispatch<SetStateAction<string>>;
  setWorkflowDescription: Dispatch<SetStateAction<string>>;
  setWorkflowVersions: Dispatch<SetStateAction<StoredWorkflow["versions"]>>;
  setWorkflowTags: Dispatch<SetStateAction<string[]>>;
  workflowName: string;
  workflowDescription: string;
  workflowTags: string[];
  currentWorkflowId: string | null;
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
    setWorkflowName,
    setWorkflowDescription,
    setWorkflowVersions,
    setWorkflowTags,
    workflowName,
    workflowDescription,
    workflowTags,
    currentWorkflowId,
  } = options;
  const [isSavingWorkflowDetails, setIsSavingWorkflowDetails] = useState(false);

  const handleSaveWorkflowConfig = useCallback(
    async (runnableConfig: WorkflowRunnableConfig | null) => {
      if (!currentWorkflowId) {
        toast({
          title: "Save required",
          description: "Save this workflow before updating its config.",
          variant: "destructive",
        });
        return;
      }

      try {
        await persistRunnableConfig(
          currentWorkflowId,
          "canvas",
          runnableConfig,
        );
        toast({
          title: "Workflow config saved",
          description: `Saved config for "${workflowName}".`,
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
    [currentWorkflowId, workflowName],
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

        setWorkflowName(snapshot.name);
        setWorkflowDescription(snapshot.description ?? "");
        toast({
          title: "Version loaded",
          description:
            "Metadata restored from the selected version. Save to persist.",
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
    [currentWorkflowId, setWorkflowDescription, setWorkflowName],
  );

  return {
    handleSaveWorkflowDetails,
    handleSaveWorkflowConfig,
    handleTagsChange,
    handleRestoreVersion,
    isSavingWorkflowDetails,
  };
}
