import { useCallback, useEffect, useState } from "react";

import { fetchWorkflowCredentialReadiness } from "@features/workflow/lib/workflow-storage-api";
import type { WorkflowCredentialReadinessResponse } from "@features/workflow/lib/workflow-storage.types";

type UseWorkflowCredentialReadinessArgs = {
  workflowId: string | null | undefined;
  refreshKey?: unknown;
};

export type WorkflowCredentialReadiness = {
  readiness: WorkflowCredentialReadinessResponse | null;
  missingCredentials: string[];
  isLoading: boolean;
  refresh: () => void;
};

export const useWorkflowCredentialReadiness = ({
  workflowId,
  refreshKey,
}: UseWorkflowCredentialReadinessArgs): WorkflowCredentialReadiness => {
  const [readiness, setReadiness] =
    useState<WorkflowCredentialReadinessResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => {
    setNonce((current) => current + 1);
  }, []);

  useEffect(() => {
    if (!workflowId) {
      setReadiness(null);
      setIsLoading(false);
      return;
    }

    let isActive = true;
    setIsLoading(true);

    fetchWorkflowCredentialReadiness(workflowId)
      .then((payload) => {
        if (!isActive) {
          return;
        }
        setReadiness(payload ?? null);
      })
      .catch(() => {
        if (!isActive) {
          return;
        }
        setReadiness(null);
      })
      .finally(() => {
        if (!isActive) {
          return;
        }
        setIsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, [workflowId, nonce, refreshKey]);

  return {
    readiness,
    missingCredentials: readiness?.missing_credentials ?? [],
    isLoading,
    refresh,
  };
};
