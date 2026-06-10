import {
  flattenSpans,
  openTelemetrySpanAdapter,
} from "@evilmartians/agent-prism-data";
import type {
  OpenTelemetryDocument,
  OpenTelemetryEvent,
  OpenTelemetrySpan,
  TraceRecord,
  TraceSpan,
  TraceSpanStatus,
  TraceSpanAttribute,
  TraceSpanAttributeValue,
} from "@evilmartians/agent-prism-types";
import { nanoid } from "nanoid";

import type { BadgeProps } from "@features/workflow/components/trace/agent-prism/Badge";
import type { TraceViewerData } from "@features/workflow/components/trace/agent-prism";

export interface TraceSpanStatusResponse {
  code: "OK" | "ERROR" | "UNSET";
  message?: string | null;
}

export interface TraceSpanEventResponse {
  name: string;
  time?: string | null;
  attributes?: Record<string, unknown>;
}

export interface TraceSpanResponse {
  span_id: string;
  parent_span_id?: string | null;
  name?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  attributes: Record<string, unknown>;
  events?: TraceSpanEventResponse[];
  status?: TraceSpanStatusResponse;
  links?: Array<Record<string, unknown>>;
}

export interface TraceExecutionMetadataResponse {
  id: string;
  status: string;
  thread_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  trace_id?: string | null;
  token_usage?: {
    input?: number;
    output?: number;
  };
}

export interface TracePageInfoResponse {
  has_next_page: boolean;
  cursor?: string | null;
}

export interface TraceResponse {
  execution: TraceExecutionMetadataResponse;
  spans: TraceSpanResponse[];
  page_info: TracePageInfoResponse;
}

export interface TraceUpdateMessage {
  type: "trace:update";
  execution_id: string;
  trace_id: string;
  spans: TraceSpanResponse[];
  complete: boolean;
}

export type TraceEntryStatus = "idle" | "loading" | "ready" | "error";

export interface TraceArtifactMetadata {
  id: string;
  downloadUrl?: string;
}

export interface TraceSpanMetadata {
  artifacts: TraceArtifactMetadata[];
  nodeId?: string;
  nodeKind?: string;
  nodeStatus?: string;
  tokenInput?: number;
  tokenOutput?: number;
  workflowStateBefore?: Record<string, unknown>;
  workflowStateAfter?: Record<string, unknown>;
  workflowStateRedacted?: boolean;
  workflowStateTruncated?: boolean;
}

export interface ExecutionTraceEntry {
  executionId: string;
  traceId: string | null;
  metadata?: TraceExecutionMetadataResponse;
  spansById: Record<string, TraceSpanResponse>;
  spanMetadata: Record<string, TraceSpanMetadata>;
  status: TraceEntryStatus;
  error?: string;
  isComplete: boolean;
  hasNextPage: boolean;
  nextCursor?: string;
  lastUpdatedAt?: string;
}

export type ExecutionTraceState = Record<string, ExecutionTraceEntry>;

export interface BuildViewerDataOptions {
  resolveArtifactUrl?: (artifactId: string) => string;
}

const STATUS_CODE_MAP: Record<TraceSpanStatusResponse["code"], string> = {
  OK: "STATUS_CODE_OK",
  ERROR: "STATUS_CODE_ERROR",
  UNSET: "STATUS_CODE_UNSET",
};

const SUCCESS_STATUSES = new Set(["completed", "success", "succeeded"]);
const ERROR_STATUSES = new Set(["error", "failed", "cancelled", "canceled"]);
const PENDING_STATUSES = new Set(["running", "pending", "queued"]);

const MS_TO_NANO = BigInt(1_000_000);

const ensureDateNano = (value?: string | null): string => {
  if (!value) {
    return "0";
  }
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) {
    return "0";
  }
  return (BigInt(ms) * MS_TO_NANO).toString();
};

