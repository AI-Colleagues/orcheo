import {
  executionStatusFromValue,
  nodeStatusFromValue,
} from "@features/workflow/pages/workflow-canvas/helpers/execution";

import type {
  NodeStatus,
  WorkflowExecutionStatus,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

export function deriveNodeStatusUpdates(
  payload: Record<string, unknown>,
  graphToCanvas: Record<string, string>,
): Record<string, NodeStatus> {
  const nodeKey = ["node", "step", "name"].find(
    (key) => typeof payload[key] === "string" && payload[key],
  );
  if (!nodeKey) {
    return {};
  }
  const statusValue =
    typeof payload.status === "string" ? payload.status : undefined;
  if (!statusValue) {
    return {};
  }
  const graphNode = String(payload[nodeKey]);
  const canvasNodeId = graphToCanvas[graphNode] ?? graphNode;
  const status = nodeStatusFromValue(statusValue);
  return { [canvasNodeId]: status };
}

export function nextExecutionStatus(
  payload: Record<string, unknown>,
  hasNodeReference: boolean,
): WorkflowExecutionStatus | null {
  const statusValue =
    typeof payload.status === "string" ? payload.status : undefined;
  let executionStatus = executionStatusFromValue(statusValue);
  if (hasNodeReference) {
    executionStatus = null;
  }
  if (typeof payload.error === "string" && payload.error.trim()) {
    executionStatus = "failed";
  }
  return executionStatus;
}
