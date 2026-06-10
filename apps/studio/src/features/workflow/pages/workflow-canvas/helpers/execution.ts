import type { NodeStatus, WorkflowExecutionStatus } from "./types";

export const nodeStatusFromValue = (value?: string): NodeStatus => {
  const normalised = value?.toLowerCase();
  switch (normalised) {
    case "running":
      return "running";
    case "error":
    case "failed":
      return "error";
    case "warning":
    case "cancelled":
    case "partial":
      return "warning";
    default:
      return "success";
  }
};

export const executionStatusFromValue = (
  value?: string,
): WorkflowExecutionStatus | null => {
  const normalised = value?.toLowerCase();
  switch (normalised) {
    case "running":
      return "running";
    case "completed":
    case "success":
      return "success";
    case "error":
    case "failed":
      return "failed";
    case "cancelled":
    case "partial":
      return "partial";
    default:
      return null;
  }
};
