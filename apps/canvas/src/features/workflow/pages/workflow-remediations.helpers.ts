import type {
  ApiWorkflowRunRemediation,
  WorkflowRunRemediationStatus,
} from "@features/workflow/lib/workflow-storage.types";

export type RemediationStatusFilter = "all" | WorkflowRunRemediationStatus;

export interface RemediationWorkflowLookup {
  workflowName: string;
}

export const sortRemediationsByUpdatedAt = (
  remediations: ApiWorkflowRunRemediation[],
): ApiWorkflowRunRemediation[] =>
  [...remediations].sort((left, right) => {
    const leftTimestamp = new Date(
      left.updated_at ?? left.created_at ?? 0,
    ).getTime();
    const rightTimestamp = new Date(
      right.updated_at ?? right.created_at ?? 0,
    ).getTime();
    return rightTimestamp - leftTimestamp;
  });

export const formatRemediationTimestamp = (
  timestamp?: string | null,
): string => {
  if (!timestamp) {
    return "Unknown";
  }
  return new Date(timestamp).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
};

export const summarizeRemediationNote = (
  note?: string | null,
): string => {
  if (!note) {
    return "No developer note recorded.";
  }

  const firstLine = note
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.length > 0);

  return firstLine ?? "No developer note recorded.";
};

export const filterRemediations = (
  remediations: ApiWorkflowRunRemediation[],
  options: {
    query: string;
    statusFilter: RemediationStatusFilter;
    workflowNamesById: Map<string, string>;
  },
): ApiWorkflowRunRemediation[] => {
  const normalizedQuery = options.query.trim().toLowerCase();

  return remediations.filter((remediation) => {
    if (options.statusFilter !== "all" && remediation.status !== options.statusFilter) {
      return false;
    }

    if (!normalizedQuery) {
      return true;
    }

    const workflowName =
      options.workflowNamesById.get(remediation.workflow_id) ??
      remediation.workflow_id;
    const haystack = [
      workflowName,
      remediation.workflow_id,
      remediation.workflow_version_id,
      remediation.run_id,
      remediation.status,
      remediation.classification,
      remediation.action,
      remediation.developer_note,
      remediation.last_error,
      remediation.created_version_id,
    ]
      .filter((value): value is string => typeof value === "string")
      .join(" ")
      .toLowerCase();

    return haystack.includes(normalizedQuery);
  });
};
