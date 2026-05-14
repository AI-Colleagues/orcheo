import type {
  CanvasEdge,
  CanvasNode,
} from "@features/workflow/pages/workflow-canvas/helpers/types";
import { useWorkflowCanvasCore } from "./use-workflow-canvas-core";
import { useWorkflowCanvasResources } from "./use-workflow-canvas-resources";
import { useWorkflowCanvasExecution } from "./use-workflow-canvas-execution";
import { useWorkflowCanvasLifecycle } from "./use-workflow-canvas-lifecycle";
import { buildWorkflowLayoutProps } from "./build-layout-props";

export function useWorkflowCanvasController(
  initialNodes: CanvasNode[],
  initialEdges: CanvasEdge[],
  workflowId?: string,
) {
  const core = useWorkflowCanvasCore({
    initialNodes,
    initialEdges,
    workflowId,
  });
  const resources = useWorkflowCanvasResources(core, workflowId ?? undefined);
  const execution = useWorkflowCanvasExecution(core, resources);
  useWorkflowCanvasLifecycle(core, workflowId ?? undefined);

  const layoutProps = buildWorkflowLayoutProps(core, resources, execution);

  return { layoutProps };
}
