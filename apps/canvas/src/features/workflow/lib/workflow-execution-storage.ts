import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";

import { mapHistoryToExecution } from "./workflow-execution-builders";
import { fetchWorkflowRemediations } from "./workflow-storage-api";
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
  const remediations = await fetchWorkflowRemediations({
    workflowId,
    limit: Math.max(limit * 4, 50),
  });
  const remediationsByRunId = new Map<string, typeof remediations>();
  for (const remediation of remediations) {
    const items = remediationsByRunId.get(remediation.run_id) ?? [];
    items.push(remediation);
    remediationsByRunId.set(remediation.run_id, items);
  }
  const workflow = options.workflow;
  const lookup: WorkflowLookup = {
    defaultNodes: workflow?.nodes ?? [],
    defaultEdges: workflow?.edges ?? [],
    defaultMapping: workflow?.versions?.at(-1)?.graphToCanvas ?? {},
    versions: new Map(
      (workflow?.versions ?? []).map((version) => [version.id, version]),
    ),
  };

  const executions = histories.map((history) => ({
    ...mapHistoryToExecution(history, lookup),
    remediations: remediationsByRunId.get(history.execution_id) ?? [],
  }));

  return executions.sort(
    (a, b) => new Date(b.startTime).getTime() - new Date(a.startTime).getTime(),
  );
};
