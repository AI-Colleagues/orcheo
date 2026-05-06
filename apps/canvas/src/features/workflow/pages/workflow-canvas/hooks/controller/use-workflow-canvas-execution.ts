import { useMemo, useState } from "react";
import type { Connection } from "@xyflow/react";
import { addEdge, MarkerType } from "@xyflow/react";
import { toast } from "@/hooks/use-toast";
import { getAccessToken } from "@features/auth/lib/auth-session";
import {
  buildWorkflowWebSocketProtocols,
  buildWorkflowWebSocketUrl,
  getBackendBaseUrl,
} from "@/lib/config";
import { convertPersistedEdgesToCanvas } from "@features/workflow/pages/workflow-canvas/helpers/transformers";
import {
  fetchWorkflowVersions,
  selectLatestWorkflowVersion,
} from "@features/workflow/lib/workflow-storage-api";
import { useExecutionUpdates } from "@features/workflow/pages/workflow-canvas/hooks/use-execution-updates";
import { useRunWorkflow } from "@features/workflow/pages/workflow-canvas/hooks/use-run-workflow";
import { usePauseWorkflow } from "@features/workflow/pages/workflow-canvas/hooks/use-pause-workflow";
import { useNodeCreation } from "@features/workflow/pages/workflow-canvas/hooks/use-node-creation";
import { useWorkflowKeybindings } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-keybindings";
import { useNodeInspectorHandlers } from "@features/workflow/pages/workflow-canvas/hooks/use-node-inspector-handlers";
import { useExecutionTrace } from "@features/workflow/pages/workflow-canvas/hooks/use-execution-trace";
import {
  createHandleCreateSubworkflow,
  createHandleDeleteSubworkflow,
  createHandleInsertSubworkflow,
} from "@features/workflow/pages/workflow-canvas/handlers/subworkflows";
import {
  createHandleDismissValidation,
  createHandleFixValidation,
  createRunPublishValidation,
} from "@features/workflow/pages/workflow-canvas/handlers/validation";
import type { CanvasEdge } from "@features/workflow/pages/workflow-canvas/helpers/types";
import {
  createExecutionRecord,
  markNodesAsRunning,
} from "@features/workflow/pages/workflow-canvas/hooks/execution-record";
import { setupExecutionWebSocket } from "@features/workflow/pages/workflow-canvas/hooks/workflow-runner-websocket";
import { generateRandomId } from "@features/workflow/pages/workflow-canvas/helpers/id";
import type { WorkflowCanvasCore } from "./use-workflow-canvas-core";
import type { WorkflowCanvasResources } from "./use-workflow-canvas-resources";
export interface WorkflowCanvasExecution {
  executionUpdates: ReturnType<typeof useExecutionUpdates>;
  handleRunWorkflow: () => Promise<void>;
  handleRunPersistedWorkflow: () => Promise<void>;
  handlePauseWorkflow: () => void;
  isRunPending: boolean;
  nodeCreation: ReturnType<typeof useNodeCreation>;
  inspectorHandlers: ReturnType<typeof useNodeInspectorHandlers>;
  trace: ReturnType<typeof useExecutionTrace>;
  runPublishValidation: ReturnType<typeof createRunPublishValidation>;
  handleDismissValidation: ReturnType<typeof createHandleDismissValidation>;
  handleFixValidation: ReturnType<typeof createHandleFixValidation>;
  handleCreateSubworkflow: ReturnType<typeof createHandleCreateSubworkflow>;
  handleDeleteSubworkflow: ReturnType<typeof createHandleDeleteSubworkflow>;
  handleInsertSubworkflow: ReturnType<typeof createHandleInsertSubworkflow>;
  handleConnect: (connection: Connection) => void;
  edgeHoverHandlers: {
    onEnter: (_event: React.MouseEvent<Element>, edge: CanvasEdge) => void;
    onLeave: (event: React.MouseEvent<Element>, edge: CanvasEdge) => void;
  };
}
export function useWorkflowCanvasExecution(
  core: WorkflowCanvasCore,
  resources: WorkflowCanvasResources,
): WorkflowCanvasExecution {
  const [isRunPending, setIsRunPending] = useState(false);
  const executionUpdates = useExecutionUpdates({
    resolveNodeLabel: core.nodeState.resolveNodeLabel,
    setExecutions: core.execution.setExecutions,
    setNodes: core.history.setNodes,
    setIsRunning: core.execution.setIsRunning,
    websocketRef: core.websocketRef,
    isMountedRef: core.isMountedRef,
  });

  const executionIds = useMemo(
    () => core.execution.executions.map((execution) => execution.id),
    [core.execution.executions],
  );

  const trace = useExecutionTrace({
    backendBaseUrl: core.chat.backendBaseUrl ?? getBackendBaseUrl(),
    workflowId: core.metadata.currentWorkflowId,
    activeExecutionId: core.execution.activeExecutionId,
    isMountedRef: core.isMountedRef,
    executionIds,
    enabled: core.ui.activeTab === "trace",
  });

  const handleRunWorkflow = useRunWorkflow({
    nodes: core.history.nodes,
    edges: core.history.edges,
    setNodes: core.history.setNodes,
    setExecutions: core.execution.setExecutions,
    setActiveExecutionId: core.execution.setActiveExecutionId,
    setIsRunning: core.execution.setIsRunning,
    websocketRef: core.websocketRef,
    isMountedRef: core.isMountedRef,
    currentWorkflowId: core.metadata.currentWorkflowId,
    applyExecutionUpdate: executionUpdates.applyExecutionUpdate,
    handleTraceUpdate: trace.handleTraceUpdate,
  });

  const handleRunPersistedWorkflow = async () => {
    const workflowId = core.metadata.currentWorkflowId;
    if (!workflowId) {
      toast({
        title: "Save workflow first",
        description:
          "Running requires a saved workflow with an ingested Python version.",
        variant: "destructive",
      });
      return;
    }

    setIsRunPending(true);
    try {
      const persistedVersions = await fetchWorkflowVersions(workflowId);
      const latestPersistedVersion =
        selectLatestWorkflowVersion(persistedVersions);
      if (!latestPersistedVersion) {
        throw new Error(
          "Running requires a saved workflow with an ingested Python version.",
        );
      }

      const latestVersionRecord =
        core.metadata.workflowVersions.find(
          (version) => version.id === latestPersistedVersion.id,
        ) ??
        core.metadata.workflowVersions.reduce<
          (typeof core.metadata.workflowVersions)[number] | undefined
        >(
          (latest, current) =>
            !latest || current.versionNumber > latest.versionNumber
              ? current
              : latest,
          undefined,
        );

      const graphToCanvas = latestVersionRecord?.graphToCanvas ?? {};
      const executionId = generateRandomId("run");
      const executionNodes = latestVersionRecord?.snapshot
        ? core.convertPersistedNodesToCanvas(latestVersionRecord.snapshot.nodes)
        : core.history.nodesRef.current;
      const executionEdges = latestVersionRecord?.snapshot
        ? convertPersistedEdgesToCanvas(latestVersionRecord.snapshot.edges)
        : core.history.edgesRef.current;
      const executionRecord = createExecutionRecord(
        executionId,
        executionNodes,
        executionEdges,
        graphToCanvas,
      );

      if (core.websocketRef.current) {
        core.websocketRef.current.close();
        core.websocketRef.current = null;
      }

      const token = getAccessToken();
      const websocketUrl = buildWorkflowWebSocketUrl(
        workflowId,
        getBackendBaseUrl(),
        token,
      );
      const websocketProtocols = buildWorkflowWebSocketProtocols(token);

      core.execution.setExecutions((prev) => [
        executionRecord,
        ...prev.filter((entry) => entry.id !== executionId),
      ]);
      core.execution.setActiveExecutionId(executionId);
      core.execution.setIsRunning(true);
      core.history.setNodes((prev) => markNodesAsRunning(prev));

      const ws = websocketProtocols
        ? new WebSocket(websocketUrl, websocketProtocols)
        : new WebSocket(websocketUrl);
      core.websocketRef.current = ws;

      setupExecutionWebSocket({
        ws,
        executionId,
        config: latestPersistedVersion.graph,
        graphToCanvas,
        storedRunnableConfig: latestPersistedVersion.runnable_config,
        nodes: executionNodes,
        currentWorkflowId: workflowId,
        isMountedRef: core.isMountedRef,
        applyExecutionUpdate: executionUpdates.applyExecutionUpdate,
        setIsRunning: core.execution.setIsRunning,
        setExecutions: core.execution.setExecutions,
        websocketRef: core.websocketRef,
        onTraceUpdate: trace.handleTraceUpdate,
      });
    } catch (error) {
      core.execution.setIsRunning(false);
      toast({
        title: "Failed to run workflow",
        description:
          error instanceof Error
            ? error.message
            : "Unable to start workflow run.",
        variant: "destructive",
      });
    } finally {
      setIsRunPending(false);
    }
  };

  const handlePauseWorkflow = usePauseWorkflow({
    activeExecutionId: core.execution.activeExecutionId,
    isRunning: core.execution.isRunning,
    setIsRunning: core.execution.setIsRunning,
    setNodes: core.history.setNodes,
    setExecutions: core.execution.setExecutions,
    websocketRef: core.websocketRef,
  });

  const nodeCreation = useNodeCreation({
    reactFlowWrapper: core.reactFlowWrapper,
    reactFlowInstance: core.reactFlowInstance,
    nodesRef: core.history.nodesRef,
    setNodes: core.history.setNodes,
    handleOpenChat: core.chat.handleOpenChat,
    handleUpdateStickyNoteNode: core.nodeState.handleUpdateStickyNoteNode,
  });

  useWorkflowKeybindings({
    nodesRef: core.history.nodesRef,
    deleteNodes: core.nodeState.deleteNodes,
    handleUndo: core.history.handleUndo,
    handleRedo: core.history.handleRedo,
    copySelectedNodes: resources.clipboard.copySelectedNodes,
    cutSelectedNodes: resources.clipboard.cutSelectedNodes,
    pasteNodes: resources.clipboard.pasteNodes,
  });

  const inspectorHandlers = useNodeInspectorHandlers({
    nodesRef: core.history.nodesRef,
    edgesRef: core.history.edgesRef,
    isRestoringRef: core.history.isRestoringRef,
    recordSnapshot: core.history.recordSnapshot,
    setNodesState: core.history.setNodesState,
    setEdgesState: core.history.setEdgesState,
    setValidationErrors: core.validation.setValidationErrors,
    setSearchMatches: core.search.setSearchMatches,
    setActiveChatNodeId: core.chat.setActiveChatNodeId,
    setChatTitle: core.chat.setChatTitle,
    setSelectedNodeId: core.ui.setSelectedNodeId,
    setNodeRuntimeCache: core.setNodeRuntimeCache,
    handleOpenChat: core.chat.handleOpenChat,
    activeChatNodeId: core.chat.activeChatNodeId,
  });

  const handleCreateSubworkflow = useMemo(
    () =>
      createHandleCreateSubworkflow({
        getSelectedNodes: () =>
          core.history.nodesRef.current.filter((node) => node.selected),
        setSubworkflows: core.subworkflowState.setSubworkflows,
      }),
    [core.history.nodesRef, core.subworkflowState.setSubworkflows],
  );

  const handleDeleteSubworkflow = useMemo(
    () =>
      createHandleDeleteSubworkflow({
        setSubworkflows: core.subworkflowState.setSubworkflows,
      }),
    [core.subworkflowState.setSubworkflows],
  );

  const handleInsertSubworkflow = useMemo(
    () =>
      createHandleInsertSubworkflow({
        nodesRef: core.history.nodesRef,
        setNodes: core.history.setNodes,
        setEdges: core.history.setEdges,
        setSubworkflows: core.subworkflowState.setSubworkflows,
        convertPersistedNodesToCanvas: core.convertPersistedNodesToCanvas,
        convertPersistedEdgesToCanvas,
        setSelectedNodeId: core.ui.setSelectedNodeId,
        setActiveTab: core.ui.setActiveTab,
        reactFlowInstance: core.reactFlowInstance,
      }),
    [
      core.convertPersistedNodesToCanvas,
      core.history.nodesRef,
      core.history.setEdges,
      core.history.setNodes,
      core.reactFlowInstance,
      core.subworkflowState.setSubworkflows,
      core.ui,
    ],
  );

  const runPublishValidation = useMemo(
    () =>
      createRunPublishValidation({
        getNodes: () => core.history.nodesRef.current,
        getEdges: () => core.history.edgesRef.current,
        setValidationErrors: core.validation.setValidationErrors,
        setIsValidating: core.validation.setIsValidating,
        setLastValidationRun: core.validation.setLastValidationRun,
      }),
    [core.history.edgesRef, core.history.nodesRef, core.validation],
  );

  const handleDismissValidation = useMemo(
    () =>
      createHandleDismissValidation({
        setValidationErrors: core.validation.setValidationErrors,
      }),
    [core.validation.setValidationErrors],
  );

  const handleFixValidation = useMemo(
    () =>
      createHandleFixValidation({
        getNodes: () => core.history.nodesRef.current,
        setActiveTab: core.ui.setActiveTab,
        setSelectedNodeId: core.ui.setSelectedNodeId,
        reactFlowInstance: core.reactFlowInstance,
      }),
    [core.history.nodesRef, core.reactFlowInstance, core.ui],
  );

  const handleConnect = (params: Connection) => {
    const id = `edge-${params.source}-${params.target}`;
    const exists = core.history.edges.some(
      (edge) => edge.source === params.source && edge.target === params.target,
    );
    if (!exists) {
      core.history.setEdges((eds) =>
        addEdge(
          {
            ...params,
            id,
            animated: false,
            type: "default",
            markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
            style: { stroke: "#99a1b3", strokeWidth: 2 },
          },
          eds,
        ),
      );
    }
  };

  const edgeHoverHandlers = {
    onEnter: (_: React.MouseEvent<Element>, edge: CanvasEdge) =>
      core.ui.setHoveredEdgeId(edge.id),
    onLeave: (event: React.MouseEvent<Element>, edge: CanvasEdge) => {
      const relatedTarget = event.relatedTarget as HTMLElement | null;
      if (relatedTarget?.closest?.(`[data-edge-id="${edge.id}"]`)) return;
      core.ui.setHoveredEdgeId((current) =>
        current === edge.id ? null : current,
      );
    },
  };

  return {
    executionUpdates,
    handleRunWorkflow,
    handleRunPersistedWorkflow,
    handlePauseWorkflow,
    isRunPending,
    nodeCreation,
    inspectorHandlers,
    trace,
    runPublishValidation,
    handleDismissValidation,
    handleFixValidation,
    handleCreateSubworkflow,
    handleDeleteSubworkflow,
    handleInsertSubworkflow,
    handleConnect,
    edgeHoverHandlers,
  };
}
