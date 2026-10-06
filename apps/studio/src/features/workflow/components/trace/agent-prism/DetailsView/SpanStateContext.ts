import { createContext, useContext, useEffect, useState } from "react";

import type { TraceSpanStateResponse } from "@features/workflow/pages/workflow/helpers/trace";

export type SpanStateLoader = (
  executionId: string,
  spanId: string,
) => Promise<TraceSpanStateResponse>;

const SpanStateLoaderContext = createContext<SpanStateLoader | undefined>(
  undefined,
);

export const SpanStateLoaderProvider = SpanStateLoaderContext.Provider;

export type SpanStateResult =
  | { status: "unavailable" }
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ready"; state: TraceSpanStateResponse };

/**
 * Fetch the workflow state captured around a node span. Snapshots are not
 * shipped with the trace itself, so they load when a span is inspected.
 */
export const useSpanWorkflowState = (
  executionId: string | undefined,
  spanId: string,
  enabled: boolean,
): SpanStateResult => {
  const loadSpanState = useContext(SpanStateLoaderContext);
  const canLoad = Boolean(enabled && executionId && loadSpanState);
  const [result, setResult] = useState<SpanStateResult>(
    canLoad ? { status: "loading" } : { status: "unavailable" },
  );

  useEffect(() => {
    if (!enabled || !executionId || !loadSpanState) {
      setResult({ status: "unavailable" });
      return;
    }
    let cancelled = false;
    setResult({ status: "loading" });
    loadSpanState(executionId, spanId).then(
      (state) => {
        if (!cancelled) {
          setResult({ status: "ready", state });
        }
      },
      (error: unknown) => {
        if (!cancelled) {
          setResult({
            status: "error",
            error: error instanceof Error ? error.message : String(error),
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [enabled, executionId, loadSpanState, spanId]);

  return result;
};
