import type { TraceRecord, TraceSpan } from "@evilmartians/agent-prism-types";

import {
  filterSpansRecursively,
  flattenSpans,
} from "@evilmartians/agent-prism-data";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import type { DetailsViewProps } from "../DetailsView/DetailsView";
import { type BadgeProps } from "../Badge";
import { useIsMobile } from "../shared";
import { type SpanCardViewOptions } from "../SpanCard/SpanCard";
import { TraceViewerDesktopLayout } from "./TraceViewerDesktopLayout";
import { TraceViewerMobileLayout } from "./TraceViewerMobileLayout";
import { useTraceSelection } from "./useTraceSelection";

export interface TraceViewerData {
  traceRecord: TraceRecord;
  badges?: Array<BadgeProps>;
  spans: TraceSpan[];
  spanCardViewOptions?: SpanCardViewOptions;
  threadId?: string;
}

export interface TraceViewerProps {
  data: Array<TraceViewerData>;
  spanCardViewOptions?: SpanCardViewOptions;
  detailsViewProps?: Partial<DetailsViewProps>;
  activeTraceId?: string;
  onTraceSelect?: (trace: TraceRecord) => void;
}

export const TraceViewer = ({
  data,
  spanCardViewOptions,
  detailsViewProps,
  activeTraceId,
  onTraceSelect,
}: TraceViewerProps) => {
  const isMobile = useIsMobile();

  const initialTraceData = useMemo(() => {
    if (activeTraceId) {
      const activeTrace = data.find(
        (item) => item.traceRecord.id === activeTraceId,
      );
      if (activeTrace) {
        return activeTrace;
      }
    }
    return data[0];
  }, [activeTraceId, data]);

  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | undefined>();
  const [searchValue, setSearchValue] = useState("");
  const [traceListExpanded, setTraceListExpanded] = useState(true);

  const {
    selectedTrace,
    selectedTraceId,
    selectedTraceSpans,
    traceChangeToken,
    selectTrace,
    clearSelection,
  } = useTraceSelection({
    data,
    initialTraceData,
  });

  const traceRecords: TraceRecordWithDisplayData[] = useMemo(() => {
    return data.map((item) => ({
      ...item.traceRecord,
      badges: item.badges,
      spanCardViewOptions: item.spanCardViewOptions,
    }));
  }, [data]);

  const normalizedSearchValue = useMemo(
    () => searchValue.trim(),
    [searchValue],
  );

  const filteredSpans = useMemo(() => {
    if (!normalizedSearchValue) {
      return selectedTraceSpans;
    }
    return filterSpansRecursively(selectedTraceSpans, normalizedSearchValue);
  }, [normalizedSearchValue, selectedTraceSpans]);

  const allIds = useMemo(() => {
    return flattenSpans(selectedTraceSpans).map((span) => span.id);
  }, [selectedTraceSpans]);

  const [expandedSpansIds, setExpandedSpansIds] = useState<string[]>(allIds);

  useEffect(() => {
    setExpandedSpansIds(allIds);
  }, [allIds]);

  useEffect(() => {
    if (!activeTraceId) {
      return;
    }

    const hasActiveTrace = data.some(
      (item) => item.traceRecord.id === activeTraceId,
    );

    if (hasActiveTrace && activeTraceId !== selectedTraceId) {
      selectTrace(activeTraceId, { source: "external" });
    }
  }, [activeTraceId, data, selectTrace, selectedTraceId]);

  useEffect(() => {
    if (traceChangeToken === 0) {
      return;
    }
    setSelectedSpan(undefined);
    setExpandedSpansIds([]);
  }, [traceChangeToken]);

  useEffect(() => {
    if (!isMobile && selectedTraceSpans.length > 0 && !selectedSpan) {
      setSelectedSpan(selectedTraceSpans[0]);
    }
  }, [selectedTraceSpans, isMobile, selectedSpan]);

  const handleExpandAll = useCallback(() => {
    setExpandedSpansIds(allIds);
  }, [allIds]);

  const handleCollapseAll = useCallback(() => {
    setExpandedSpansIds([]);
  }, []);

  const handleTraceSelect = useCallback(
    (trace: TraceRecord) => {
      selectTrace(trace.id, { source: "user" });
      onTraceSelect?.(trace);
    },
    [onTraceSelect, selectTrace],
  );

  const handleClearTraceSelection = useCallback(() => {
    clearSelection();
    setSelectedSpan(undefined);
    setExpandedSpansIds([]);
  }, [clearSelection]);

  const props: TraceViewerLayoutProps = {
    traceRecords,
    traceListExpanded,
    setTraceListExpanded,
    selectedTrace,
    selectedTraceId: selectedTraceId,
    selectedSpan,
    setSelectedSpan,
    searchValue,
    setSearchValue,
    filteredSpans,
    expandedSpansIds,
    setExpandedSpansIds,
    handleExpandAll,
    handleCollapseAll,
    handleTraceSelect,
    spanCardViewOptions:
      spanCardViewOptions || selectedTrace?.spanCardViewOptions,
    onClearTraceSelection: handleClearTraceSelection,
    detailsViewProps,
  };

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      <div className="hidden min-h-0 w-full min-w-0 flex-1 lg:block">
        <TraceViewerDesktopLayout {...props} />
      </div>
      <div className="min-h-0 w-full min-w-0 flex-1 lg:hidden">
        <TraceViewerMobileLayout {...props} />
      </div>
    </div>
  );
};

export interface TraceRecordWithDisplayData extends TraceRecord {
  spanCardViewOptions?: SpanCardViewOptions;
  badges?: BadgeProps[];
}

export interface TraceViewerLayoutProps {
  traceRecords: TraceRecordWithDisplayData[];
  traceListExpanded: boolean;
  setTraceListExpanded: (expanded: boolean) => void;
  selectedTrace: TraceRecordWithDisplayData | undefined;
  selectedTraceId?: string;
  selectedSpan: TraceSpan | undefined;
  setSelectedSpan: (span: TraceSpan | undefined) => void;
  searchValue: string;
  setSearchValue: (value: string) => void;
  filteredSpans: TraceSpan[];
  expandedSpansIds: string[];
  setExpandedSpansIds: (ids: string[]) => void;
  handleExpandAll: () => void;
  handleCollapseAll: () => void;
  handleTraceSelect: (trace: TraceRecord) => void;
  spanCardViewOptions?: SpanCardViewOptions;
  onClearTraceSelection: () => void;
  detailsViewProps?: Partial<DetailsViewProps>;
}
