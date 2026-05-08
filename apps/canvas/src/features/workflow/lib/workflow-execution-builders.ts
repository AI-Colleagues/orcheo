import {
  computeIssues,
  describePayload,
  determineLogLevel,
  formatTimestamp,
  toExecutionStatus,
  toNodeStatus,
} from "./workflow-execution-formatters";
import type {
  RunHistoryResponse,
  SnapshotEdge,
  SnapshotNode,
  WorkflowExecution,
  WorkflowLookup,
  WorkflowExecutionEdge,
  WorkflowExecutionNode,
} from "./workflow-execution.types";

const buildNodesFromSnapshot = (
  nodes: SnapshotNode[],
  status: WorkflowExecution["status"],
): WorkflowExecutionNode[] => {
  const resolvedStatus = toNodeStatus(status);
  return nodes.map((node) => ({
    id: node.id,
    type: node.type,
    name:
      typeof node.data?.label === "string" && node.data.label.trim()
        ? node.data.label
        : node.id,
    position: { ...node.position },
    status: resolvedStatus,
    iconKey:
      typeof node.data?.iconKey === "string" ? node.data.iconKey : undefined,
    details: node.data ? { ...node.data } : undefined,
  }));
};

const buildEdgesFromSnapshot = (edges: SnapshotEdge[]): WorkflowExecutionEdge[] =>
  edges.map((edge) => ({
    id: edge.id ?? `${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
  }));

const extractVersionRecord = (
  history: RunHistoryResponse,
  lookup: WorkflowLookup,
) => {
  const inputs = history.inputs ?? {};
  const metadata = (inputs.metadata ?? inputs.canvas ?? {}) as Record<
    string,
    unknown
  >;
  const versionIdRaw = metadata?.workflow_version_id;
  if (typeof versionIdRaw === "string" && versionIdRaw) {
    return lookup.versions.get(versionIdRaw);
  }
  return undefined;
};

export const mapHistoryToExecution = (
  history: RunHistoryResponse,
  lookup: WorkflowLookup,
): WorkflowExecution => {
  const status = toExecutionStatus(history.status);
  const version = extractVersionRecord(history, lookup);
  const snapshotNodes = version?.snapshot.nodes ?? lookup.defaultNodes;
  const snapshotEdges = version?.snapshot.edges ?? lookup.defaultEdges;
  const graphMapping = version?.graphToCanvas ?? lookup.defaultMapping;

  const nodes = buildNodesFromSnapshot(snapshotNodes, status);
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const edges = buildEdgesFromSnapshot(snapshotEdges);

  const logs = history.steps.map((step) => ({
    timestamp: formatTimestamp(step.at),
    level: determineLogLevel(step.payload),
    message: describePayload(step.payload, graphMapping, nodeMap),
  }));

  const startTime = history.started_at;
  const completedAt = history.completed_at ?? undefined;
  const start = new Date(startTime).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const duration = Number.isFinite(start) ? Math.max(0, end - start) : 0;

  return {
    id: history.execution_id,
    runId: history.execution_id,
    status,
    startTime,
    endTime: completedAt,
    duration,
    issues: computeIssues(logs, history.error ?? undefined),
    nodes,
    edges,
    logs,
    metadata: { graphToCanvas: graphMapping },
  };
};
