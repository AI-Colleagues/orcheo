import type {
  WorkflowExecution,
  WorkflowExecutionNode,
} from "./workflow-execution.types";

type LogLevel = "INFO" | "DEBUG" | "ERROR" | "WARNING";
type NodeStatus = "idle" | "running" | "success" | "error" | "warning";

export const toExecutionStatus = (
  status: string,
): WorkflowExecution["status"] => {
  const normalised = status.toLowerCase();
  switch (normalised) {
    case "completed":
    case "success":
      return "success";
    case "error":
    case "failed":
      return "failed";
    case "cancelled":
    case "partial":
      return "partial";
    case "running":
    default:
      return "running";
  }
};

export const toNodeStatus = (
  status: WorkflowExecution["status"],
): NodeStatus => {
  switch (status) {
    case "running":
      return "running";
    case "failed":
      return "error";
    case "partial":
      return "warning";
    case "success":
    default:
      return "success";
  }
};

export const determineLogLevel = (
  payload: Record<string, unknown>,
): LogLevel => {
  const explicit = payload.level ?? payload.log_level;
  if (typeof explicit === "string") {
    const level = explicit.trim().toLowerCase();
    if (level === "debug") {
      return "DEBUG";
    }
    if (level === "error") {
      return "ERROR";
    }
    if (level === "warning" || level === "warn") {
      return "WARNING";
    }
  }

  if (typeof payload.error === "string" && payload.error.trim()) {
    return "ERROR";
  }

  const status =
    typeof payload.status === "string" ? payload.status.toLowerCase() : null;
  if (status === "error" || status === "failed") {
    return "ERROR";
  }
  if (status === "warning" || status === "cancelled" || status === "partial") {
    return "WARNING";
  }
  if (status === "debug") {
    return "DEBUG";
  }
  return "INFO";
};

export const resolveNodeLabel = (
  nodeId: string,
  nodes: Map<string, WorkflowExecutionNode>,
): string => nodes.get(nodeId)?.name ?? nodeId;

export const describePayload = (
  payload: Record<string, unknown>,
  graphToCanvas: Record<string, string>,
  nodes: Map<string, WorkflowExecutionNode>,
): string => {
  if (typeof payload.error === "string" && payload.error.trim()) {
    return `Run error: ${payload.error.trim()}`;
  }

  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }

  const nodeKey = ["node", "step", "name"].find(
    (key) => typeof payload[key] === "string" && payload[key],
  );

  const status =
    typeof payload.status === "string"
      ? payload.status.toLowerCase()
      : undefined;

  if (nodeKey) {
    const graphNode = String(payload[nodeKey]);
    const canvasNodeId = graphToCanvas[graphNode] ?? graphNode;
    const label = resolveNodeLabel(canvasNodeId, nodes);
    if (status) {
      return `Node ${label} ${status}`;
    }
    return `Node ${label} emitted an update`;
  }

  if (status) {
    return `Run status changed to ${status}`;
  }

  try {
    return JSON.stringify(payload);
  } catch {
    return String(payload);
  }
};

export const formatTimestamp = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
};

export const computeIssues = (
  logs: WorkflowExecution["logs"],
  error: string | null | undefined,
): number => {
  const issueCount = logs.filter(
    (log) => log.level !== "INFO" && log.level !== "DEBUG",
  ).length;
  return error ? issueCount + 1 : issueCount;
};
