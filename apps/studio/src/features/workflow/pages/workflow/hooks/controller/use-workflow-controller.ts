import { useWorkflowCore } from "./use-workflow-core";
import { useWorkflowResources } from "./use-workflow-resources";
import { useWorkflowExecutionController } from "./use-workflow-execution-controller";
import { useWorkflowLifecycle } from "./use-workflow-lifecycle";
import { buildWorkflowLayoutProps } from "./build-layout-props";

export function useWorkflowController(
  workflowId?: string,
  workflowRouteRef?: string | null,
) {
  const core = useWorkflowCore({ workflowId, workflowRouteRef });
  const resources = useWorkflowResources(core, workflowId);
  const execution = useWorkflowExecutionController(core);
  useWorkflowLifecycle(core, workflowId);

  const layoutProps = buildWorkflowLayoutProps(core, resources, execution);

  return { layoutProps };
}
