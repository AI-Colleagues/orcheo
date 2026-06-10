import { describe, expect, it } from "vitest";

import { buildBackendHttpUrl, buildWorkflowWebSocketUrl } from "./config";

describe("buildWorkflowWebSocketUrl", () => {
  it("appends an access token query parameter when provided", () => {
    expect(
      buildWorkflowWebSocketUrl("wf-1", "http://localhost:2025", "token-123"),
    ).toBe("ws://localhost:2025/ws/workflow/wf-1?access_token=token-123");
  });

  it("uses the public HTTPS origin for websocket routing", () => {
    expect(
      buildWorkflowWebSocketUrl("wf-1", "https://orcheo.example.com"),
    ).toBe("wss://orcheo.example.com/ws/workflow/wf-1");
  });
});

describe("buildBackendHttpUrl", () => {
  it("preserves a public same-origin backend base URL", () => {
    expect(
      buildBackendHttpUrl("/api/system/info", "https://orcheo.example.com"),
    ).toBe("https://orcheo.example.com/api/system/info");
  });
});
