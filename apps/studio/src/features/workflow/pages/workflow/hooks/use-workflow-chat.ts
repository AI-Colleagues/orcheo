import { useCallback, useEffect, useRef, useState } from "react";

import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import {
  requestWorkflowChatSession,
  type WorkflowChatSession,
} from "@features/chatkit/lib/workflow-session";
import { recordChatTelemetry } from "@features/chatkit/lib/telemetry";

export type ChatSessionStatus = "idle" | "loading" | "ready" | "error";

type UseWorkflowChatArgs = {
  workflowId: string | null | undefined;
  backendBaseUrl: string | null;
  userName: string;
};

const SESSION_REFRESH_BUFFER_MS = 30_000;

export const useWorkflowChat = ({
  workflowId,
  backendBaseUrl,
  userName,
}: UseWorkflowChatArgs) => {
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [activeChatNodeId, setActiveChatNodeId] = useState<string | null>(null);
  const [chatTitle, setChatTitle] = useState("Chat");
  const [sessionStatus, setSessionStatus] = useState<ChatSessionStatus>("idle");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const sessionRef = useRef<WorkflowChatSession | null>(null);
  const pendingSessionRef = useRef<Promise<string> | null>(null);
  const previousWorkflowIdRef = useRef<string | null | undefined>(workflowId);

  const resetSession = useCallback(() => {
    sessionRef.current = null;
    pendingSessionRef.current = null;
    setSessionStatus("idle");
    setSessionError(null);
  }, []);

  useEffect(() => {
    if (workflowId === previousWorkflowIdRef.current) {
      return;
    }
    previousWorkflowIdRef.current = workflowId;
    resetSession();
  }, [resetSession, workflowId]);

  const isSessionValid = useCallback((session: WorkflowChatSession | null) => {
    if (!session?.clientSecret) {
      return false;
    }
    if (!session.expiresAt) {
      return true;
    }
    return Date.now() + SESSION_REFRESH_BUFFER_MS < session.expiresAt;
  }, []);

  const refreshSession = useCallback(async () => {
    if (!workflowId) {
      const message = "Save the workflow before opening ChatKit.";
      setSessionStatus("error");
      setSessionError(message);
      recordChatTelemetry("studio.chat.session.failure", {
        reason: "missing_workflow",
      });
      throw new Error(message);
    }

    if (pendingSessionRef.current) {
      return pendingSessionRef.current;
    }

    const pending = (async () => {
      setSessionStatus("loading");
      setSessionError(null);
      try {
        const session = await requestWorkflowChatSession(
          workflowId,
          backendBaseUrl ?? undefined,
        );
        sessionRef.current = session;
        setSessionStatus("ready");
        recordChatTelemetry("studio.chat.session.success", { workflowId });
        return session.clientSecret;
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Unable to start a ChatKit session.";
        setSessionStatus("error");
        setSessionError(message);
        sessionRef.current = null;
        recordChatTelemetry("studio.chat.session.failure", {
          workflowId,
          message,
        });
        throw error instanceof Error ? error : new Error(message);
      } finally {
        pendingSessionRef.current = null;
      }
    })();

    pendingSessionRef.current = pending;
    return pending;
  }, [backendBaseUrl, workflowId]);

  const getClientSecret = useCallback(
    async (currentSecret: string | null) => {
      void currentSecret;
      const reusableSession = sessionRef.current;
      if (reusableSession && isSessionValid(reusableSession)) {
        return reusableSession.clientSecret;
      }
      return refreshSession();
    },
    [isSessionValid, refreshSession],
  );

  const handleOpenChat = useCallback(() => {
    setChatTitle("Chat");
    setActiveChatNodeId(null);
    setIsChatOpen(true);
    recordChatTelemetry("studio.chat.open", { workflowId });
    void refreshSession();
  }, [refreshSession, workflowId]);

  const handleCloseChat = useCallback(() => {
    setIsChatOpen(false);
    setActiveChatNodeId(null);
    recordChatTelemetry("studio.chat.close", { workflowId });
  }, [workflowId]);

  const handleChatResponseStart = useCallback(() => {}, []);

  const handleChatResponseEnd = useCallback(() => {}, []);

  const handleChatClientTool = useCallback(
    async (toolCall: { name: string; params: Record<string, unknown> }) => {
      if (toolCall.name !== "orcheo.run_workflow") {
        return {};
      }

      if (!workflowId) {
        throw new Error("Cannot trigger workflow without a workflow ID");
      }

      const params = toolCall.params ?? {};
      const rawMessage =
        typeof params.message === "string" ? params.message : "";
      const threadId =
        typeof params.threadId === "string"
          ? params.threadId
          : typeof params.thread_id === "string"
            ? params.thread_id
            : null;

      const metadata = { ...(params as Record<string, unknown>) };
      delete metadata.message;
      delete metadata.threadId;
      delete metadata.thread_id;

      const response = await authFetch(
        buildBackendHttpUrl(
          `/api/chatkit/workflows/${workflowId}/trigger`,
          backendBaseUrl,
        ),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: rawMessage,
            actor: userName,
            client_thread_id: threadId,
            metadata,
          }),
        },
      );

      if (!response.ok) {
        throw new Error("Failed to trigger workflow via ChatKit client tool");
      }

      const result = (await response.json()) as Record<string, unknown>;
      return result;
    },
    [backendBaseUrl, userName, workflowId],
  );

  return {
    isChatOpen,
    setIsChatOpen,
    activeChatNodeId,
    setActiveChatNodeId,
    chatTitle,
    setChatTitle,
    handleOpenChat,
    handleCloseChat,
    handleChatResponseStart,
    handleChatResponseEnd,
    handleChatClientTool,
    getClientSecret,
    refreshSession,
    sessionStatus,
    sessionError,
    workflowId: workflowId ?? null,
    backendBaseUrl,
  };
};