const toAttributeValue = (value: unknown): TraceSpanAttributeValue => {
  if (typeof value === "string") {
    return { stringValue: value };
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return { intValue: String(value) };
  }
  if (typeof value === "boolean") {
    return { boolValue: value };
  }
  if (value == null) {
    return { stringValue: "" };
  }
  if (typeof value === "object") {
    return { stringValue: JSON.stringify(value) };
  }
  return { stringValue: String(value) };
};

const toAttributes = (
  attributes: Record<string, unknown>,
): TraceSpanAttribute[] =>
  Object.entries(attributes).map(([key, value]) => ({
    key,
    value: toAttributeValue(value),
  }));

const toEvents = (
  events: TraceSpanEventResponse[] | undefined,
): OpenTelemetryEvent[] | undefined => {
  if (!events?.length) {
    return undefined;
  }
  return events.map((event) => ({
    name: event.name,
    timeUnixNano: ensureDateNano(event.time),
    attributes: event.attributes ? toAttributes(event.attributes) : undefined,
  }));
};

const createSpanMetadata = (span: TraceSpanResponse): TraceSpanMetadata => {
  const artifactsValue = span.attributes?.["orcheo.artifact.ids"];
  const artifacts: TraceArtifactMetadata[] = Array.isArray(artifactsValue)
    ? artifactsValue
        .map((value) => ({ id: String(value) }))
        .filter((item) => item.id.trim().length > 0)
    : [];

  const metadata: TraceSpanMetadata = {
    artifacts,
  };

  const nodeId = span.attributes?.["orcheo.node.id"];
  if (nodeId) {
    metadata.nodeId = String(nodeId);
  }
  const nodeKind = span.attributes?.["orcheo.node.kind"];
  if (nodeKind) {
    metadata.nodeKind = String(nodeKind);
  }
  const nodeStatus = span.attributes?.["orcheo.node.status"];
  if (nodeStatus) {
    metadata.nodeStatus = String(nodeStatus);
  }

  const input = span.attributes?.["orcheo.token.input"];
  const output = span.attributes?.["orcheo.token.output"];
  if (typeof input === "number" && Number.isFinite(input)) {
    metadata.tokenInput = input;
  }
  if (typeof output === "number" && Number.isFinite(output)) {
    metadata.tokenOutput = output;
  }

  const workflowStateBefore = span.attributes?.["orcheo.workflow.state.before"];
  if (
    workflowStateBefore &&
    typeof workflowStateBefore === "object" &&
    !Array.isArray(workflowStateBefore)
  ) {
    metadata.workflowStateBefore = workflowStateBefore as Record<
      string,
      unknown
    >;
  }

  const workflowStateAfter = span.attributes?.["orcheo.workflow.state.after"];
  if (
    workflowStateAfter &&
    typeof workflowStateAfter === "object" &&
    !Array.isArray(workflowStateAfter)
  ) {
    metadata.workflowStateAfter = workflowStateAfter as Record<string, unknown>;
  }

  if (span.attributes?.["orcheo.workflow.state.redacted"] === true) {
    metadata.workflowStateRedacted = true;
  }

  if (span.attributes?.["orcheo.workflow.state.truncated"] === true) {
    metadata.workflowStateTruncated = true;
  }

  return metadata;
};

const normalizeThreadId = (value: unknown): string | undefined => {
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = value.trim();
  return normalized || undefined;
};

const mergeAttributes = (
  existing: Record<string, unknown>,
  incoming: Record<string, unknown>,
): Record<string, unknown> => ({
  ...existing,
  ...incoming,
});

const mergeEvents = (
  existing: TraceSpanEventResponse[] | undefined,
  incoming: TraceSpanEventResponse[] | undefined,
): TraceSpanEventResponse[] | undefined => {
  if (!existing?.length) {
    return incoming?.slice();
  }
  if (!incoming?.length) {
    return existing;
  }

  const combined = [...existing];
  const seen = new Set(combined.map((event) => JSON.stringify(event)));
  for (const event of incoming) {
    const key = JSON.stringify(event);
    if (!seen.has(key)) {
      combined.push(event);
      seen.add(key);
    }
  }
  return combined;
};

