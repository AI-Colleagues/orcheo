import { useWorkflowNodeDeletion } from "./use-workflow-node-deletion";
import { useWorkflowStickyNotes } from "./use-workflow-sticky-notes";
import { useDecoratedNodes } from "./use-decorated-nodes";
import type {
  CanvasEdge,
  CanvasNode,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

type NodeRuntimeCacheEntry = Record<string, unknown>;

type UseWorkflowNodeStateArgs = {
  nodes: CanvasNode[];
  searchMatches: string[];
  searchMatchSet: Set<string>;
  isSearchOpen: boolean;
  currentSearchIndex: number;
  latestNodesRef: React.MutableRefObject<CanvasNode[]>;
  isRestoringRef: React.MutableRefObject<boolean>;
  setNodes: React.Dispatch<React.SetStateAction<CanvasNode[]>>;
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

export const useWorkflowNodeState = ({
  nodes,
  searchMatches,
  searchMatchSet,
  isSearchOpen,
  currentSearchIndex,
  latestNodesRef,
  isRestoringRef,
  setNodes,
  setNodesState,
  setEdgesState,
  recordSnapshot,
  setNodeRuntimeCache,
  setSearchMatches,
  setSelectedNodeId,
  setActiveChatNodeId,
  setIsChatOpen,
  activeChatNodeId,
}: UseWorkflowNodeStateArgs) => {
  const { resolveNodeLabel, deleteNodes, handleDeleteNode } =
    useWorkflowNodeDeletion({
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
    });

  const { handleUpdateStickyNoteNode } = useWorkflowStickyNotes({
    setNodes,
  });

  const decoratedNodes = useDecoratedNodes({
    nodes,
    isSearchOpen,
    searchMatchSet,
    searchMatches,
    currentSearchIndex,
    handleDeleteNode,
    handleUpdateStickyNoteNode,
  });

  return {
    decoratedNodes,
    resolveNodeLabel,
    deleteNodes,
    handleDeleteNode,
    handleUpdateStickyNoteNode,
  };
};
