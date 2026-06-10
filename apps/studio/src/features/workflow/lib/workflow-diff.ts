import type {
  WorkflowNode,
  WorkflowEdge,
} from "@features/workflow/data/workflow-data";

export type WorkflowDiffType = "added" | "removed" | "modified";
export type WorkflowDiffEntity = "workflow" | "node" | "edge";

export interface WorkflowSnapshot {
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface WorkflowDiffEntry {
  id: string;
  type: WorkflowDiffType;
  entity: WorkflowDiffEntity;
  name: string;
  detail?: string;
  before?: unknown;
  after?: unknown;
}

export interface WorkflowDiffResult {
  summary: {
    added: number;
    removed: number;
    modified: number;
  };
  entries: WorkflowDiffEntry[];
}

const serializeNode = (node: WorkflowNode) => ({
  id: node.id,
  type: node.type,
  position: node.position,
  data: node.data,
});

const serializeEdge = (edge: WorkflowEdge) => ({
  id: edge.id,
  source: edge.source,
  target: edge.target,
  sourceHandle: edge.sourceHandle,
  targetHandle: edge.targetHandle,
  label: edge.label,
  type: edge.type,
});

const nodesEqual = (a: WorkflowNode, b: WorkflowNode) => {
  return JSON.stringify(serializeNode(a)) === JSON.stringify(serializeNode(b));
};

const edgesEqual = (a: WorkflowEdge, b: WorkflowEdge) => {
  return JSON.stringify(serializeEdge(a)) === JSON.stringify(serializeEdge(b));
};

const buildNodeDetail = (node: WorkflowNode) => {
  const { label, status, description } = node.data ?? {};
  return [label, status, description].filter(Boolean).join(" · ");
};

export const computeWorkflowDiff = (
  before: WorkflowSnapshot,
  after: WorkflowSnapshot,
): WorkflowDiffResult => {
  const entries: WorkflowDiffEntry[] = [];

  if (before.name !== after.name) {
    entries.push({
      id: `workflow-name-${after.name}`,
      type: "modified",
      entity: "workflow",
      name: "Workflow name",
      before: before.name,
      after: after.name,
    });
  }

  if ((before.description ?? "") !== (after.description ?? "")) {
    entries.push({
      id: `workflow-description-${after.name}`,
      type: "modified",
      entity: "workflow",
      name: "Description",
      before: before.description ?? "",
      after: after.description ?? "",
    });
  }

  const beforeNodes = new Map(before.nodes.map((node) => [node.id, node]));
  const afterNodes = new Map(after.nodes.map((node) => [node.id, node]));

  after.nodes.forEach((node) => {
    const previous = beforeNodes.get(node.id);
    if (!previous) {
      entries.push({
        id: `node-added-${node.id}`,
        type: "added",
        entity: "node",
        name: node.data?.label ?? node.id,
        detail: buildNodeDetail(node),
        after: serializeNode(node),
      });
      return;
    }

    if (!nodesEqual(previous, node)) {
      entries.push({
        id: `node-modified-${node.id}`,
        type: "modified",
        entity: "node",
        name: node.data?.label ?? node.id,
        detail: buildNodeDetail(node),
        before: serializeNode(previous),
        after: serializeNode(node),
      });
    }
  });

  before.nodes.forEach((node) => {
    if (!afterNodes.has(node.id)) {
      entries.push({
        id: `node-removed-${node.id}`,
        type: "removed",
        entity: "node",
        name: node.data?.label ?? node.id,
        detail: buildNodeDetail(node),
        before: serializeNode(node),
      });
    }
  });

  const beforeEdges = new Map(before.edges.map((edge) => [edge.id, edge]));
  const afterEdges = new Map(after.edges.map((edge) => [edge.id, edge]));

  after.edges.forEach((edge) => {
    const previous = beforeEdges.get(edge.id);
    if (!previous) {
      entries.push({
        id: `edge-added-${edge.id}`,
        type: "added",
        entity: "edge",
        name: `${edge.source} → ${edge.target}`,
        after: serializeEdge(edge),
      });
      return;
    }

    if (!edgesEqual(previous, edge)) {
      entries.push({
        id: `edge-modified-${edge.id}`,
        type: "modified",
        entity: "edge",
        name: `${edge.source} → ${edge.target}`,
        before: serializeEdge(previous),
        after: serializeEdge(edge),
      });
    }
  });

  before.edges.forEach((edge) => {
    if (!afterEdges.has(edge.id)) {
      entries.push({
        id: `edge-removed-${edge.id}`,
        type: "removed",
        entity: "edge",
        name: `${edge.source} → ${edge.target}`,
        before: serializeEdge(edge),
      });
    }
  });

  const summary = entries.reduce(
    (acc, entry) => {
      if (entry.type === "added") {
        acc.added += 1;
      } else if (entry.type === "removed") {
        acc.removed += 1;
      } else if (entry.type === "modified") {
        acc.modified += 1;
      }
      return acc;
    },
    { added: 0, removed: 0, modified: 0 },
  );

  return { summary, entries };
};
