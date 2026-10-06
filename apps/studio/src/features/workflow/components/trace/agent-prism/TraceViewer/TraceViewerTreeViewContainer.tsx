import type { TraceSpan } from "@evilmartians/agent-prism-types";

import type { SpanCardViewOptions } from "../SpanCard/SpanCard";

import { Badge } from "../Badge";
import { TraceListItemHeader } from "../TraceList/TraceListItemHeader";
import { TreeView } from "../TreeView";
import { type TraceRecordWithDisplayData } from "./TraceViewer";
import { isTraceLoadPending } from "./traceLoadStatus";
import { TraceViewerSearchAndControls } from "./TraceViewerSearchAndControls";

const SKELETON_ROW_INDENTS = ["pl-3", "pl-8", "pl-8", "pl-12", "pl-8", "pl-3"];

export const TraceSpansSkeleton = () => (
  <div
    role="status"
    aria-live="polite"
    data-testid="trace-spans-loading"
    className="flex flex-col gap-3 p-3"
  >
    <span className="sr-only">Loading trace spans…</span>
    {SKELETON_ROW_INDENTS.map((indent, index) => (
      <div key={index} className={`flex items-center gap-2 ${indent}`}>
        <div className="bg-agentprism-muted size-4 shrink-0 animate-pulse rounded" />
        <div
          className="bg-agentprism-muted h-3 animate-pulse rounded"
          style={{ width: `${55 - index * 5}%` }}
        />
        <div className="bg-agentprism-muted ml-auto h-3 w-12 shrink-0 animate-pulse rounded" />
      </div>
    ))}
  </div>
);

const EmptySpansMessage = ({
  selectedTrace,
  isSearching,
}: {
  selectedTrace?: TraceRecordWithDisplayData;
  isSearching: boolean;
}) => {
  if (!isSearching && isTraceLoadPending(selectedTrace?.loadStatus)) {
    return <TraceSpansSkeleton />;
  }
  let message = "No spans recorded yet for this execution.";
  if (isSearching) {
    message = "No spans found";
  } else if (selectedTrace?.loadStatus === "error") {
    message = "Couldn't load this trace. Use Refresh to try again.";
  }
  return (
    <div className="text-agentprism-muted-foreground p-3 text-center">
      {message}
    </div>
  );
};

export const TraceViewerTreeViewContainer = ({
  searchValue,
  setSearchValue,
  handleExpandAll,
  handleCollapseAll,
  filteredSpans,
  selectedSpan,
  setSelectedSpan,
  expandedSpansIds,
  setExpandedSpansIds,
  spanCardViewOptions,
  selectedTrace,
  showHeader = true,
}: {
  searchValue: string;
  setSearchValue: (value: string) => void;
  handleExpandAll: () => void;
  handleCollapseAll: () => void;
  filteredSpans: TraceSpan[];
  selectedSpan: TraceSpan | undefined;
  setSelectedSpan: (span: TraceSpan | undefined) => void;
  expandedSpansIds: string[];
  setExpandedSpansIds: (ids: string[]) => void;
  spanCardViewOptions?: SpanCardViewOptions;
  selectedTrace?: TraceRecordWithDisplayData;
  showHeader?: boolean;
}) => (
  <>
    {showHeader && selectedTrace && (
      <div className="flex shrink-0 gap-2 px-4">
        <TraceListItemHeader trace={selectedTrace} />

        <div className="flex flex-wrap items-center gap-2">
          {selectedTrace.badges?.map((badge, index) => (
            <Badge key={index} size="4" label={badge.label} />
          ))}
        </div>
      </div>
    )}

    <div className="bg-agentprism-background flex min-h-0 flex-1 flex-col overflow-hidden rounded-md">
      <TraceViewerSearchAndControls
        searchValue={searchValue}
        setSearchValue={setSearchValue}
        handleExpandAll={handleExpandAll}
        handleCollapseAll={handleCollapseAll}
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {filteredSpans.length === 0 ? (
          <EmptySpansMessage
            selectedTrace={selectedTrace}
            isSearching={searchValue.trim().length > 0}
          />
        ) : (
          <TreeView
            spans={filteredSpans}
            onSpanSelect={setSelectedSpan}
            selectedSpan={selectedSpan}
            expandedSpansIds={expandedSpansIds}
            onExpandSpansIdsChange={setExpandedSpansIds}
            spanCardViewOptions={spanCardViewOptions}
          />
        )}
      </div>
    </div>
  </>
);
