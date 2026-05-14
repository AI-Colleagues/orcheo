import { useEffect } from "react";
import { WorkflowCanvasLayout } from "@features/workflow/pages/workflow-canvas/components/workflow-canvas-layout";
import { useWorkflowCanvasController } from "@features/workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-controller";
import { usePageContext } from "@/hooks/use-page-context";

import type {
  CanvasEdge,
  CanvasNode,
} from "@features/workflow/pages/workflow-canvas/helpers/types";

interface WorkflowCanvasProps {
  initialNodes?: CanvasNode[];
  initialEdges?: CanvasEdge[];
  workflowId?: string;
}

export default function WorkflowCanvas({
  initialNodes = [],
  initialEdges = [],
  workflowId,
}: WorkflowCanvasProps) {
  const { layoutProps } = useWorkflowCanvasController(
    initialNodes,
    initialEdges,
    workflowId,
  );

  const { setPageContext } = usePageContext();
  const activeWorkflowId = layoutProps.workflowProps.workflowId ?? null;
  const workflowName =
    layoutProps.topNavigationProps.currentWorkflow.name ?? null;
  const activeTab = layoutProps.tabsProps.activeTab;

  useEffect(() => {
    setPageContext({
      page: "canvas",
      workflowId: activeWorkflowId,
      workflowName,
      activeTab,
    });
  }, [setPageContext, activeWorkflowId, workflowName, activeTab]);

  return <WorkflowCanvasLayout {...layoutProps} />;
}
