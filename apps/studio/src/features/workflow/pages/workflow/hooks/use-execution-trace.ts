import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";

import { toast } from "@/hooks/use-toast";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import type { TraceViewerData } from "@features/workflow/components/trace/agent-prism";
import {
  applyTraceResponse,
  applyTraceUpdate,
  buildTraceViewerData,
  createEmptyTraceEntry,
  deriveViewerDataList,
  ExecutionTraceEntry,
  ExecutionTraceState,
  getEntryError,
  getEntryStatus,
  markTraceLoading,
  summarizeTrace,
  TraceEntryStatus,
  type TraceExecutionSummary,
  type TraceResponse,
  type TraceSpanStateResponse,
  type TraceUpdateMessage,
} from "@features/workflow/pages/workflow/helpers/trace";

export interface UseExecutionTraceParams {
  backendBaseUrl: string;
  workflowId?: string | null;
  activeExecutionId: string | null;
  isMountedRef: MutableRefObject<boolean>;
  /** Executions listed in the trace list; their spans load on selection. */
  executions?: TraceExecutionSummary[];
  enabled?: boolean;
}

export interface ExecutionTraceResult {
  traces: ExecutionTraceState;
  activeTrace?: ExecutionTraceEntry;
  activeTraceViewer?: TraceViewerData;
  viewerData: TraceViewerData[];
  status: TraceEntryStatus;
  error?: string;
  refresh: (executionId?: string) => Promise<void>;
  loadAll: () => Promise<void>;
  loadSpanState: (
    executionId: string,
    spanId: string,
  ) => Promise<TraceSpanStateResponse>;
  loadMore: (executionId?: string) => Promise<void>;
  canLoadMore: boolean;
  isRefreshing: boolean;
  isLoadingMore: boolean;
  handleTraceUpdate: (update: TraceUpdateMessage) => void;
}

const MAX_TRACE_FETCH_RETRIES = 2;
const RETRY_DELAY_BASE_MS = 300;
const LOAD_ALL_CONCURRENCY = 3;
type TraceFetchMode = "refresh" | "loadMore";

const buildTraceUrl = (
  backendBaseUrl: string,
  executionId: string,
  cursor?: string,
  cacheBustToken?: string,
): string => {
  const url = new URL(
    buildBackendHttpUrl(`/api/executions/${executionId}/trace`, backendBaseUrl),
  );
  if (cursor) {
    url.searchParams.set("cursor", cursor);
  }
  if (cacheBustToken) {
    url.searchParams.set("_refresh", cacheBustToken);
  }
  return url.toString();
};

const buildSpanStateUrl = (
  backendBaseUrl: string,
  executionId: string,
  spanId: string,
): string =>
  buildBackendHttpUrl(
    `/api/executions/${encodeURIComponent(executionId)}/trace/spans/${encodeURIComponent(spanId)}/state`,
    backendBaseUrl,
  );

const buildWorkflowExecutionsUrl = (
  backendBaseUrl: string,
  workflowId: string,
  limit = 50,
): string =>
  buildBackendHttpUrl(
    `/api/workflows/${workflowId}/executions?limit=${encodeURIComponent(
      String(limit),
    )}&include_steps=false`,
    backendBaseUrl,
  );

const appendExecutionId = (ids: string[], executionId: string): string[] =>
  ids.includes(executionId) ? ids : [...ids, executionId];

const removeExecutionId = (ids: string[], executionId: string): string[] =>
  ids.filter((id) => id !== executionId);

const runWithConcurrency = async <T>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>,
): Promise<void> => {
  const queue = [...items];
  const runners = Array.from(
    { length: Math.min(limit, queue.length) },
    async () => {
      for (let item = queue.shift(); item !== undefined; item = queue.shift()) {
        await worker(item);
      }
    },
  );
  await Promise.all(runners);
};

const buildArtifactResolver =
  (backendBaseUrl: string) => (artifactId: string) => {
    const normalizedId =
      typeof artifactId === "string" ? artifactId.trim() : "";
    if (!normalizedId) {
      throw new Error("Invalid artifact identifier provided.");
    }
    return buildBackendHttpUrl(
      `/api/artifacts/${encodeURIComponent(normalizedId)}/download`,
      backendBaseUrl,
    );
  };

