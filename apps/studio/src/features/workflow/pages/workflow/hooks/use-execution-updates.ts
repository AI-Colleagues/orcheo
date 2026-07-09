import { useCallback } from "react";
import type { MutableRefObject } from "react";

import {
  determineLogLevel as determineLogLevelRaw,
  describePayload as describePayloadRaw,
} from "@features/workflow/pages/workflow/hooks/execution-log-helpers";
import {
  deriveNodeStatusUpdates,
  nextExecutionStatus,
} from "@features/workflow/pages/workflow/hooks/execution-node-status";
import { collectRuntimeUpdates } from "@features/workflow/pages/workflow/hooks/execution-runtime-updates";
import { updateExecutionRecord } from "@features/workflow/pages/workflow/hooks/execution-record-updater";

import type { WorkflowExecution } from "@features/workflow/pages/workflow/helpers/types";

interface UseExecutionUpdatesParams {
  setExecutions: React.Dispatch<React.SetStateAction<WorkflowExecution[]>>;
  setIsRunning: React.Dispatch<React.SetStateAction<boolean>>;
  websocketRef: MutableRefObject<WebSocket | null>;
  isMountedRef: MutableRefObject<boolean>;
}

interface ExecutionUpdateHandlers {
  determineLogLevel: (
    payload: Record<string, unknown>,
  ) => "INFO" | "DEBUG" | "ERROR" | "WARNING";
  describePayload: (
    payload: Record<string, unknown>,
    graphToWorkflow: Record<string, string>,
  ) => string;
  applyExecutionUpdate: (
    executionId: string,
    payload: Record<string, unknown>,
    graphToWorkflow: Record<string, string>,
  ) => void;
}

export function useExecutionUpdates({
  setExecutions,
  setIsRunning,
  websocketRef,
  isMountedRef,
}: UseExecutionUpdatesParams): ExecutionUpdateHandlers {
  const determineLogLevel = useCallback(
    (payload: Record<string, unknown>) => determineLogLevelRaw(payload),
    [],
  );

  const describePayload = useCallback(
    (
      payload: Record<string, unknown>,
      graphToWorkflow: Record<string, string>,
    ) => describePayloadRaw(payload, graphToWorkflow, (id) => id),
    [],
  );

  const applyExecutionUpdate = useCallback(
    (
      executionId: string,
      payload: Record<string, unknown>,
      graphToWorkflow: Record<string, string>,
    ) => {
      if (!isMountedRef.current) {
        return;
      }

      const hasNodeReference = ["node", "step", "name"].some(
        (key) => typeof payload[key] === "string" && payload[key],
      );
      const executionStatus = nextExecutionStatus(payload, hasNodeReference);

      const nodeUpdates = deriveNodeStatusUpdates(payload, graphToWorkflow);
      const timestamp = new Date();
      const updatedAt = timestamp.toISOString();
      const runtimeUpdates = collectRuntimeUpdates(
        payload,
        graphToWorkflow,
        updatedAt,
      );

      const logLevel = determineLogLevel(payload);
      const message = describePayload(payload, graphToWorkflow);

      setExecutions((prev) =>
        prev.map((execution) => {
          if (execution.id !== executionId) {
            return execution;
          }

          return updateExecutionRecord({
            execution,
            executionStatus,
            nodeUpdates,
            runtimeUpdates,
            logLevel,
            message,
            timestamp,
            graphToWorkflow,
          });
        }),
      );

      if (executionStatus && executionStatus !== "running") {
        setIsRunning(false);
        if (websocketRef.current) {
          websocketRef.current.close();
          websocketRef.current = null;
        }
      }
    },
    [
      describePayload,
      determineLogLevel,
      isMountedRef,
      setExecutions,
      setIsRunning,
      websocketRef,
    ],
  );

  return {
    determineLogLevel,
    describePayload,
    applyExecutionUpdate,
  };
}
