import type { WorkflowExecution } from "@features/workflow/pages/workflow-canvas/helpers/types";

export function createExecutionRecord(
  executionId: string,
  graphToCanvas: Record<string, string>,
): WorkflowExecution {
  const startTime = new Date();

  return {
    id: executionId,
    runId: executionId,
    status: "running",
    startTime: startTime.toISOString(),
    duration: 0,
    issues: 0,
    nodes: [],
    edges: [],
    logs: [
      {
        timestamp: startTime.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
        level: "INFO" as const,
        message: "Workflow execution started",
      },
    ],
    metadata: { graphToCanvas },
  };
}
