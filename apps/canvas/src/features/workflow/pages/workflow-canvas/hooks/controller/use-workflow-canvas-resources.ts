import { getBackendBaseUrl } from "@/lib/config";
import { useWorkflowCredentials } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-credentials";
import { useWorkflowListeners } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-listeners";
import { useWorkflowSaver } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-saver";

import type { WorkflowCanvasCore } from "./use-workflow-canvas-core";

export interface WorkflowCanvasResources {
  credentials: ReturnType<typeof useWorkflowCredentials>;
  listeners: ReturnType<typeof useWorkflowListeners>;
  saver: ReturnType<typeof useWorkflowSaver>;
}

export function useWorkflowCanvasResources(
  core: WorkflowCanvasCore,
  workflowId: string | undefined,
): WorkflowCanvasResources {
  const credentials = useWorkflowCredentials({
    routeWorkflowId: workflowId,
    currentWorkflowId: core.metadata.currentWorkflowId,
    backendBaseUrl: getBackendBaseUrl(),
    userName: core.user.name,
  });

  const listeners = useWorkflowListeners({
    routeWorkflowId: workflowId,
    currentWorkflowId: core.metadata.currentWorkflowId,
    workflowVersionCount: core.metadata.workflowVersions.length,
    actor: core.user.name,
    enabled: core.ui.activeTab === "settings",
  });

  const saver = useWorkflowSaver({
    setWorkflowName: core.metadata.setWorkflowName,
    setWorkflowDescription: core.metadata.setWorkflowDescription,
    setWorkflowVersions: core.metadata.setWorkflowVersions,
    setWorkflowTags: core.metadata.setWorkflowTags,
    workflowName: core.metadata.workflowName,
    workflowDescription: core.metadata.workflowDescription,
    workflowTags: core.metadata.workflowTags,
    currentWorkflowId: core.metadata.currentWorkflowId,
  });

  return {
    credentials,
    listeners,
    saver,
  };
}
