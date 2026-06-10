import { useWorkflowLoader } from "@features/workflow/pages/workflow/hooks/use-workflow-loader";
import { useWorkflowStorageListener } from "@features/workflow/pages/workflow/hooks/use-workflow-storage-listener";

import type { WorkflowCore } from "./use-workflow-core";

export function useWorkflowLifecycle(
  core: WorkflowCore,
  workflowId: string | undefined,
) {
  useWorkflowLoader({
    workflowId,
    loadExecutionHistory: core.ui.activeTab === "trace",
    setCurrentWorkflowId: core.metadata.setCurrentWorkflowId,
    setWorkflowName: core.metadata.setWorkflowName,
    setWorkflowDescription: core.metadata.setWorkflowDescription,
    setWorkflowTags: core.metadata.setWorkflowTags,
    setWorkflowVersions: core.metadata.setWorkflowVersions,
    setChatkitStartScreenPrompts: core.metadata.setChatkitStartScreenPrompts,
    setChatkitSupportedModels: core.metadata.setChatkitSupportedModels,
    setIsWorkflowPublic: core.metadata.setIsWorkflowPublic,
    setWorkflowShareUrl: core.metadata.setWorkflowShareUrl,
    setIsWorkflowLoading: core.metadata.setIsWorkflowLoading,
    setWorkflowLoadError: core.metadata.setWorkflowLoadError,
    setExecutions: core.execution.setExecutions,
    setActiveExecutionId: core.execution.setActiveExecutionId,
  });

  useWorkflowStorageListener({
    currentWorkflowId: core.metadata.currentWorkflowId,
    setWorkflowName: core.metadata.setWorkflowName,
    setWorkflowDescription: core.metadata.setWorkflowDescription,
    setWorkflowVersions: core.metadata.setWorkflowVersions,
    setWorkflowTags: core.metadata.setWorkflowTags,
    setChatkitStartScreenPrompts: core.metadata.setChatkitStartScreenPrompts,
    setChatkitSupportedModels: core.metadata.setChatkitSupportedModels,
  });
}
