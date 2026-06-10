import "@features/workflow/components/trace/agent-prism/theme/theme.css";

import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { TraceSpan } from "@evilmartians/agent-prism-types";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Button } from "@/design-system/ui/button";
import type { TraceViewerData } from "@features/workflow/components/trace/agent-prism";
import { TraceViewer } from "@features/workflow/components/trace/agent-prism";
import { deriveThreadStitchedViewerDataList } from "@features/workflow/pages/workflow-canvas/helpers/trace";
import type { TraceSpanMetadata } from "@features/workflow/pages/workflow-canvas/helpers/trace";

export interface TraceTabContentProps {
  error?: string;
  viewerData: TraceViewerData[];
  activeViewer?: TraceViewerData;
  onRefresh: () => void;
  isRefreshing: boolean;
  onSelectTrace?: (traceId: string) => void;
}

const renderArtifactActions = (span: TraceSpan) => {
  const metadata = span.metadata as
    | (TraceSpanMetadata & {
        artifacts?: Array<{ id: string; downloadUrl?: string }>;
      })
    | undefined;
  const artifacts = metadata?.artifacts ?? [];
  if (artifacts.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      {artifacts.map((artifact) => (
        <Button
          key={artifact.id}
          size="sm"
          variant="outline"
          onClick={() => {
            if (artifact.downloadUrl) {
              window.open(
                artifact.downloadUrl,
                "_blank",
                "noopener,noreferrer",
              );
            }
          }}
        >
          Download {artifact.id}
        </Button>
      ))}
    </div>
  );
};

export function TraceTabContent({
  error,
  viewerData,
  activeViewer,
  onRefresh,
  isRefreshing,
  onSelectTrace,
}: TraceTabContentProps) {
  const [isStitchedTimeline, setIsStitchedTimeline] = useState(false);
  const canStitchByThread = useMemo(
    () => viewerData.some((trace) => Boolean(trace.threadId)),
    [viewerData],
  );

  useEffect(() => {
    if (!canStitchByThread && isStitchedTimeline) {
      setIsStitchedTimeline(false);
    }
  }, [canStitchByThread, isStitchedTimeline]);

  const displayedViewerData = useMemo(() => {
    if (!isStitchedTimeline) {
      return viewerData;
    }
    return deriveThreadStitchedViewerDataList(
      viewerData,
      activeViewer?.traceRecord.id,
    );
  }, [activeViewer?.traceRecord.id, isStitchedTimeline, viewerData]);

  const displayedActiveTraceId = useMemo(() => {
    if (!isStitchedTimeline) {
      return activeViewer?.traceRecord.id;
    }

    const activeTraceId = activeViewer?.traceRecord.id;
    if (
      activeTraceId &&
      displayedViewerData.some(
        (trace) => trace.traceRecord.id === activeTraceId,
      )
    ) {
      return activeTraceId;
    }

    const activeThreadId = activeViewer?.threadId;
    if (activeThreadId) {
      const stitchedGroup = displayedViewerData.find(
        (trace) => trace.threadId === activeThreadId,
      );
      if (stitchedGroup) {
        return stitchedGroup.traceRecord.id;
      }
    }

    return displayedViewerData[0]?.traceRecord.id;
  }, [activeViewer, displayedViewerData, isStitchedTimeline]);

  const hasData = displayedViewerData.length > 0;

  return (
    <div className="flex h-full w-full min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Execution trace</h2>
          <p className="text-sm text-muted-foreground">
            Inspect span hierarchy, metrics, and artifacts for the selected run.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={isStitchedTimeline ? "default" : "outline"}
            disabled={!canStitchByThread}
            onClick={() => {
              setIsStitchedTimeline((current) => !current);
            }}
          >
            {isStitchedTimeline ? "Stitched: On" : "Stitched: Off"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={isRefreshing}
            onClick={() => {
              void onRefresh();
            }}
          >
            <RefreshCw className="mr-2 size-4" />
            {isRefreshing ? "Refreshing..." : "Refresh"}
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Unable to load trace</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!hasData && !error && (
        <div
          data-testid="trace-empty-state"
          className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground"
        >
          No trace spans recorded yet for this execution.
        </div>
      )}

      {hasData && (
        <div className="min-h-0 w-full min-w-0 flex-1 overflow-hidden rounded-lg border border-border bg-background">
          <TraceViewer
            data={displayedViewerData}
            activeTraceId={displayedActiveTraceId}
            onTraceSelect={(trace) => {
              onSelectTrace?.(trace.id);
            }}
            detailsViewProps={{
              headerActions: renderArtifactActions,
            }}
          />
        </div>
      )}
    </div>
  );
}
