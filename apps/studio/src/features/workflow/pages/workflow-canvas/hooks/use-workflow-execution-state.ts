import { useState } from "react";

import type { WorkflowExecution } from "@features/workflow/pages/workflow-canvas/helpers/types";

export function useWorkflowExecutionState() {
  const [executions, setExecutions] = useState<WorkflowExecution[]>([]);
  const [activeExecutionId, setActiveExecutionId] = useState<string | null>(
    null,
  );
  const [isRunning, setIsRunning] = useState(false);

  return {
    executions,
    setExecutions,
    activeExecutionId,
    setActiveExecutionId,
    isRunning,
    setIsRunning,
  };
}
