import "@features/workflow/components/trace/agent-prism/theme/theme.css";

import { LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { TraceSpan } from "@evilmartians/agent-prism-types";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Button } from "@/design-system/ui/button";
import type { TraceViewerData } from "@features/workflow/components/trace/agent-prism";
import { TraceViewer } from "@features/workflow/components/trace/agent-prism";
import {
  SpanStateLoaderProvider,
  type SpanStateLoader,
} from "@features/workflow/components/trace/agent-prism/DetailsView/SpanStateContext";
import { deriveThreadStitchedViewerDataList } from "@features/workflow/pages/workflow/helpers/trace";
import type {
  TraceEntryStatus,
  TraceSpanMetadata,
} from "@features/workflow/pages/workflow/helpers/trace";

export interface TraceTabContentProps {
  error?: string;
  viewerData: TraceViewerData[];
  activeViewer?: TraceViewerData;
  /** Execution currently selected, even before its spans have loaded. */
  activeExecutionId?: string;
  /** Load status of the selected execution's trace. */
  status?: TraceEntryStatus;
  onRefresh: () => void;
  isRefreshing: boolean;
  onSelectTrace?: (traceId: string) => void;
  /** Fetch every listed trace; the stitched timeline needs them all. */
  onLoadAllTraces?: () => Promise<void>;
  loadSpanState?: SpanStateLoader;
}

const TraceViewerSkeleton = () => (
  <div
    role="status"
    aria-live="polite"
    data-testid="trace-loading-state"
    className="flex min-h-0 flex-1 gap-4 rounded-lg border border-border bg-background p-4"
  >
    <span className="sr-only">Loading traces…</span>
    <div className="hidden w-1/5 flex-col gap-3 lg:flex">
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className="h-14 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
    <div className="flex flex-1 flex-col gap-3">
      <div className="h-5 w-1/3 animate-pulse rounded bg-muted" />
      {[90, 75, 75, 60, 75, 85].map((width, index) => (
        <div
          key={index}
          className="h-4 animate-pulse rounded bg-muted"
          style={{ width: `${width}%` }}
        />
      ))}
    </div>
  </div>
);

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
  activeExecutionId,
  status,
  onRefresh,
  isRefreshing,
  onSelectTrace,
  onLoadAllTraces,
  loadSpanState,
}: TraceTabContentProps) {
  const [isStitchedTimeline, setIsStitchedTimeline] = useState(false);
  const [isLoadingAllTraces, setIsLoadingAllTraces] = useState(false);
  const activeTraceId = activeViewer?.traceRecord.id ?? activeExecutionId;
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
    return deriveThreadStitchedViewerDataList(viewerData, activeTraceId);
  }, [activeTraceId, isStitchedTimeline, viewerData]);

  const displayedActiveTraceId = useMemo(() => {
    if (!isStitchedTimeline) {
      return activeTraceId;
    }

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
  }, [activeTraceId, activeViewer, displayedViewerData, isStitchedTimeline]);

  const hasData = displayedViewerData.length > 0;
  const isInitialLoad = !hasData && (status === "loading" || isRefreshing);

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
              const next = !isStitchedTimeline;
              setIsStitchedTimeline(next);
              if (next && onLoadAllTraces) {
                setIsLoadingAllTraces(true);
                void onLoadAllTraces().finally(() => {
                  setIsLoadingAllTraces(false);
                });
              }
            }}
          >
            {isLoadingAllTraces && (
              <LoaderCircle className="mr-2 size-4 animate-spin" />
            )}
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
            <RefreshCw
              className={`mr-2 size-4${isRefreshing ? " animate-spin" : ""}`}
            />
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

      {isInitialLoad && !error && <TraceViewerSkeleton />}

      {!hasData && !isInitialLoad && !error && (
        <div
          data-testid="trace-empty-state"
          className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground"
        >
          No traces recorded yet. Run the workflow to capture one.
        </div>
      )}

      {hasData && (
        <div className="min-h-0 w-full min-w-0 flex-1 overflow-hidden rounded-lg border border-border bg-background">
          <SpanStateLoaderProvider value={loadSpanState}>
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
          </SpanStateLoaderProvider>
        </div>
      )}
    </div>
  );
}
