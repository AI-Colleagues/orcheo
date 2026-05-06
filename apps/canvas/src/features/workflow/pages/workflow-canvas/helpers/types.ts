import React from "react";
import type { Edge, Node } from "@xyflow/react";

import type {
  WorkflowEdge as PersistedWorkflowEdge,
  WorkflowNode as PersistedWorkflowNode,
} from "@features/workflow/data/workflow-data";
import type {
  WorkflowExecution as SharedWorkflowExecution,
  WorkflowExecutionNode as SharedWorkflowExecutionNode,
  WorkflowExecutionNodeStatus as SharedWorkflowExecutionNodeStatus,
  WorkflowExecutionStatus as SharedWorkflowExecutionStatus,
} from "@features/workflow/lib/workflow-execution.types";

export interface NodeRuntimeData {
  inputs?: unknown;
  outputs?: unknown;
  messages?: unknown;
  raw?: unknown;
  updatedAt: string;
}

export interface NodeData {
  type: string;
  label: string;
  description?: string;
  status: "idle" | "running" | "success" | "error" | "warning";
  iconKey?: string;
  icon?: React.ReactNode;
  onOpenChat?: () => void;
  onDelete?: (id: string) => void;
  isDisabled?: boolean;
  runtime?: NodeRuntimeData;
  code?: string;
  [key: string]: unknown;
}

export type CanvasNode = Node<NodeData>;
export type CanvasEdge = Edge<Record<string, unknown>>;

export interface WorkflowSnapshot {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

export type WorkflowClipboardPayload = {
  version: 1;
  type: "workflow-selection";
  nodes: PersistedWorkflowNode[];
  edges: PersistedWorkflowEdge[];
  copiedAt?: number;
};

export type CopyClipboardOptions = {
  skipSuccessToast?: boolean;
};

export type CopyClipboardResult = {
  success: boolean;
  nodeCount: number;
  edgeCount: number;
  usedFallback: boolean;
};

export type WorkflowExecutionStatus = SharedWorkflowExecutionStatus;
export type NodeStatus = SharedWorkflowExecutionNodeStatus;
export type WorkflowExecutionNode = SharedWorkflowExecutionNode;
export type WorkflowExecution = SharedWorkflowExecution;

export interface RunHistoryStep {
  index: number;
  at: string;
  payload: Record<string, unknown>;
}

export interface RunHistoryResponse {
  execution_id: string;
  workflow_id: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  error?: string | null;
  inputs?: Record<string, unknown>;
  steps: RunHistoryStep[];
}

export interface SidebarNodeDefinition {
  id?: string;
  type?: string;
  name?: string;
  description?: string;
  iconKey?: string;
  icon?: React.ReactNode;
  data?: Record<string, unknown>;
}