const delay = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

class TraceRequestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "TraceRequestError";
  }
}

const createTraceRequestError = async (
  response: Response,
  executionId: string,
): Promise<TraceRequestError> => {
  const detail = (await response.text()).trim();
  const statusText = `${response.status} ${response.statusText}`.trim();
  const baseMessage = `Trace fetch for execution ${executionId} failed (${statusText})`;
  const message = detail ? `${baseMessage}: ${detail}` : baseMessage;
  return new TraceRequestError(message, response.status);
};

const normalizeTraceError = (
  error: unknown,
  executionId: string,
): TraceRequestError => {
  if (error instanceof TraceRequestError) {
    return error;
  }
  if (error instanceof Error) {
    return new TraceRequestError(
      `Network error while fetching trace for execution ${executionId}: ${error.message}`,
    );
  }
  return new TraceRequestError(
    `Unknown error while fetching trace for execution ${executionId}.`,
  );
};

const formatTraceErrorMessage = (error: TraceRequestError): string =>
  error.message;

export function useExecutionTrace({
  backendBaseUrl,
  workflowId,
  activeExecutionId,
  isMountedRef,
  executions,
  enabled = true,
}: UseExecutionTraceParams): ExecutionTraceResult {
  const [traces, setTraces] = useState<ExecutionTraceState>({});
  const tracesRef = useRef(traces);
  tracesRef.current = traces;
  const [discoveredExecutions, setDiscoveredExecutions] = useState<
    TraceExecutionSummary[]
  >([]);
  const fetchingModesRef = useRef(new Map<string, TraceFetchMode>());
  const spanStateCacheRef = useRef(
    new Map<string, Promise<TraceSpanStateResponse>>(),
  );
  const [refreshingExecutionIds, setRefreshingExecutionIds] = useState<
    string[]
  >([]);
  const [loadingMoreExecutionIds, setLoadingMoreExecutionIds] = useState<
    string[]
  >([]);

  const resolveArtifactUrl = useMemo(
    () => buildArtifactResolver(backendBaseUrl),
    [backendBaseUrl],
  );

  const loadLatestExecutions = useCallback(async (): Promise<
    TraceExecutionSummary[]
  > => {
    if (!workflowId) {
      return [];
    }
    try {
      const response = await authFetch(
        buildWorkflowExecutionsUrl(backendBaseUrl, workflowId),
        { cache: "no-store" },
      );
      if (!response.ok) {
        return [];
      }
      const payload = (await response.json()) as Array<{
        execution_id?: string;
        status?: string;
        started_at?: string;
        completed_at?: string | null;
      }>;
      return payload.flatMap((item) =>
        item.execution_id
          ? [
              {
                id: item.execution_id,
                status: item.status,
                startTime: item.started_at,
                endTime: item.completed_at ?? undefined,
              },
            ]
          : [],
      );
    } catch {
      return [];
    }
  }, [backendBaseUrl, workflowId]);

  const fetchTracePage = useCallback(
    async ({
      targetExecutionId,
      mode,
      cursor,
      replaceSpans = false,
      forceNoStore = false,
    }: {
      targetExecutionId?: string;
      mode: TraceFetchMode;
      cursor?: string;
      replaceSpans?: boolean;
      forceNoStore?: boolean;
    }) => {
      if (!enabled) {
        return;
      }
      const executionId = targetExecutionId ?? activeExecutionId;
      if (!executionId) {
        return;
      }
      if (fetchingModesRef.current.has(executionId)) {
        return;
      }

      fetchingModesRef.current.set(executionId, mode);
      if (mode === "refresh") {
        setRefreshingExecutionIds((prev) =>
          appendExecutionId(prev, executionId),
        );
        setTraces((prev) => ({
          ...prev,
          [executionId]: markTraceLoading(
            prev[executionId] ?? createEmptyTraceEntry(executionId),
          ),
        }));
      } else {
        setLoadingMoreExecutionIds((prev) =>
          appendExecutionId(prev, executionId),
        );
      }

      try {
        let lastError: TraceRequestError | undefined;
        let succeeded = false;

        for (
          let attempt = 0;
          attempt <= MAX_TRACE_FETCH_RETRIES;
          attempt += 1
        ) {
          try {
            const response = await authFetch(
              buildTraceUrl(
                backendBaseUrl,
                executionId,
                cursor,
                forceNoStore ? Date.now().toString() : undefined,
              ),
              forceNoStore ? { cache: "no-store" } : undefined,
            );
            if (!response.ok) {
              throw await createTraceRequestError(response, executionId);
            }
            const payload = (await response.json()) as TraceResponse;
            if (!isMountedRef.current) {
              return;
            }
            setTraces((prev) => {
              const current =
                prev[executionId] ?? createEmptyTraceEntry(executionId);
              const next = applyTraceResponse(current, payload, {
                replaceSpans,
              });
              return {
                ...prev,
                [executionId]: next,
              };
            });
            succeeded = true;
            lastError = undefined;
            break;
          } catch (error) {
            lastError = normalizeTraceError(error, executionId);
            if (attempt < MAX_TRACE_FETCH_RETRIES) {
              await delay(RETRY_DELAY_BASE_MS * (attempt + 1));
            }
          }
        }

        if (succeeded || !isMountedRef.current) {
          return;
        }

        const errorMessage = formatTraceErrorMessage(
          lastError ??
            new TraceRequestError(
              `Unknown error while fetching trace for execution ${executionId}.`,
            ),
        );

        toast({
          title: "Trace fetch failed",
          description: errorMessage,
          variant: "destructive",
        });
        setTraces((prev) => {
          const current =
            prev[executionId] ?? createEmptyTraceEntry(executionId);
          return {
            ...prev,
            [executionId]: {
              ...current,
              status: mode === "refresh" ? "error" : current.status,
              error: errorMessage,
            },
          };
        });
      } finally {
        fetchingModesRef.current.delete(executionId);
        if (mode === "refresh") {
          setRefreshingExecutionIds((prev) =>
            removeExecutionId(prev, executionId),
          );
        } else {
          setLoadingMoreExecutionIds((prev) =>
            removeExecutionId(prev, executionId),
          );
        }
      }
    },
    [activeExecutionId, backendBaseUrl, enabled, isMountedRef],
  );

  const refresh = useCallback(
    async (targetExecutionId?: string) => {
      if (!enabled) {
        return;
      }
      if (targetExecutionId) {
        await fetchTracePage({
          targetExecutionId,
          mode: "refresh",
          replaceSpans: true,
          forceNoStore: true,
        });
        return;
      }

      // Discover runs started elsewhere (cron, webhooks) so they appear in
      // the trace list; only the trace being viewed is re-fetched.
      const latestExecutions = await loadLatestExecutions();
      if (isMountedRef.current && latestExecutions.length) {
        setDiscoveredExecutions(latestExecutions);
      }

      const executionIdToRefresh =
        activeExecutionId ?? executions?.[0]?.id ?? latestExecutions[0]?.id;
      if (executionIdToRefresh) {
        await fetchTracePage({
          targetExecutionId: executionIdToRefresh,
          mode: "refresh",
          replaceSpans: true,
          forceNoStore: true,
        });
      }
    },
    [
      activeExecutionId,
      enabled,
      executions,
      fetchTracePage,
      isMountedRef,
      loadLatestExecutions,
    ],
  );

  const loadMore = useCallback(
    async (targetExecutionId?: string) => {
      if (!enabled) {
        return;
      }
      const executionId = targetExecutionId ?? activeExecutionId;
      if (!executionId) {
        return;
      }
      const entry = traces[executionId];
      if (!entry?.hasNextPage || !entry.nextCursor) {
        return;
      }
      await fetchTracePage({
        targetExecutionId: executionId,
        mode: "loadMore",
        cursor: entry.nextCursor,
        forceNoStore: true,
      });
    },
    [activeExecutionId, enabled, fetchTracePage, traces],
  );

  const handleTraceUpdate = useCallback((update: TraceUpdateMessage) => {
    setTraces((prev) => {
      const current =
        prev[update.execution_id] ?? createEmptyTraceEntry(update.execution_id);
      const next = applyTraceUpdate(current, update);
      return {
        ...prev,
        [update.execution_id]: next,
      };
    });
  }, []);

  const executionSummaries = useMemo(() => {
    const byId = new Map<string, TraceExecutionSummary>();
    for (const summary of discoveredExecutions) {
      byId.set(summary.id, summary);
    }
    for (const summary of executions ?? []) {
      byId.set(summary.id, summary);
    }
    return [...byId.values()];
  }, [discoveredExecutions, executions]);

  const loadAll = useCallback(async () => {
    if (!enabled) {
      return;
    }
    const pending = executionSummaries
      .map((summary) => summary.id)
      .filter((executionId) => {
        const status = tracesRef.current[executionId]?.status ?? "idle";
        return status === "idle" || status === "error";
      });
    await runWithConcurrency(pending, LOAD_ALL_CONCURRENCY, (executionId) =>
      fetchTracePage({
        targetExecutionId: executionId,
        mode: "refresh",
        replaceSpans: true,
      }),
    );
  }, [enabled, executionSummaries, fetchTracePage]);

  const loadSpanState = useCallback(
    (executionId: string, spanId: string) => {
      const cacheKey = `${executionId}:${spanId}`;
      const cached = spanStateCacheRef.current.get(cacheKey);
      if (cached) {
        return cached;
      }
      const request = (async () => {
        const response = await authFetch(
          buildSpanStateUrl(backendBaseUrl, executionId, spanId),
        );
        if (!response.ok) {
          throw await createTraceRequestError(response, executionId);
        }
        return (await response.json()) as TraceSpanStateResponse;
      })();
      spanStateCacheRef.current.set(cacheKey, request);
      // Only successful snapshots are immutable; let failures be retried.
      request.catch(() => {
        spanStateCacheRef.current.delete(cacheKey);
      });
      return request;
    },
    [backendBaseUrl],
  );

  // Fetch the selected trace when it is first shown. A failed trace is retried
  // each time it is selected again (or the tab is reopened), but never in a
  // loop while it stays selected; Refresh retries it explicitly.
  const shownExecutionIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!enabled || !activeExecutionId) {
      shownExecutionIdRef.current = null;
      return;
    }
    const isNewSelection = shownExecutionIdRef.current !== activeExecutionId;
    shownExecutionIdRef.current = activeExecutionId;
    const entry = tracesRef.current[activeExecutionId];
    const isUnloaded = !entry || entry.status === "idle";
    if (isUnloaded || (entry.status === "error" && isNewSelection)) {
      void refresh(activeExecutionId);
    }
  }, [activeExecutionId, enabled, refresh]);

  const activeTrace = activeExecutionId ? traces[activeExecutionId] : undefined;

  const activeTraceViewer = useMemo(() => {
    if (!activeTrace) {
      return undefined;
    }
    return buildTraceViewerData(activeTrace, {
      resolveArtifactUrl,
    });
  }, [activeTrace, resolveArtifactUrl]);

  const viewerData = useMemo(
    () =>
      deriveViewerDataList(traces, { resolveArtifactUrl }, executionSummaries),
    [traces, resolveArtifactUrl, executionSummaries],
  );

  const status = getEntryStatus(activeTrace);
  const error = getEntryError(activeTrace);
  const isRefreshing = activeExecutionId
    ? refreshingExecutionIds.includes(activeExecutionId)
    : false;
  const isLoadingMore = activeExecutionId
    ? loadingMoreExecutionIds.includes(activeExecutionId)
    : false;
  const canLoadMore = Boolean(
    activeTrace?.hasNextPage && activeTrace.nextCursor,
  );

  useEffect(() => {
    if (!enabled || !activeTrace) {
      return;
    }
    if (activeTrace.status === "ready" && !activeTrace.isComplete) {
      const summary = summarizeTrace(activeTrace);
      if (summary.spanCount === 0 && !fetchingModesRef.current.size) {
        void refresh(activeTrace.executionId);
      }
    }
  }, [activeTrace, enabled, refresh]);

  return {
    traces,
    activeTrace,
    activeTraceViewer,
    viewerData,
    status,
    error,
    refresh,
    loadAll,
    loadSpanState,
    loadMore,
    canLoadMore,
    isRefreshing,
    isLoadingMore,
    handleTraceUpdate,
  };
}
