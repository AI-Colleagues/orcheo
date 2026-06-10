export type ExecutionLogLevel = "INFO" | "DEBUG" | "ERROR" | "WARNING";

export function determineLogLevel(
  payload: Record<string, unknown>,
): ExecutionLogLevel {
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
}

export function describePayload(
  payload: Record<string, unknown>,
  graphToCanvas: Record<string, string>,
  resolveNodeLabel: (nodeId: string) => string,
): string {
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
    const label = resolveNodeLabel(canvasNodeId);
    if (status) {
      return `Node ${label} ${status}`;
    }
    return `Node ${label} emitted an update`;
  }

  if (status) {
    return `Run status changed to ${status}`;
  }

  return JSON.stringify(payload);
}
