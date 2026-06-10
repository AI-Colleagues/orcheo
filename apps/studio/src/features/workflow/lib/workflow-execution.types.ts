import type {
  StoredWorkflow,
  WorkflowVersionRecord,
} from "@features/workflow/lib/workflow-storage";

export interface RunHistoryStepResponse {
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
  steps: RunHistoryStepResponse[];
}

export type SnapshotNode = StoredWorkflow["nodes"][number];
export type SnapshotEdge = StoredWorkflow["edges"][number];

export type WorkflowLookup = {
  defaultNodes: SnapshotNode[];
  defaultEdges: SnapshotEdge[];
  defaultMapping: Record<string, string>;
  versions: Map<string, WorkflowVersionRecord>;
};

export type WorkflowExecutionStatus =
  | "running"
  | "success"
  | "failed"
  | "partial";

export type WorkflowExecutionNodeStatus =
  | "idle"
  | "running"
  | "success"
  | "error"
  | "warning";

export interface WorkflowExecutionNode {
  id: string;
  type: string;
  name: string;
  position: { x: number; y: number };
  status: WorkflowExecutionNodeStatus;
  iconKey?: string;
  details?: Record<string, unknown>;
}

export interface WorkflowExecutionEdge {
  id: string;
  source: string;
  target: string;
}

export interface WorkflowExecution {
  id: string;
  runId: string;
  status: WorkflowExecutionStatus;
  startTime: string;
  endTime?: string;
  duration: number;
  issues: number;
  nodes: WorkflowExecutionNode[];
  edges: WorkflowExecutionEdge[];
  logs: {
    timestamp: string;
    level: "INFO" | "DEBUG" | "ERROR" | "WARNING";
    message: string;
  }[];
  metadata?: {
    graphToCanvas?: Record<string, string>;
  };
}
