import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";

import { mapHistoryToExecution } from "./workflow-execution-builders";
import type {
  RunHistoryResponse,
  WorkflowExecution,
  WorkflowLookup,
} from "./workflow-execution.types";
import type { StoredWorkflow } from "@features/workflow/lib/workflow-storage";

export interface LoadWorkflowExecutionsOptions {
  workflow?: StoredWorkflow;
  limit?: number;
  backendBaseUrl?: string;
}

export const loadWorkflowExecutions = async (
  workflowId: string,
  options: LoadWorkflowExecutionsOptions = {},
): Promise<WorkflowExecution[]> => {
  if (!workflowId) {
    return [];
  }
  if (typeof fetch === "undefined") {
    throw new Error("Fetch API is not available in this environment.");
  }

  const limit = options.limit ?? 50;
  const url = buildBackendHttpUrl(
    `/api/workflows/${workflowId}/executions?limit=${encodeURIComponent(String(limit))}`,
    options.backendBaseUrl,
  );

  const response = await authFetch(url);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail || `Failed to load execution history (${response.status})`,
    );
  }

  const histories = (await response.json()) as RunHistoryResponse[];
  const workflow = options.workflow;
  const lookup: WorkflowLookup = {
    // @legacy TODO: remove after mermaid-only migration — defaultNodes/defaultEdges
    // were populated from template nodes; new workflows use nodes: [] with mermaid.
    defaultNodes: workflow?.nodes ?? [],
    defaultEdges: workflow?.edges ?? [],
    defaultMapping: workflow?.versions?.at(-1)?.graphToWorkflow ?? {},
    versions: new Map(
      (workflow?.versions ?? []).map((version) => [version.id, version]),
    ),
  };

  const executions = histories.map((history) =>
    mapHistoryToExecution(history, lookup),
  );

  return executions.sort(
    (a, b) => new Date(b.startTime).getTime() - new Date(a.startTime).getTime(),
  );
};
