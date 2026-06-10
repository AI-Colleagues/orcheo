import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Skeleton } from "@/design-system/ui/skeleton";
import { WorkflowLayout } from "@features/workflow/pages/workflow/components/workflow-layout";
import { useWorkflowController } from "@features/workflow/pages/workflow/hooks/controller/use-workflow-controller";
import {
  getWorkflowById,
  listWorkflows,
} from "@features/workflow/lib/workflow-storage";
import { usePageContext } from "@/hooks/use-page-context";

interface WorkflowPageProps {
  workflowId?: string;
}

export default function WorkflowPage({ workflowId }: WorkflowPageProps) {
  const [resolvedWorkflowId, setResolvedWorkflowId] = useState<
    string | undefined
  >(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const resolveWorkflow = async () => {
      if (!workflowId) {
        setResolvedWorkflowId(undefined);
        setLoadError(null);
        return;
      }

      setLoadError(null);

      try {
        const directWorkflow = await getWorkflowById(workflowId);
        if (cancelled) {
          return;
        }
        if (directWorkflow) {
          setResolvedWorkflowId(directWorkflow.id);
          return;
        }

        const workflows = await listWorkflows({ forceRefresh: true });
        if (cancelled) {
          return;
        }

        const matchedWorkflow = workflows.find(
          (workflow) =>
            workflow.id === workflowId || workflow.handle === workflowId,
        );
        if (matchedWorkflow) {
          setResolvedWorkflowId(matchedWorkflow.id);
          return;
        }

        setResolvedWorkflowId(undefined);
        setLoadError("Workflow not found.");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setResolvedWorkflowId(undefined);
        setLoadError(
          error instanceof Error ? error.message : "Unable to load workflow.",
        );
      }
    };

    void resolveWorkflow();

    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const { layoutProps } = useWorkflowController(
    resolvedWorkflowId,
    workflowId ?? null,
  );

  const { setPageContext } = usePageContext();
  const activeWorkflowId = layoutProps.workflowProps.workflowId ?? null;
  const workflowName =
    layoutProps.topNavigationProps.currentWorkflow.name ?? null;
  const activeTab = layoutProps.tabsProps.activeTab;

  useEffect(() => {
    setPageContext({
      page: "workflow",
      workflowId: activeWorkflowId,
      workflowName,
      activeTab,
    });
  }, [setPageContext, activeWorkflowId, workflowName, activeTab]);

  if (workflowId && !resolvedWorkflowId) {
    if (loadError) {
      return (
        <div className="flex min-h-[60vh] items-center justify-center p-6">
          <Alert className="max-w-xl">
            <AlertTitle>Workflow unavailable</AlertTitle>
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        </div>
      );
    }

    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-10 w-64 rounded-full" />
        <Skeleton className="h-[72vh] w-full rounded-3xl" />
      </div>
    );
  }

  return <WorkflowLayout {...layoutProps} />;
}
