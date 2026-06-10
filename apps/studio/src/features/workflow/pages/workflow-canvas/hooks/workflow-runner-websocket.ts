import { toast } from "@/hooks/use-toast";

import type { MutableRefObject } from "react";

import type {
  NodeStatus,
  WorkflowExecution,
} from "@features/workflow/pages/workflow-canvas/helpers/types";
import type { TraceUpdateMessage } from "@features/workflow/pages/workflow-canvas/helpers/trace";

interface WebSocketParams {
  ws: WebSocket;
  executionId: string;
  config: Record<string, unknown>;
  graphToCanvas: Record<string, string>;
  storedRunnableConfig?: Record<string, unknown> | null;
  nodes: unknown[];
  currentWorkflowId: string | null;
  isMountedRef: MutableRefObject<boolean>;
  applyExecutionUpdate: (
    executionId: string,
    payload: Record<string, unknown>,
    graphToCanvas: Record<string, string>,
  ) => void;
  setIsRunning: React.Dispatch<React.SetStateAction<boolean>>;
  setExecutions: React.Dispatch<React.SetStateAction<WorkflowExecution[]>>;
  websocketRef: MutableRefObject<WebSocket | null>;
  onTraceUpdate?: (update: TraceUpdateMessage) => void;
}

export function setupExecutionWebSocket({
  ws,
  executionId,
  config,
  graphToCanvas,
  storedRunnableConfig,
  nodes,
  currentWorkflowId,
  isMountedRef,
  applyExecutionUpdate,
  setIsRunning,
  setExecutions,
  websocketRef,
  onTraceUpdate,
}: WebSocketParams) {
  const startTime = new Date();

  ws.onopen = () => {
    const payload = {
      type: "run_workflow",
      graph_config: config,
      ...(storedRunnableConfig
        ? { stored_runnable_config: storedRunnableConfig }
        : {}),
      inputs: {
        canvas: {
          triggered_from: "canvas-app",
          workflow_id: currentWorkflowId ?? "canvas-preview",
          at: startTime.toISOString(),
        },
        metadata: {
          node_count: nodes.length,
          edge_count: graphToCanvas ? Object.keys(graphToCanvas).length : 0,
        },
      },
      execution_id: executionId,
    };
    ws.send(JSON.stringify(payload));
  };

  ws.onmessage = (event) => {
    if (!isMountedRef.current) {
      return;
    }
    try {
      const data = JSON.parse(event.data) as Record<string, unknown>;
      if (data?.type === "trace:update") {
        onTraceUpdate?.(data as TraceUpdateMessage);
        return;
      }
      applyExecutionUpdate(executionId, data, graphToCanvas);
    } catch (error) {
      console.error("Failed to parse workflow update", error);
      toast({
        title: "Workflow update error",
        description:
          error instanceof Error ? error.message : "Unknown parsing error",
        variant: "destructive",
      });
    }
  };

  ws.onerror = () => {
    if (!isMountedRef.current) {
      return;
    }
    const timestamp = new Date();
    setIsRunning(false);
    setExecutions((prev) =>
      prev.map((execution) => {
        if (execution.id !== executionId) {
          return execution;
        }
        const errorLog = {
          timestamp: timestamp.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
          level: "ERROR" as const,
          message: "WebSocket connection reported an error.",
        };
        const updatedNodes = execution.nodes.map((node) =>
          node.status === "running"
            ? { ...node, status: "error" as NodeStatus }
            : node,
        );
        return {
          ...execution,
          status: execution.status === "success" ? execution.status : "failed",
          nodes: updatedNodes,
          logs: [...execution.logs, errorLog],
          endTime: execution.endTime ?? timestamp.toISOString(),
          duration:
            timestamp.getTime() - new Date(execution.startTime).getTime(),
          issues: execution.issues + 1,
        };
      }),
    );
    toast({
      title: "Workflow stream error",
      description: "The WebSocket connection reported an error.",
      variant: "destructive",
    });
    if (websocketRef.current === ws) {
      websocketRef.current.close();
      websocketRef.current = null;
    }
  };

  ws.onclose = () => {
    if (!isMountedRef.current) {
      return;
    }
    setIsRunning(false);
    if (websocketRef.current === ws) {
      websocketRef.current = null;
    }
  };
}
