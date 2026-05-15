import { useCallback } from "react";

import { toast } from "@/hooks/use-toast";
import type {
  CanvasEdge,
  CanvasNode,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

type NodeRuntimeCacheEntry = Record<string, unknown>;

type UseWorkflowNodeDeletionArgs = {
  latestNodesRef: React.MutableRefObject<CanvasNode[]>;
  isRestoringRef: React.MutableRefObject<boolean>;
  setNodesState: React.Dispatch<React.SetStateAction<CanvasNode[]>>;
  setEdgesState: React.Dispatch<React.SetStateAction<CanvasEdge[]>>;
  recordSnapshot: (options?: { force?: boolean }) => void;
  setNodeRuntimeCache: React.Dispatch<
    React.SetStateAction<Record<string, NodeRuntimeCacheEntry>>
  >;
  setSearchMatches: React.Dispatch<React.SetStateAction<string[]>>;
  setSelectedNodeId: React.Dispatch<React.SetStateAction<string | null>>;
  setActiveChatNodeId: React.Dispatch<React.SetStateAction<string | null>>;
  setIsChatOpen: React.Dispatch<React.SetStateAction<boolean>>;
  activeChatNodeId: string | null;
};

export const useWorkflowNodeDeletion = ({
  latestNodesRef,
  isRestoringRef,
  setNodesState,
  setEdgesState,
  recordSnapshot,
  setNodeRuntimeCache,
  setSearchMatches,
  setSelectedNodeId,
  setActiveChatNodeId,
  setIsChatOpen,
  activeChatNodeId,
}: UseWorkflowNodeDeletionArgs) => {
  const resolveNodeLabel = useCallback(
    (canvasNodeId: string): string => {
      const node = latestNodesRef.current.find(
        (item) => item.id === canvasNodeId,
      );
      const label =
        typeof node?.data?.label === "string" && node.data.label.trim()
          ? node.data.label.trim()
          : null;
      return label ?? canvasNodeId;
    },
    [latestNodesRef],
  );

  const deleteNodes = useCallback(
    (nodeIds: string[], options?: { suppressToast?: boolean }) => {
      const uniqueIds = Array.from(new Set(nodeIds)).filter(Boolean);
      if (uniqueIds.length === 0) {
        return;
      }

      const labels = uniqueIds.map((id) => resolveNodeLabel(id));

      setNodeRuntimeCache((current) => {
        if (Object.keys(current).length === 0) {
          return current;
        }

        let modified = false;
        const next = { ...current };
        for (const id of uniqueIds) {
          if (id in next) {
            delete next[id];
            modified = true;
          }
        }

        return modified ? next : current;
      });

      isRestoringRef.current = true;
      recordSnapshot({ force: true });
      try {
        setNodesState((current) =>
          current.filter((node) => !uniqueIds.includes(node.id)),
        );
        setEdgesState((current) =>
          current.filter(
            (edge) =>
              !uniqueIds.includes(edge.source) &&
              !uniqueIds.includes(edge.target),
          ),
        );
      } catch (error) {
        isRestoringRef.current = false;
        throw error;
      }

      setSearchMatches((matches) =>
        matches.filter((match) => !uniqueIds.includes(match)),
      );

      setSelectedNodeId((current) =>
        current && uniqueIds.includes(current) ? null : current,
      );

      if (activeChatNodeId && uniqueIds.includes(activeChatNodeId)) {
        setActiveChatNodeId(null);
        setIsChatOpen(false);
      }

      if (!options?.suppressToast) {
        toast({
          title: uniqueIds.length === 1 ? "Node deleted" : "Nodes deleted",
          description:
            uniqueIds.length === 1
              ? `Removed ${labels[0]}.`
              : `Removed ${uniqueIds.length} nodes.`,
        });
      }
    },
    [
      activeChatNodeId,
      isRestoringRef,
      recordSnapshot,
      resolveNodeLabel,
      setActiveChatNodeId,
      setEdgesState,
      setIsChatOpen,
      setNodeRuntimeCache,
      setNodesState,
      setSearchMatches,
      setSelectedNodeId,
    ],
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      deleteNodes([nodeId]);
    },
    [deleteNodes],
  );

  return {
    resolveNodeLabel,
    deleteNodes,
    handleDeleteNode,
  };
};
