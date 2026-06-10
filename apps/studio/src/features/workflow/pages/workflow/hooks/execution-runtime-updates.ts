import type { NodeStatus } from "@features/workflow/pages/workflow/helpers/types";

export interface NodeRuntimeData {
  inputs?: unknown;
  outputs?: unknown;
  messages?: unknown;
  raw?: unknown;
  updatedAt: string;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

// Kept for callers that pass node status updates alongside runtime data
export type { NodeStatus };

export function collectRuntimeUpdates(
  payload: Record<string, unknown>,
  graphToWorkflow: Record<string, string>,
  updatedAt: string,
): Record<string, NodeRuntimeData> {
  const runtimeUpdates: Record<string, NodeRuntimeData> = {};
  Object.entries(payload).forEach(([key, value]) => {
    if (typeof key !== "string") {
      return;
    }
    if (
      key === "status" ||
      key === "level" ||
      key === "error" ||
      key === "message" ||
      key === "type" ||
      key === "timestamp" ||
      key === "step"
    ) {
      return;
    }
    const workflowNodeId = graphToWorkflow[key] ?? null;
    if (!workflowNodeId) {
      return;
    }
    if (!isRecord(value)) {
      return;
    }

    const resultsCandidate = value["results"];
    let candidatePayload: unknown;

    if (isRecord(resultsCandidate)) {
      candidatePayload =
        resultsCandidate[key] ??
        resultsCandidate[workflowNodeId] ??
        Object.values(resultsCandidate)[0];
    }

    if (candidatePayload === undefined) {
      const directValue =
        typeof value[key] !== "undefined" ? value[key] : undefined;
      if (directValue !== undefined) {
        candidatePayload = directValue;
      }
    }

    if (candidatePayload === undefined && value["value"] !== undefined) {
      candidatePayload = value["value"];
    }

    if (candidatePayload === undefined) {
      candidatePayload = value;
    }

    let inputs: unknown;
    let outputs: unknown;
    let messages: unknown;
    if (isRecord(candidatePayload)) {
      inputs =
        candidatePayload["inputs"] !== undefined
          ? candidatePayload["inputs"]
          : candidatePayload["input"];
      outputs =
        candidatePayload["outputs"] !== undefined
          ? candidatePayload["outputs"]
          : (candidatePayload["output"] ?? candidatePayload["result"]);
      messages = candidatePayload["messages"];
    }

    runtimeUpdates[workflowNodeId] = {
      ...(inputs !== undefined ? { inputs } : {}),
      ...(outputs !== undefined ? { outputs } : {}),
      ...(messages !== undefined ? { messages } : {}),
      raw: candidatePayload,
      updatedAt,
    };
  });
  return runtimeUpdates;
}