const mergeSpan = (
  existing: TraceSpanResponse | undefined,
  update: TraceSpanResponse,
): TraceSpanResponse => {
  if (!existing) {
    return { ...update, attributes: { ...update.attributes } };
  }
  return {
    span_id: update.span_id || existing.span_id,
    parent_span_id:
      update.parent_span_id !== undefined
        ? update.parent_span_id
        : existing.parent_span_id,
    name: update.name ?? existing.name,
    start_time: update.start_time ?? existing.start_time,
    end_time: update.end_time ?? existing.end_time,
    attributes: update.attributes
      ? mergeAttributes(existing.attributes, update.attributes)
      : existing.attributes,
    events: mergeEvents(existing.events, update.events),
    status: update.status ?? existing.status,
    links: update.links ?? existing.links,
  };
};

export const createEmptyTraceEntry = (
  executionId: string,
): ExecutionTraceEntry => ({
  executionId,
  traceId: null,
  metadata: undefined,
  spansById: {},
  spanMetadata: {},
  status: "idle",
  error: undefined,
  isComplete: false,
  hasNextPage: false,
  nextCursor: undefined,
  lastUpdatedAt: undefined,
});

export interface ApplyTraceResponseOptions {
  replaceSpans?: boolean;
}

const updateEntryWithSpan = (
  entry: ExecutionTraceEntry,
  span: TraceSpanResponse,
): ExecutionTraceEntry => {
  const merged = mergeSpan(entry.spansById[span.span_id], span);
  return {
    ...entry,
    spansById: {
      ...entry.spansById,
      [merged.span_id]: merged,
    },
    spanMetadata: {
      ...entry.spanMetadata,
      [merged.span_id]: createSpanMetadata(merged),
    },
    lastUpdatedAt: new Date().toISOString(),
  };
};

export const applyTraceResponse = (
  entry: ExecutionTraceEntry,
  response: TraceResponse,
  options: ApplyTraceResponseOptions = {},
): ExecutionTraceEntry => {
  const baseEntry = options.replaceSpans
    ? {
        ...entry,
        spansById: {},
        spanMetadata: {},
      }
    : entry;
  const priorIsComplete = options.replaceSpans ? false : baseEntry.isComplete;

  let next = {
    ...baseEntry,
    status: "ready" as TraceEntryStatus,
    metadata: response.execution,
    traceId: response.execution.trace_id ?? baseEntry.traceId,
    error: undefined,
    hasNextPage: response.page_info.has_next_page,
    nextCursor: response.page_info.cursor ?? undefined,
    isComplete:
      priorIsComplete ||
      Boolean(response.execution.finished_at) ||
      response.page_info.has_next_page === false,
  };

  for (const span of response.spans) {
    next = updateEntryWithSpan(next, span);
  }

  return next;
};

export const applyTraceUpdate = (
  entry: ExecutionTraceEntry,
  update: TraceUpdateMessage,
): ExecutionTraceEntry => {
  let next: ExecutionTraceEntry = {
    ...entry,
    traceId: update.trace_id,
    lastUpdatedAt: new Date().toISOString(),
  };
  for (const span of update.spans) {
    next = updateEntryWithSpan(next, span);
  }
  if (update.complete) {
    next = {
      ...next,
      isComplete: true,
      hasNextPage: false,
      nextCursor: undefined,
    };
  }
  return next;
};

const toOpenTelemetrySpan = (
  traceId: string,
  span: TraceSpanResponse,
): OpenTelemetrySpan => ({
  traceId,
  spanId: span.span_id,
  parentSpanId: span.parent_span_id ?? undefined,
  name: span.name ?? "span",
  kind: "SPAN_KIND_INTERNAL",
  startTimeUnixNano: ensureDateNano(span.start_time),
  endTimeUnixNano: ensureDateNano(span.end_time ?? span.start_time),
  attributes: toAttributes(span.attributes ?? {}),
  status: {
    code: STATUS_CODE_MAP[span.status?.code ?? "UNSET"],
    message: span.status?.message ?? undefined,
  },
  flags: 0,
  events: toEvents(span.events),
  links: undefined,
});

