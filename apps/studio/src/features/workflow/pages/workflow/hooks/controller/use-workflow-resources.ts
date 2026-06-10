import { getBackendBaseUrl } from "@/lib/config";
import { useWorkflowCredentialReadiness } from "@features/workflow/pages/workflow/hooks/use-workflow-credential-readiness";
import { useWorkflowCredentials } from "@features/workflow/pages/workflow/hooks/use-workflow-credentials";
import { useWorkflowListeners } from "@features/workflow/pages/workflow/hooks/use-workflow-listeners";
import { useWorkflowSaver } from "@features/workflow/pages/workflow/hooks/use-workflow-saver";

import type { WorkflowCore } from "./use-workflow-core";

export interface WorkflowResources {
  credentials: ReturnType<typeof useWorkflowCredentials>;
  credentialReadiness: ReturnType<typeof useWorkflowCredentialReadiness>;
  listeners: ReturnType<typeof useWorkflowListeners>;
  saver: ReturnType<typeof useWorkflowSaver>;
}

export function useWorkflowResources(
  core: WorkflowCore,
  workflowId: string | undefined,
): WorkflowResources {
  const credentials = useWorkflowCredentials({
    routeWorkflowId: workflowId,
    currentWorkflowId: core.metadata.currentWorkflowId,
    backendBaseUrl: getBackendBaseUrl(),
    userName: core.user.name,
  });

  const credentialReadiness = useWorkflowCredentialReadiness({
    workflowId: core.metadata.currentWorkflowId ?? workflowId ?? null,
    refreshKey: credentials.credentials.length,
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
    credentialReadiness,
    listeners,
    saver,
  };
}
