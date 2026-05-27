import { useWorkflowCanvasCore } from "./use-workflow-canvas-core";
import { useWorkflowCanvasResources } from "./use-workflow-canvas-resources";
import { useWorkflowCanvasExecution } from "./use-workflow-canvas-execution";
import { useWorkflowCanvasLifecycle } from "./use-workflow-canvas-lifecycle";
import { buildWorkflowLayoutProps } from "./build-layout-props";

export function useWorkflowCanvasController(
  workflowId?: string,
  workflowRouteRef?: string | null,
) {
  const core = useWorkflowCanvasCore({ workflowId, workflowRouteRef });
  const resources = useWorkflowCanvasResources(core, workflowId);
  const execution = useWorkflowCanvasExecution(core);
  useWorkflowCanvasLifecycle(core, workflowId);

  const layoutProps = buildWorkflowLayoutProps(core, resources, execution);

  return { layoutProps };
}
