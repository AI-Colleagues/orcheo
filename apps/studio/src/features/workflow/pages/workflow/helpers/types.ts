import type {
  WorkflowExecution as SharedWorkflowExecution,
  WorkflowExecutionNode as SharedWorkflowExecutionNode,
  WorkflowExecutionNodeStatus as SharedWorkflowExecutionNodeStatus,
  WorkflowExecutionStatus as SharedWorkflowExecutionStatus,
} from "@features/workflow/lib/workflow-execution.types";

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
