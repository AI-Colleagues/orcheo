import { useCallback } from "react";
import type { MutableRefObject } from "react";
import { toast } from "@/hooks/use-toast";

import {
  createIdentityAllocator,
  sanitizeLabel,
} from "@features/workflow/pages/workflow-canvas/helpers/node-identity";
import type {
  CanvasEdge,
  CanvasNode,
  NodeData,
  NodeRuntimeCacheEntry,
  NodeStatus,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

interface UseNodeInspectorHandlersParams {
  nodesRef: MutableRefObject<CanvasNode[]>;
  edgesRef: MutableRefObject<CanvasEdge[]>;
  isRestoringRef: MutableRefObject<boolean>;
  recordSnapshot: (options?: { force?: boolean }) => void;
  setNodesState: (nodes: CanvasNode[]) => void;
  setEdgesState: (edges: CanvasEdge[]) => void;
  setSearchMatches: React.Dispatch<React.SetStateAction<string[]>>;
  setActiveChatNodeId: React.Dispatch<React.SetStateAction<string | null>>;
  setChatTitle: React.Dispatch<React.SetStateAction<string>>;
  setSelectedNodeId: React.Dispatch<React.SetStateAction<string | null>>;
  setNodeRuntimeCache: React.Dispatch<
    React.SetStateAction<Record<string, NodeRuntimeCacheEntry>>
  >;
  handleOpenChat: (nodeId: string) => void;
  activeChatNodeId: string | null;
}

export function useNodeInspectorHandlers({
  nodesRef,
  edgesRef,
  isRestoringRef,
  recordSnapshot,
  setNodesState,
  setEdgesState,
  setSearchMatches,
  setActiveChatNodeId,
  setChatTitle,
  setSelectedNodeId,
  setNodeRuntimeCache,
  handleOpenChat,
  activeChatNodeId,
}: UseNodeInspectorHandlersParams) {
  const handleCloseNodeInspector = useCallback(() => {
    setSelectedNodeId(null);
  }, [setSelectedNodeId]);

  const handleCacheNodeRuntime = useCallback(
    (nodeId: string, runtime: NodeRuntimeCacheEntry) => {
      setNodeRuntimeCache((current) => ({ ...current, [nodeId]: runtime }));
    },
    [setNodeRuntimeCache],
  );

  const handleNodeUpdate = useCallback(
    (nodeId: string, data: Partial<NodeData>) => {
      const currentNodes = nodesRef.current;
      const currentEdges = edgesRef.current;

      const targetNode = currentNodes.find((node) => node.id === nodeId);
      if (!targetNode) {
        return;
      }

      const desiredLabelInput =
        data.label !== undefined
          ? data.label
          : (targetNode.data?.label as string | undefined);
      const desiredLabel = sanitizeLabel(desiredLabelInput);
      const allocateIdentity = createIdentityAllocator(currentNodes, {
        excludeId: nodeId,
      });
      const { id: newId, label: uniqueLabel } = allocateIdentity(desiredLabel);

      const nextStatus =
        (data.status as NodeStatus | undefined) ||
        (targetNode.data?.status as NodeStatus | undefined) ||
        ("idle" as NodeStatus);

      const nextData: NodeData = {
        ...(targetNode.data as NodeData),
        ...data,
        label: uniqueLabel,
        status: nextStatus,
      };

      if (targetNode.type === "chatTrigger") {
        nextData.onOpenChat = () => handleOpenChat(newId);
      }

      const updatedNodes = currentNodes.map((node) =>
        node.id === nodeId
          ? ({
              ...node,
              id: newId,
              data: nextData,
            } as CanvasNode)
          : node,
      );

      const updatedEdges = currentEdges.map((edge) => {
        let modified = false;
        const nextEdge = { ...edge };
        if (edge.source === nodeId) {
          nextEdge.source = newId;
          modified = true;
        }
        if (edge.target === nodeId) {
          nextEdge.target = newId;
          modified = true;
        }
        return modified ? nextEdge : edge;
      });

      isRestoringRef.current = true;
      recordSnapshot({ force: true });
      try {
        setNodesState(updatedNodes);
        setEdgesState(updatedEdges);
      } finally {
        isRestoringRef.current = false;
      }

      setSearchMatches((matches) =>
        matches.map((match) => (match === nodeId ? newId : match)),
      );

      setActiveChatNodeId((current) => (current === nodeId ? newId : current));

      setChatTitle((title) =>
        activeChatNodeId === nodeId ? uniqueLabel : title,
      );

      if (desiredLabel !== uniqueLabel) {
        toast({
          title: "Adjusted node name",
          description: `Renamed to "${uniqueLabel}" to keep names unique.`,
        });
      }

      setSelectedNodeId(null);
    },
    [
      activeChatNodeId,
      handleOpenChat,
      isRestoringRef,
      nodesRef,
      edgesRef,
      recordSnapshot,
      setNodesState,
      setEdgesState,
      setSearchMatches,
      setActiveChatNodeId,
      setChatTitle,
      setSelectedNodeId,
    ],
  );

  return {
    handleCloseNodeInspector,
    handleCacheNodeRuntime,
    handleNodeUpdate,
  };
}
