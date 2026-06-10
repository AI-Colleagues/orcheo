import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TraceSpan } from "@evilmartians/agent-prism-types";

import type {
  TraceRecordWithDisplayData,
  TraceViewerData,
} from "./TraceViewer";

interface UseTraceSelectionOptions {
  data: TraceViewerData[];
  initialTraceData?: TraceViewerData;
}

interface SelectTraceOptions {
  source?: "user" | "external";
}

export interface TraceSelectionState {
  selectedTrace: TraceRecordWithDisplayData | undefined;
  selectedTraceId?: string;
  selectedTraceSpans: TraceSpan[];
  hasUserSelection: boolean;
  traceChangeToken: number;
  selectTrace: (
    traceId: string | undefined,
    options?: SelectTraceOptions,
  ) => void;
  clearSelection: () => void;
}

export const useTraceSelection = ({
  data,
  initialTraceData,
}: UseTraceSelectionOptions): TraceSelectionState => {
  const [selectedTrace, setSelectedTrace] = useState<
    TraceRecordWithDisplayData | undefined
  >(
    initialTraceData
      ? {
          ...initialTraceData.traceRecord,
          badges: initialTraceData.badges,
          spanCardViewOptions: initialTraceData.spanCardViewOptions,
        }
      : undefined,
  );
  const [selectedTraceId, setSelectedTraceId] = useState<string | undefined>(
    initialTraceData?.traceRecord.id,
  );
  const [selectedTraceSpans, setSelectedTraceSpans] = useState<TraceSpan[]>(
    initialTraceData?.spans ?? [],
  );
  const [hasUserSelection, setHasUserSelection] = useState(false);
  const [traceChangeToken, setTraceChangeToken] = useState(0);
  const previousTraceIdRef = useRef<string | undefined>(
    initialTraceData?.traceRecord.id,
  );

  const registerTraceChange = useCallback(() => {
    setTraceChangeToken((current) => current + 1);
  }, []);

  const clearSelection = useCallback(() => {
    previousTraceIdRef.current = undefined;
    setHasUserSelection(true);
    setSelectedTrace(undefined);
    setSelectedTraceSpans([]);
    setSelectedTraceId(undefined);
    registerTraceChange();
  }, [registerTraceChange]);

  const selectTrace = useCallback(
    (traceId: string | undefined, options?: SelectTraceOptions) => {
      const source = options?.source ?? "user";
      setHasUserSelection(source === "user");
      setSelectedTraceId(traceId);
    },
    [],
  );

  useEffect(() => {
    if (data.length === 0) {
      setSelectedTrace(undefined);
      setSelectedTraceSpans([]);
      setSelectedTraceId(undefined);
      if (hasUserSelection) {
        setHasUserSelection(false);
      }
      if (previousTraceIdRef.current) {
        previousTraceIdRef.current = undefined;
        registerTraceChange();
      }
      return;
    }

    if (!selectedTraceId) {
      if (!hasUserSelection) {
        const firstTrace = data[0];
        if (firstTrace) {
          setSelectedTraceId(firstTrace.traceRecord.id);
        }
        return;
      }
      setSelectedTrace(undefined);
      setSelectedTraceSpans([]);
      if (previousTraceIdRef.current) {
        previousTraceIdRef.current = undefined;
        registerTraceChange();
      }
      return;
    }

    const traceData = data.find(
      (item) => item.traceRecord.id === selectedTraceId,
    );

    if (!traceData) {
      const fallback = data[0];
      if (fallback && fallback.traceRecord.id !== selectedTraceId) {
        setSelectedTraceId(fallback.traceRecord.id);
        setHasUserSelection(false);
      } else {
        setSelectedTrace(undefined);
        setSelectedTraceSpans([]);
        if (hasUserSelection) {
          setHasUserSelection(false);
        }
        if (previousTraceIdRef.current) {
          previousTraceIdRef.current = undefined;
          registerTraceChange();
        }
      }
      return;
    }

    const nextTrace: TraceRecordWithDisplayData = {
      ...traceData.traceRecord,
      badges: traceData.badges,
      spanCardViewOptions: traceData.spanCardViewOptions,
    };

    setSelectedTrace((previous) => {
      if (
        previous &&
        previous.id === nextTrace.id &&
        previous.badges === nextTrace.badges &&
        previous.spanCardViewOptions === nextTrace.spanCardViewOptions
      ) {
        return previous;
      }
      return nextTrace;
    });

    setSelectedTraceSpans((previous) =>
      previous === traceData.spans ? previous : traceData.spans,
    );

    if (previousTraceIdRef.current !== traceData.traceRecord.id) {
      previousTraceIdRef.current = traceData.traceRecord.id;
      registerTraceChange();
    }
  }, [data, hasUserSelection, registerTraceChange, selectedTraceId]);

  const state = useMemo<TraceSelectionState>(
    () => ({
      selectedTrace,
      selectedTraceId,
      selectedTraceSpans,
      hasUserSelection,
      traceChangeToken,
      selectTrace,
      clearSelection,
    }),
    [
      clearSelection,
      hasUserSelection,
      selectTrace,
      selectedTrace,
      selectedTraceId,
      selectedTraceSpans,
      traceChangeToken,
    ],
  );

  return state;
};
