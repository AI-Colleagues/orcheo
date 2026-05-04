import type React from "react";
import { Button } from "@/design-system/ui/button";
import { Badge } from "@/design-system/ui/badge";
import {
  Copy,
  ExternalLink,
  Maximize2,
  Minimize2,
  Trash,
  Wrench,
} from "lucide-react";
import { cn } from "@/lib/utils";
import WorkflowFlow from "@features/workflow/components/canvas/workflow-flow";
import { Node } from "@xyflow/react";
import {
  getReactFlowEdges,
  getReactFlowNodes,
  getStatusBadgeClass,
} from "./workflow-execution-history.utils";
import type { WorkflowExecution } from "./workflow-execution-history.types";

interface WorkflowExecutionDetailsProps {
  execution: WorkflowExecution | null;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  onNodeSelect: (nodeId: string) => void;
  onCopyToEditor?: (execution: WorkflowExecution) => void;
  onDelete?: (execution: WorkflowExecution) => void;
}

export const WorkflowExecutionDetails = ({
  execution,
  isFullscreen,
  onToggleFullscreen,
  onNodeSelect,
  onCopyToEditor,
  onDelete,
}: WorkflowExecutionDetailsProps) => {
  if (!execution) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        Select an execution to view details
      </div>
    );
  }

  const latestRemediation = execution.remediations?.[0] ?? null;

  return (
    <>
      <div className="flex items-center justify-between border-b border-border p-2">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold">
            <Badge className={cn(getStatusBadgeClass(execution.status))}>
              {execution.status.charAt(0).toUpperCase() +
                execution.status.slice(1)}
            </Badge>
            Run #{execution.runId}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onCopyToEditor?.(execution)}
            title="Copy to editor"
          >
            <Copy className="mr-2 h-4 w-4" />
            Copy to editor
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={() => onDelete?.(execution)}
            title="Delete execution"
          >
            <Trash className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {latestRemediation && (
        <div className="border-b border-border px-3 py-2">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border bg-muted/20 p-3">
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Wrench className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">
                  Orcheo Vibe remediation
                </span>
                <Badge variant="outline">{latestRemediation.status}</Badge>
                {latestRemediation.classification && (
                  <Badge variant="secondary">
                    {latestRemediation.classification}
                  </Badge>
                )}
              </div>
              {latestRemediation.developer_note && (
                <p className="max-w-3xl text-sm text-muted-foreground">
                  {latestRemediation.developer_note}
                </p>
              )}
              {latestRemediation.last_error && (
                <p className="text-sm text-destructive">
                  {latestRemediation.last_error}
                </p>
              )}
            </div>
            {latestRemediation.created_version_id && (
              <Button variant="outline" size="sm" asChild>
                <a href={`/workflow-canvas/${latestRemediation.workflow_id}`}>
                  <ExternalLink className="mr-2 h-4 w-4" />
                  Version {latestRemediation.created_version_id.slice(0, 8)}
                </a>
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-1 flex-col overflow-hidden p-2">
        <div
          className={cn(
            "relative flex-1 rounded-lg border border-border bg-muted/20",
            isFullscreen && "fixed inset-0 z-50 bg-background p-4",
          )}
        >
          <WorkflowFlow
            nodes={getReactFlowNodes(execution)}
            edges={getReactFlowEdges(execution)}
            fitView
            editable={false}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={true}
            zoomOnDoubleClick={false}
            showMiniMap={true}
            onNodeDoubleClick={(_event: React.MouseEvent, node: Node) => {
              if (node.type === "startEnd") {
                return;
              }
              onNodeSelect(node.id);
            }}
          >
            <div className="absolute right-4 top-4 z-10">
              <Button
                variant="outline"
                size="icon"
                onClick={onToggleFullscreen}
                title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
              >
                {isFullscreen ? (
                  <Minimize2 className="h-4 w-4" />
                ) : (
                  <Maximize2 className="h-4 w-4" />
                )}
              </Button>
            </div>
          </WorkflowFlow>
        </div>
      </div>
    </>
  );
};

export default WorkflowExecutionDetails;
