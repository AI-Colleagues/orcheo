import type { NodeRuntimeData } from "@features/workflow/pages/workflow/hooks/execution-runtime-updates";
import type {
  NodeStatus,
  WorkflowExecution,
  WorkflowExecutionStatus,
} from "@features/workflow/pages/workflow/helpers/types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

interface ExecutionRecordUpdateParams {
  execution: WorkflowExecution;
  executionStatus: WorkflowExecutionStatus | null;
  nodeUpdates: Record<string, NodeStatus>;
  runtimeUpdates: Record<string, NodeRuntimeData>;
  logLevel: "INFO" | "DEBUG" | "ERROR" | "WARNING";
  message: string;
  timestamp: Date;
  graphToWorkflow: Record<string, string>;
}

export function updateExecutionRecord({
  execution,
  executionStatus,
  nodeUpdates,
  runtimeUpdates,
  logLevel,
  message,
  timestamp,
  graphToWorkflow,
}: ExecutionRecordUpdateParams): WorkflowExecution {
  const updatedNodes = execution.nodes.map((node) => {
    const nextStatus = nodeUpdates[node.id];
    const runtime = runtimeUpdates[node.id];
    let updatedNode = node;
    if (nextStatus) {
      updatedNode = { ...updatedNode, status: nextStatus };
    } else if (
      executionStatus &&
      executionStatus !== "running" &&
      node.status === "running"
    ) {
      const fallback: NodeStatus =
        executionStatus === "failed"
          ? "error"
          : executionStatus === "partial"
            ? "warning"
            : "success";
      updatedNode = { ...updatedNode, status: fallback };
    }

    if (runtime) {
      const existingDetails =
        node.details && isRecord(node.details)
          ? (node.details as Record<string, unknown>)
          : {};
      const nextDetails: Record<string, unknown> = {
        ...existingDetails,
      };
      if (runtime.inputs !== undefined) {
        nextDetails.inputs = runtime.inputs;
      }
      if (runtime.outputs !== undefined) {
        nextDetails.outputs = runtime.outputs;
      }
      if (runtime.messages !== undefined) {
        nextDetails.messages = runtime.messages;
      }
      nextDetails.raw = runtime.raw;
      nextDetails.updatedAt = runtime.updatedAt;
      updatedNode = { ...updatedNode, details: nextDetails };
    }

    return updatedNode;
  });

  const logs = [
    ...execution.logs,
    {
      timestamp: timestamp.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
      level: logLevel,
      message,
    },
  ];

  const duration =
    timestamp.getTime() - new Date(execution.startTime).getTime();

  const issues = logLevel === "ERROR" ? execution.issues + 1 : execution.issues;

  const metadata = {
    ...(execution.metadata ?? {}),
    graphToWorkflow: {
      ...(execution.metadata?.graphToWorkflow ?? {}),
      ...graphToWorkflow,
    },
  };

  const status = executionStatus ?? execution.status;
  const endTime =
    executionStatus && executionStatus !== "running"
      ? timestamp.toISOString()
      : execution.endTime;

  return {
    ...execution,
    status,
    nodes: updatedNodes,
    logs,
    duration,
    issues,
    endTime,
    metadata,
  };
}
