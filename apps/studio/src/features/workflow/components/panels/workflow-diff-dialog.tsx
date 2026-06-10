import React from "react";
import { FileDown, GitBranch } from "lucide-react";

import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/design-system/ui/dialog";
import type { WorkflowDiffResult } from "@features/workflow/lib/workflow-diff";
import type { WorkflowVersionRecord } from "@features/workflow/lib/workflow-storage";

export interface WorkflowDiffContext {
  base: WorkflowVersionRecord;
  target: WorkflowVersionRecord;
  result: WorkflowDiffResult;
}

interface WorkflowDiffDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  diffContext: WorkflowDiffContext | null;
  selectedVersionRecord: WorkflowVersionRecord | null;
  compareVersionRecord: WorkflowVersionRecord | null;
}

const summaryBadgeClass = {
  added: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  removed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  modified: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
};

const formatValue = (value: unknown) =>
  typeof value === "string" ||
  typeof value === "number" ||
  typeof value === "boolean"
    ? value.toString()
    : JSON.stringify(value, null, 2);

const DiffSummary = ({
  diffContext,
}: {
  diffContext: WorkflowDiffContext | null;
}) => {
  const summary = diffContext?.result.summary ?? {
    added: 0,
    removed: 0,
    modified: 0,
  };

  return (
    <div className="flex items-center gap-2">
      <Badge className={summaryBadgeClass.added}>Added {summary.added}</Badge>
      <Badge className={summaryBadgeClass.removed}>
        Removed {summary.removed}
      </Badge>
      <Badge className={summaryBadgeClass.modified}>
        Modified {summary.modified}
      </Badge>
    </div>
  );
};

const Differences = ({
  diffContext,
}: {
  diffContext: WorkflowDiffContext | null;
}) => {
  if (!diffContext || diffContext.result.entries.length === 0) {
    return (
      <div className="text-sm text-muted-foreground">
        No differences detected between the selected versions.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {diffContext.result.entries.map((entry) => {
        const badgeClass =
          entry.type === "added"
            ? summaryBadgeClass.added
            : entry.type === "removed"
              ? summaryBadgeClass.removed
              : summaryBadgeClass.modified;

        return (
          <div key={entry.id} className="border rounded-md overflow-hidden">
            <div className="bg-muted p-2 border-b border-border flex items-center justify-between">
              <div>
                <span className="font-medium">{entry.name}</span>
                <span className="ml-2 text-xs uppercase text-muted-foreground">
                  {entry.entity}
                </span>
              </div>
              <Badge className={badgeClass}>{entry.type}</Badge>
            </div>
            <div className="p-3 space-y-2 text-sm">
              {entry.detail && (
                <p className="text-muted-foreground">{entry.detail}</p>
              )}
              {entry.type !== "added" && entry.before !== undefined && (
                <pre className="bg-red-50 dark:bg-red-900/20 rounded-md p-2 font-mono text-xs whitespace-pre-wrap">
                  - {formatValue(entry.before)}
                </pre>
              )}
              {entry.type !== "removed" && entry.after !== undefined && (
                <pre className="bg-green-50 dark:bg-green-900/20 rounded-md p-2 font-mono text-xs whitespace-pre-wrap">
                  + {formatValue(entry.after)}
                </pre>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default function WorkflowDiffDialog({
  open,
  onOpenChange,
  diffContext,
  selectedVersionRecord,
  compareVersionRecord,
}: WorkflowDiffDialogProps) {
  const handleExport = () => {
    if (!diffContext) {
      return;
    }

    const payload = {
      baseVersion: diffContext.base.version,
      targetVersion: diffContext.target.version,
      summary: diffContext.result.summary,
      entries: diffContext.result.entries,
    };
    const serialized = JSON.stringify(payload, null, 2);
    const blob = new Blob([serialized], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workflow-diff-${diffContext.base.version}-vs-${diffContext.target.version}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>Compare Versions</DialogTitle>
          <DialogDescription>
            {diffContext
              ? `Comparing ${diffContext.base.version} with ${diffContext.target.version}`
              : "Select two versions to compare."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" />

              <span className="font-medium">
                {diffContext?.base.version ??
                  selectedVersionRecord?.version ??
                  "Select a version"}
              </span>
              <span className="text-muted-foreground">→</span>
              <GitBranch className="h-4 w-4" />

              <span className="font-medium">
                {diffContext?.target.version ??
                  compareVersionRecord?.version ??
                  "Select a version"}
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleExport}
              disabled={!diffContext}
            >
              <FileDown className="h-4 w-4 mr-2" />
              Export Diff
            </Button>
          </div>

          <div className="border rounded-md overflow-hidden">
            <div className="bg-muted p-2 border-b border-border flex items-center justify-between">
              <div className="text-sm font-medium">Changes</div>
              <DiffSummary diffContext={diffContext} />
            </div>

            <div className="p-4 bg-muted/20">
              <Differences diffContext={diffContext} />
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
