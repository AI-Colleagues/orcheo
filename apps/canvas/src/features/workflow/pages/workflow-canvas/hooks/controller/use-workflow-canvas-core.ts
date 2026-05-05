import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import type { ReactFlowInstance } from "@xyflow/react";

import { getBackendBaseUrl } from "@/lib/config";
import {
  getRuntimeCacheStorageKey,
  readRuntimeCacheFromSession,
} from "@features/workflow/pages/workflow-canvas/helpers/runtime-cache";
import type { WorkflowNode as PersistedWorkflowNode } from "@features/workflow/data/workflow-data";
import { toCanvasNodeBase } from "@features/workflow/pages/workflow-canvas/helpers/transformers";
import { useWorkflowCanvasHistory } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-canvas-history";
import { useWorkflowSearch } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-search";
import { useWorkflowNodeState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-node-state";
import { useWorkflowChat } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-chat";
import { useWorkflowMetadataState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-metadata-state";
import { useWorkflowValidationState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-validation-state";
import { useWorkflowExecutionState } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-execution-state";
import { useSubworkflowState } from "@features/workflow/pages/workflow-canvas/hooks/use-subworkflow-state";
import { useCanvasUiState } from "@features/workflow/pages/workflow-canvas/hooks/use-canvas-ui-state";
import { useRuntimeCacheSync } from "@features/workflow/pages/workflow-canvas/hooks/use-runtime-cache-sync";

import type {
  CanvasEdge,
  CanvasNode,
  NodeRuntimeCacheEntry,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

export interface WorkflowCanvasCore {
  routeWorkflowId: string | null;
  history: ReturnType<typeof useWorkflowCanvasHistory>;
  metadata: ReturnType<typeof useWorkflowMetadataState>;
  validation: ReturnType<typeof useWorkflowValidationState>;
  execution: ReturnType<typeof useWorkflowExecutionState>;
  subworkflowState: ReturnType<typeof useSubworkflowState>;
  ui: ReturnType<typeof useCanvasUiState>;
  reactFlowWrapper: React.MutableRefObject<HTMLDivElement | null>;
  reactFlowInstance: React.MutableRefObject<ReactFlowInstance<
    CanvasNode,
    CanvasEdge
  > | null>;
  websocketRef: React.MutableRefObject<WebSocket | null>;
  isMountedRef: React.MutableRefObject<boolean>;
  nodeRuntimeCache: Record<string, NodeRuntimeCacheEntry>;
  setNodeRuntimeCache: React.Dispatch<
    React.SetStateAction<Record<string, NodeRuntimeCacheEntry>>
  >;
  search: ReturnType<typeof useWorkflowSearch>;
  chat: ReturnType<typeof useWorkflowChat>;
  nodeState: ReturnType<typeof useWorkflowNodeState>;
  convertPersistedNodesToCanvas: (
    nodes: PersistedWorkflowNode[],
  ) => CanvasNode[];
  user: { id: string; name: string; avatar: string };
  ai: { id: string; name: string; avatar: string };
}

interface UseWorkflowCanvasCoreArgs {
  initialNodes: CanvasNode[];
  initialEdges: CanvasEdge[];
}

export function useWorkflowCanvasCore({
  initialNodes,
  initialEdges,
}: UseWorkflowCanvasCoreArgs): WorkflowCanvasCore {
  const { workflowId } = useParams<{ workflowId?: string }>();
  const history = useWorkflowCanvasHistory({ initialNodes, initialEdges });
  const metadata = useWorkflowMetadataState();
  const validation = useWorkflowValidationState();
  const execution = useWorkflowExecutionState();
  const subworkflowState = useSubworkflowState();
  const ui = useCanvasUiState();

  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const reactFlowInstance = useRef<ReactFlowInstance<
    CanvasNode,
    CanvasEdge
  > | null>(null);
  const websocketRef = useRef<WebSocket | null>(null);
  const isMountedRef = useRef(true);

  const runtimeCacheKey = useMemo(
    () => getRuntimeCacheStorageKey(workflowId ?? null),
    [workflowId],
  );
  const [nodeRuntimeCache, setNodeRuntimeCache] = useState<
    Record<string, NodeRuntimeCacheEntry>
  >(() => readRuntimeCacheFromSession(runtimeCacheKey));
  const previousRuntimeCacheKeyRef = useRef(runtimeCacheKey);

  useRuntimeCacheSync({
    runtimeCacheKey,
    nodeRuntimeCache,
    setNodeRuntimeCache,
    previousRuntimeCacheKeyRef,
  });

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

  const search = useWorkflowSearch({
    nodesRef: history.nodesRef,
    reactFlowInstance,
  });

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
      name: "Orcheo Canvas Assistant",
      avatar: "https://avatar.vercel.sh/orcheo-canvas",
    }),
    [],
  );

  const chat = useWorkflowChat({
    nodesRef: history.nodesRef,
    setNodes: history.setNodes,
    workflowId,
    backendBaseUrl: getBackendBaseUrl(),
    userName: user.name,
  });

  const convertPersistedNodesToCanvas = useCallback(
    (persisted: PersistedWorkflowNode[]) =>
      persisted
        .map((node) => toCanvasNodeBase(node))
        .map(chat.attachChatHandlerToNode),
    [chat.attachChatHandlerToNode],
  );

  const nodeState = useWorkflowNodeState({
    nodes: history.nodes,
    searchMatches: search.searchMatches,
    searchMatchSet: search.searchMatchSet,
    isSearchOpen: search.isSearchOpen,
    currentSearchIndex: search.currentSearchIndex,
    latestNodesRef: history.latestNodesRef,
    isRestoringRef: history.isRestoringRef,
    setNodes: history.setNodes,
    setNodesState: history.setNodesState,
    setEdgesState: history.setEdgesState,
    recordSnapshot: history.recordSnapshot,
    setNodeRuntimeCache,
    setValidationErrors: validation.setValidationErrors,
    setSearchMatches: search.setSearchMatches,
    setSelectedNodeId: ui.setSelectedNodeId,
    setActiveChatNodeId: chat.setActiveChatNodeId,
    setIsChatOpen: chat.setIsChatOpen,
    activeChatNodeId: chat.activeChatNodeId,
  });

  return {
    routeWorkflowId: workflowId ?? null,
    history,
    metadata,
    validation,
    execution,
    subworkflowState,
    ui,
    reactFlowWrapper,
    reactFlowInstance,
    websocketRef,
    isMountedRef,
    nodeRuntimeCache,
    setNodeRuntimeCache,
    search,
    chat,
    nodeState,
    convertPersistedNodesToCanvas,
    user,
    ai,
  };
}
