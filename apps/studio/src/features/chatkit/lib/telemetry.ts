type ChatTelemetryEvent =
  | "studio.chat.open"
  | "studio.chat.close"
  | "studio.chat.session.success"
  | "studio.chat.session.failure";

const buildDetail = (detail?: Record<string, unknown>) => ({
  timestamp: new Date().toISOString(),
  ...(detail ?? {}),
});

export function recordChatTelemetry(
  event: ChatTelemetryEvent,
  detail?: Record<string, unknown>,
) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("chatkit:telemetry", {
        detail: { event, ...buildDetail(detail) },
      }),
    );
  }

  if (process.env.NODE_ENV !== "production") {
    console.info(`[telemetry] ${event}`, buildDetail(detail));
  }
}

export type { ChatTelemetryEvent };