const buildTraceRecord = (
  entry: ExecutionTraceEntry,
  spans: TraceSpan[],
): TraceRecord => {
  const startedAt = entry.metadata?.started_at
    ? Date.parse(entry.metadata.started_at)
    : undefined;
  const finishedAt = entry.metadata?.finished_at
    ? Date.parse(entry.metadata.finished_at)
    : undefined;
  const durationMs =
    startedAt && finishedAt ? Math.max(finishedAt - startedAt, 0) : 0;
  const totalTokens =
    (entry.metadata?.token_usage?.input ?? 0) +
    (entry.metadata?.token_usage?.output ?? 0);

  return {
    id: entry.executionId,
    name: entry.metadata?.trace_id ?? entry.executionId,
    spansCount: spans.length,
    durationMs,
    agentDescription: entry.metadata?.status ?? "unknown",
    totalTokens,
    startTime: startedAt,
  };
};

const resolveEntryThreadId = (
  entry: ExecutionTraceEntry,
): string | undefined => {
  const metadataThreadId = normalizeThreadId(entry.metadata?.thread_id);
  if (metadataThreadId) {
    return metadataThreadId;
  }

  for (const span of Object.values(entry.spansById)) {
    if (span.parent_span_id) {
      continue;
    }
    const rootThreadId = normalizeThreadId(
      span.attributes["orcheo.execution.thread_id"] ??
        span.attributes["thread_id"] ??
        span.attributes["threadId"],
    );
    if (rootThreadId) {
      return rootThreadId;
    }
  }

  return undefined;
};

const attachMetadataToSpan = (
  span: TraceSpan,
  metadata: Record<string, TraceSpanMetadata>,
  resolver?: (artifactId: string) => string,
): TraceSpan => {
  const meta = metadata[span.id];
  if (meta) {
    span.metadata = {
      ...meta,
      artifacts: meta.artifacts.map((artifact) => {
        if (!resolver) {
          return { id: artifact.id };
        }
        try {
          return {
            id: artifact.id,
            downloadUrl: resolver(artifact.id),
          };
        } catch {
          return { id: artifact.id };
        }
      }),
    };
  }
  if (span.children?.length) {
    span.children = span.children.map((child) =>
      attachMetadataToSpan(child, metadata, resolver),
    );
  }
  return span;
};

const sortChildrenByStart = (span: TraceSpan): TraceSpan => {
  if (span.children?.length) {
    span.children = span.children
      .map((child) => sortChildrenByStart(child))
      .sort((a, b) => a.startTime.getTime() - b.startTime.getTime());
  }
  return span;
};

export const buildTraceViewerData = (
  entry: ExecutionTraceEntry,
  options: BuildViewerDataOptions = {},
): TraceViewerData | undefined => {
  const traceId = entry.traceId ?? entry.metadata?.trace_id;
  if (!traceId) {
    return undefined;
  }
  const spans = Object.values(entry.spansById);
  if (spans.length === 0) {
    return undefined;
  }

  const document: OpenTelemetryDocument = {
    resourceSpans: [
      {
        resource: { attributes: [] },
        scopeSpans: [
          {
            scope: { name: "orcheo.trace" },
            spans: spans.map((span) => toOpenTelemetrySpan(traceId, span)),
          },
        ],
      },
    ],
  };

  const spanTree =
    openTelemetrySpanAdapter.convertRawDocumentsToSpans(document);

  const enrichedTree = spanTree
    .map((span) =>
      attachMetadataToSpan(
        span,
        entry.spanMetadata,
        options.resolveArtifactUrl,
      ),
    )
    .map((span) => sortChildrenByStart(span));

  const flattened = flattenSpans(enrichedTree);
  const traceRecord = buildTraceRecord(entry, flattened);

  const badges: BadgeProps[] = [];
  if (entry.metadata?.status) {
    badges.push({ label: `Status: ${entry.metadata.status}` });
  }
  const inputTokens = entry.metadata?.token_usage?.input ?? 0;
  const outputTokens = entry.metadata?.token_usage?.output ?? 0;
  if (inputTokens || outputTokens) {
    badges.push({
      label: `Tokens in/out: ${inputTokens}/${outputTokens}`,
    });
  }
  if (!entry.isComplete) {
    badges.push({
      label: "Live",
      className:
        "bg-agentprism-warning text-agentprism-warning-muted-foreground",
    });
  }

  return {
    traceRecord,
    spans: enrichedTree,
    badges,
    threadId: resolveEntryThreadId(entry),
  };
};

