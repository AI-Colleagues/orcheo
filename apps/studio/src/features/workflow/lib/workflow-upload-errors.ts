import type { StoredWorkflow } from "./workflow-storage.types";

export interface WorkflowUploadError {
  message: string;
  occurredAt: string;
}

const STORAGE_PREFIX = "orcheo.workflowUploadError.";

const storageKey = (workflowId: string): string => `${STORAGE_PREFIX}${workflowId}`;

const canUseSessionStorage = (): boolean =>
  typeof window !== "undefined" && Boolean(window.sessionStorage);

export const saveWorkflowUploadError = (
  workflowId: string,
  message: string,
): WorkflowUploadError => {
  const error = {
    message,
    occurredAt: new Date().toISOString(),
  };

  if (!canUseSessionStorage()) {
    return error;
  }

  try {
    window.sessionStorage.setItem(storageKey(workflowId), JSON.stringify(error));
  } catch {
    // Upload failure rendering should not depend on storage availability.
  }

  return error;
};

export const readWorkflowUploadError = (
  workflowId: string,
): WorkflowUploadError | undefined => {
  if (!canUseSessionStorage()) {
    return undefined;
  }

  try {
    const raw = window.sessionStorage.getItem(storageKey(workflowId));
    if (!raw) {
      return undefined;
    }
    const parsed = JSON.parse(raw) as Partial<WorkflowUploadError>;
    if (
      typeof parsed.message === "string" &&
      parsed.message.trim().length > 0 &&
      typeof parsed.occurredAt === "string"
    ) {
      return {
        message: parsed.message,
        occurredAt: parsed.occurredAt,
      };
    }
  } catch {
    return undefined;
  }

  return undefined;
};

export const clearWorkflowUploadError = (workflowId: string): void => {
  if (!canUseSessionStorage()) {
    return;
  }

  try {
    window.sessionStorage.removeItem(storageKey(workflowId));
  } catch {
    // Ignore storage errors; the next successful version is authoritative.
  }
};

export const getWorkflowUploadError = (
  workflow: Pick<StoredWorkflow, "versions" | "uploadError">,
): WorkflowUploadError | undefined => workflow.uploadError;
