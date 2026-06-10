import { useState } from "react";
import { toast } from "@/hooks/use-toast";
import { getAccessToken } from "@features/auth/lib/auth-session";
import {
  buildWorkflowWebSocketProtocols,
  buildWorkflowWebSocketUrl,
  getBackendBaseUrl,
} from "@/lib/config";
import {
  fetchWorkflowVersions,
  selectLatestWorkflowVersion,
} from "@features/workflow/lib/workflow-storage-api";
import { useExecutionUpdates } from "@features/workflow/pages/workflow/hooks/use-execution-updates";
import { usePauseWorkflow } from "@features/workflow/pages/workflow/hooks/use-pause-workflow";
import { useExecutionTrace } from "@features/workflow/pages/workflow/hooks/use-execution-trace";
import { createExecutionRecord } from "@features/workflow/pages/workflow/hooks/execution-record";
import { setupExecutionWebSocket } from "@features/workflow/pages/workflow/hooks/workflow-runner-websocket";

const generateRandomId = (prefix: string) =>
  `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
import type { WorkflowCore } from "./use-workflow-core";

export interface WorkflowExecutionController {
  executionUpdates: ReturnType<typeof useExecutionUpdates>;
  handleRunPersistedWorkflow: () => Promise<void>;
  handlePauseWorkflow: () => void;
  isRunPending: boolean;
  trace: ReturnType<typeof useExecutionTrace>;
}

export function useWorkflowExecutionController(
  core: WorkflowCore,
): WorkflowExecutionController {
  const [isRunPending, setIsRunPending] = useState(false);

  const executionUpdates = useExecutionUpdates({
    setExecutions: core.execution.setExecutions,
    setIsRunning: core.execution.setIsRunning,
    websocketRef: core.websocketRef,
    isMountedRef: core.isMountedRef,
  });

  const executionIds = core.execution.executions.map(
    (execution) => execution.id,
  );

  const trace = useExecutionTrace({
    backendBaseUrl: core.chat.backendBaseUrl ?? getBackendBaseUrl(),
    workflowId: core.metadata.currentWorkflowId,
    activeExecutionId: core.execution.activeExecutionId,
    isMountedRef: core.isMountedRef,
    executionIds,
    enabled: core.ui.activeTab === "trace",
  });

  const handleRunPersistedWorkflow = async () => {
    const workflowId = core.metadata.currentWorkflowId;
    if (!workflowId) {
      toast({
        title: "Save workflow first",
        description:
          "Running requires a saved workflow with an ingested Python version.",
        variant: "destructive",
      });
      return;
    }

    setIsRunPending(true);
    try {
      const persistedVersions = await fetchWorkflowVersions(workflowId);
      const latestPersistedVersion =
        selectLatestWorkflowVersion(persistedVersions);
      if (!latestPersistedVersion) {
        throw new Error(
          "Running requires a saved workflow with an ingested Python version.",
        );
      }

      const latestVersionRecord =
        core.metadata.workflowVersions.find(
          (version) => version.id === latestPersistedVersion.id,
        ) ??
        core.metadata.workflowVersions.reduce<
          (typeof core.metadata.workflowVersions)[number] | undefined
        >(
          (latest, current) =>
            !latest || current.versionNumber > latest.versionNumber
              ? current
              : latest,
          undefined,
        );

      const graphToWorkflow = latestVersionRecord?.graphToWorkflow ?? {};
      const executionId = generateRandomId("run");
      const executionRecord = createExecutionRecord(executionId, graphToWorkflow);

      if (core.websocketRef.current) {
        core.websocketRef.current.close();
        core.websocketRef.current = null;
      }

      const token = getAccessToken();
      const websocketUrl = buildWorkflowWebSocketUrl(
        workflowId,
        getBackendBaseUrl(),
        token,
      );
      const websocketProtocols = buildWorkflowWebSocketProtocols(token);

      core.execution.setExecutions((prev) => [
        executionRecord,
        ...prev.filter((entry) => entry.id !== executionId),
      ]);
      core.execution.setActiveExecutionId(executionId);
      core.execution.setIsRunning(true);

      const ws = websocketProtocols
        ? new WebSocket(websocketUrl, websocketProtocols)
        : new WebSocket(websocketUrl);
      core.websocketRef.current = ws;

      setupExecutionWebSocket({
        ws,
        executionId,
        config: latestPersistedVersion.graph,
        graphToWorkflow,
        storedRunnableConfig: latestPersistedVersion.runnable_config,
        nodes: [],
        currentWorkflowId: workflowId,
        isMountedRef: core.isMountedRef,
        applyExecutionUpdate: executionUpdates.applyExecutionUpdate,
        setIsRunning: core.execution.setIsRunning,
        setExecutions: core.execution.setExecutions,
        websocketRef: core.websocketRef,
        onTraceUpdate: trace.handleTraceUpdate,
      });
    } catch (error) {
      core.execution.setIsRunning(false);
      toast({
        title: "Failed to run workflow",
        description:
          error instanceof Error
            ? error.message
            : "Unable to start workflow run.",
        variant: "destructive",
      });
    } finally {
      setIsRunPending(false);
    }
  };

  const handlePauseWorkflow = usePauseWorkflow({
    activeExecutionId: core.execution.activeExecutionId,
    isRunning: core.execution.isRunning,
    setIsRunning: core.execution.setIsRunning,
    setExecutions: core.execution.setExecutions,
    websocketRef: core.websocketRef,
  });

  return {
    executionUpdates,
    handleRunPersistedWorkflow,
    handlePauseWorkflow,
    isRunPending,
    trace,
  };
}
