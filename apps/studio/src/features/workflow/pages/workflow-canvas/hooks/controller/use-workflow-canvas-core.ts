import { useEffect, useMemo, useRef } from "react";

import { getBackendBaseUrl } from "@/lib/config";
import { useWorkflowChat } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-chat";
import { useWorkflowMetadataState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-metadata-state";
import { useWorkflowExecutionState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-execution-state";
import { useStudioUiState } from "@features/workflow/pages/workflow-canvas/hooks/use-studio-ui-state";

export interface WorkflowCanvasCore {
  routeWorkflowId: string | null;
  routeWorkflowRef: string | null;
  metadata: ReturnType<typeof useWorkflowMetadataState>;
  execution: ReturnType<typeof useWorkflowExecutionState>;
  ui: ReturnType<typeof useStudioUiState>;
  websocketRef: React.MutableRefObject<WebSocket | null>;
  isMountedRef: React.MutableRefObject<boolean>;
  chat: ReturnType<typeof useWorkflowChat>;
  user: { id: string; name: string; avatar: string };
  ai: { id: string; name: string; avatar: string };
}

interface UseWorkflowCanvasCoreArgs {
  workflowId?: string;
  workflowRouteRef?: string | null;
}

export function useWorkflowCanvasCore({
  workflowId,
  workflowRouteRef,
}: UseWorkflowCanvasCoreArgs): WorkflowCanvasCore {
  const metadata = useWorkflowMetadataState();
  const execution = useWorkflowExecutionState();
  const ui = useStudioUiState();

  const websocketRef = useRef<WebSocket | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (websocketRef.current) {
        websocketRef.current.close();
        websocketRef.current = null;
      }
    };
  }, []);

  const user = useMemo(
    () => ({
      id: "user-1",
      name: "Avery Chen",
      avatar: "https://avatar.vercel.sh/avery",
    }),
    [],
  );
  const ai = useMemo(
    () => ({
      id: "ai-1",
      name: "Orcheo Assistant",
      avatar: "https://avatar.vercel.sh/orcheo-assistant",
    }),
    [],
  );

  const chat = useWorkflowChat({
    workflowId,
    backendBaseUrl: getBackendBaseUrl(),
    userName: user.name,
  });

  return {
    routeWorkflowId: workflowId ?? null,
    routeWorkflowRef: workflowRouteRef ?? null,
    metadata,
    execution,
    ui,
    websocketRef,
    isMountedRef,
    chat,
    user,
    ai,
  };
}
