export type TraceLoadStatus = "idle" | "loading" | "ready" | "error";

/** Whether a trace without spans is still waiting for its spans to load. */
export const isTraceLoadPending = (status?: TraceLoadStatus): boolean =>
  status === "idle" || status === "loading";
