import type { ApiWorkflowRunRemediation } from "@features/workflow/lib/workflow-storage.types";

export interface WorkflowNode {
  id: string;
  type: string;
  name: string;
  position: { x: number; y: number };
  iconKey?: string;
  status?: "success" | "error" | "running" | "idle" | "warning";
  details?: {
    method?: string;
    url?: string;
    message?: string;
    items?: number;
    description?: string;
    [key: string]: unknown;
  };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
}

export interface WorkflowExecution {
  id: string;
  runId: string;
  status: "success" | "failed" | "partial" | "running";
  startTime: string;
  endTime?: string;
  duration: number;
  issues: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  logs: {
    timestamp: string;
    level: "INFO" | "DEBUG" | "ERROR" | "WARNING";
    message: string;
  }[];
  metadata?: {
    graphToCanvas?: Record<string, string>;
  };
  remediations?: ApiWorkflowRunRemediation[];
}

export interface WorkflowExecutionHistoryProps {
  executions: WorkflowExecution[];
  onViewDetails?: (execution: WorkflowExecution) => void;
  onRefresh?: () => void;
  onCopyToEditor?: (execution: WorkflowExecution) => void;
  onDelete?: (execution: WorkflowExecution) => void;
  className?: string;
  showList?: boolean;
  defaultSelectedExecution?: WorkflowExecution;
}