export const getEntryStatus = (
  entry: ExecutionTraceEntry | undefined,
): TraceEntryStatus => entry?.status ?? "idle";

export const getEntryError = (
  entry: ExecutionTraceEntry | undefined,
): string | undefined => entry?.error;

export const summarizeTrace = (entry: ExecutionTraceEntry) => {
  const spanCount = Object.keys(entry.spansById).length;
  const totalTokens =
    (entry.metadata?.token_usage?.input ?? 0) +
    (entry.metadata?.token_usage?.output ?? 0);
  return {
    spanCount,
    totalTokens,
  };
};

export const upsertTraceError = (
  entry: ExecutionTraceEntry,
  error: string,
): ExecutionTraceEntry => ({
  ...entry,
  status: "error",
  error,
});

export const markTraceLoading = (
  entry: ExecutionTraceEntry,
): ExecutionTraceEntry => ({
  ...entry,
  status: "loading",
  error: undefined,
});

export const markTraceReady = (
  entry: ExecutionTraceEntry,
): ExecutionTraceEntry => ({
  ...entry,
  status: "ready",
});

export const deriveViewerDataList = (
  state: ExecutionTraceState,
  options: BuildViewerDataOptions = {},
): TraceViewerData[] =>
  Object.values(state)
    .map((entry) => buildTraceViewerData(entry, options))
    .filter((value): value is TraceViewerData => Boolean(value))
    .sort((a, b) => {
      const aStart = a.traceRecord.startTime ?? 0;
      const bStart = b.traceRecord.startTime ?? 0;
      return bStart - aStart;
    });

const deriveSpanTimeBounds = (
  spans: TraceSpan[],
): {
  start: number;
  end: number;
} => {
  const flattened = flattenSpans(spans);
  if (flattened.length === 0) {
    const now = Date.now();
    return { start: now, end: now };
  }

  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const span of flattened) {
    const spanStart = span.startTime.getTime();
    const spanEnd = span.endTime.getTime();
    if (Number.isFinite(spanStart)) {
      start = Math.min(start, spanStart);
    }
    if (Number.isFinite(spanEnd)) {
      end = Math.max(end, spanEnd);
    }
  }

  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    const now = Date.now();
    return { start: now, end: now };
  }

  return {
    start,
    end: Math.max(end, start),
  };
};

const resolveSegmentBounds = (
  trace: TraceViewerData,
): {
  start: number;
  end: number;
} => {
  const spanBounds = deriveSpanTimeBounds(trace.spans);
  const start = trace.traceRecord.startTime ?? spanBounds.start;
  const durationMs = Math.max(trace.traceRecord.durationMs ?? 0, 0);
  const endFromRecord = start + durationMs;
  const end =
    durationMs > 0 ? endFromRecord : Math.max(spanBounds.end, endFromRecord);
  return { start, end: Math.max(end, start) };
};

const toSpanStatus = (executionStatus: string | undefined): TraceSpanStatus => {
  if (!executionStatus) {
    return "warning";
  }
  const normalized = executionStatus.toLowerCase();
  if (SUCCESS_STATUSES.has(normalized)) {
    return "success";
  }
  if (ERROR_STATUSES.has(normalized)) {
    return "error";
  }
  if (PENDING_STATUSES.has(normalized)) {
    return "pending";
  }
  return "warning";
};

