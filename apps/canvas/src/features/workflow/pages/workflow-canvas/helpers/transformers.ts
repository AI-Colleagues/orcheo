import { MarkerType } from "@xyflow/react";

import {
  getNodeIcon,
  inferNodeIconKey,
} from "@features/workflow/lib/node-icons";
import type {
  WorkflowEdge as PersistedWorkflowEdge,
  WorkflowNode as PersistedWorkflowNode,
} from "@features/workflow/data/workflow-data";

import type { CanvasEdge, CanvasNode, NodeData, NodeStatus } from "./types";
import { sanitizeNodeDataForPersist } from "./node-identity";

export const defaultNodeStyle = {
  background: "none",
  border: "none",
  padding: 0,
  borderRadius: 0,
  width: "auto",
  boxShadow: "none",
} as const;

export const toPersistedNode = (node: CanvasNode): PersistedWorkflowNode => ({
  id: node.id,
  type:
    typeof node.data?.type === "string"
      ? node.data.type
      : (node.type ?? "default"),
  position: {
    x: node.position?.x ?? 0,
    y: node.position?.y ?? 0,
  },
  data: sanitizeNodeDataForPersist(node.data),
});

export const toPersistedEdge = (edge: CanvasEdge): PersistedWorkflowEdge => ({
  id: edge.id,
  source: edge.source,
  target: edge.target,
  sourceHandle: edge.sourceHandle,
  targetHandle: edge.targetHandle,
  label: edge.label,
  type: edge.type,
  animated: edge.animated,
  style: edge.style,
});

export const resolveReactFlowType = (
  persistedType?: string,
): "default" | "chatTrigger" | "startEnd" | "stickyNote" => {
  if (!persistedType) {
    return "default";
  }

  if (persistedType === "chatTrigger") {
    return "chatTrigger";
  }

  if (persistedType === "stickyNote" || persistedType === "annotation") {
    return "stickyNote";
  }

  // @legacy TODO: remove after mermaid-only migration — "start"/"end" were
  // used by old hardcoded template nodes; new candidate templates use nodes: [].
  if (
    persistedType === "start" ||
    persistedType === "end" ||
    persistedType === "startEnd"
  ) {
    return "startEnd";
  }

  return "default";
};

export const toCanvasNodeBase = (node: PersistedWorkflowNode): CanvasNode => {
  const extraEntries = Object.entries(node.data ?? {}).filter(
    ([key]) => !["label", "description", "type", "isDisabled"].includes(key),
  );

  const extraData = Object.fromEntries(extraEntries);
  const semanticType = node.data?.type ?? node.type ?? "default";
  const extraDataRecord = { ...extraData } as Record<string, unknown>;
  const storedIconKeyRaw = extraDataRecord.iconKey;
  delete extraDataRecord.iconKey;
  delete extraDataRecord.icon;
  const otherExtraData = extraDataRecord;

  const label =
    typeof node.data?.label === "string" ? node.data.label : "New Node";
  const description =
    typeof node.data?.description === "string" ? node.data.description : "";

  const storedIconKey =
    typeof storedIconKeyRaw === "string" ? storedIconKeyRaw : undefined;
  const resolvedIconKey =
    inferNodeIconKey({
      iconKey: storedIconKey,
      label,
      type: semanticType,
    }) ?? storedIconKey;
  const icon = getNodeIcon(resolvedIconKey);

  return {
    id: node.id,
    type: resolveReactFlowType(node.type),
    position: node.position ?? { x: 0, y: 0 },
    style: defaultNodeStyle,
    data: {
      type: semanticType,
      label,
      description,
      status: (node.data?.status ?? "idle") as NodeStatus,
      isDisabled: node.data?.isDisabled,
      iconKey: resolvedIconKey,
      icon,
      ...otherExtraData,
    } as NodeData,
    draggable: true,
  };
};

export const toCanvasEdge = (edge: PersistedWorkflowEdge): CanvasEdge => ({
  id: edge.id ?? `edge-${edge.source}-${edge.target}`,
  source: edge.source,
  target: edge.target,
  sourceHandle: edge.sourceHandle,
  targetHandle: edge.targetHandle,
  label: edge.label,
  type: edge.type ?? "default",
  animated: edge.animated ?? false,
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 12,
    height: 12,
  },
  style: edge.style ?? { stroke: "#99a1b3", strokeWidth: 2 },
});

export const convertPersistedEdgesToCanvas = (edges: PersistedWorkflowEdge[]) =>
  edges.map(toCanvasEdge);