const buildStitchedSegment = (
  trace: TraceViewerData,
  threadId: string,
  segmentIndex: number,
): TraceSpan => {
  const { start, end } = resolveSegmentBounds(trace);
  return {
    id: `stitched:${threadId}:${trace.traceRecord.id}:${segmentIndex}`,
    title: `Execution ${trace.traceRecord.id}`,
    startTime: new Date(start),
    endTime: new Date(end),
    duration: Math.max(end - start, 0),
    type: "span",
    raw: JSON.stringify({
      stitched: true,
      threadId,
      executionId: trace.traceRecord.id,
    }),
    status: toSpanStatus(trace.traceRecord.agentDescription),
    children: trace.spans,
    metadata: {
      stitched: true,
      isSegment: true,
      threadId,
      executionId: trace.traceRecord.id,
      traceId: trace.traceRecord.name,
    },
  };
};

export const deriveThreadStitchedViewerDataList = (
  data: TraceViewerData[],
  activeTraceId?: string,
): TraceViewerData[] => {
  const grouped = new Map<string, TraceViewerData[]>();
  for (const trace of data) {
    const key = trace.threadId
      ? `thread:${trace.threadId}`
      : `execution:${trace.traceRecord.id}`;
    const existing = grouped.get(key);
    if (existing) {
      existing.push(trace);
    } else {
      grouped.set(key, [trace]);
    }
  }

  const stitched: TraceViewerData[] = [];
  for (const traces of grouped.values()) {
    if (traces.length === 0) {
      continue;
    }
    const sortedByStartDesc = [...traces].sort((a, b) => {
      const aStart = a.traceRecord.startTime ?? 0;
      const bStart = b.traceRecord.startTime ?? 0;
      return bStart - aStart;
    });

    const latestTrace = sortedByStartDesc[0];
    if (!latestTrace) {
      continue;
    }

    const threadId = latestTrace.threadId;
    if (!threadId || sortedByStartDesc.length === 1) {
      stitched.push(latestTrace);
      continue;
    }

    const sortedByStartAsc = [...sortedByStartDesc].sort((a, b) => {
      const aStart = a.traceRecord.startTime ?? 0;
      const bStart = b.traceRecord.startTime ?? 0;
      return aStart - bStart;
    });
    const representative =
      sortedByStartDesc.find(
        (trace) => trace.traceRecord.id === activeTraceId,
      ) ?? sortedByStartDesc[0];

    const segmentSpans = sortedByStartAsc.map((trace, index) =>
      buildStitchedSegment(trace, threadId, index),
    );
    const earliestStart = Math.min(
      ...segmentSpans.map((segment) => segment.startTime.getTime()),
    );
    const latestEnd = Math.max(
      ...segmentSpans.map((segment) => segment.endTime.getTime()),
    );
    const totalTokens = sortedByStartDesc.reduce(
      (sum, trace) => sum + (trace.traceRecord.totalTokens ?? 0),
      0,
    );

    stitched.push({
      traceRecord: {
        id: representative.traceRecord.id,
        name: `Thread ${threadId}`,
        spansCount: sortedByStartDesc.reduce(
          (sum, trace) => sum + trace.traceRecord.spansCount,
          0,
        ),
        durationMs: Math.max(latestEnd - earliestStart, 0),
        agentDescription: `${sortedByStartDesc.length} executions`,
        totalTokens,
        startTime: representative.traceRecord.startTime,
      },
      spans: segmentSpans,
      badges: [
        { label: `Thread: ${threadId}` },
        { label: `Executions: ${sortedByStartDesc.length}` },
        { label: "Stitched timeline" },
      ],
      threadId,
    });
  }

  return stitched.sort((a, b) => {
    const aStart = a.traceRecord.startTime ?? 0;
    const bStart = b.traceRecord.startTime ?? 0;
    return bStart - aStart;
  });
};

export const DEFAULT_TRACE_BADGES: BadgeProps[] = [{ label: "Trace" }];

export const ensureTraceId = (): string => nanoid();
