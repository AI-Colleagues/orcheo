import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBackendHttpUrl,
  buildWorkflowWebSocketUrl,
  getBackendBaseUrl,
} from "./config";

const setLocationOrigin = (origin: string) => {
  vi.stubGlobal("location", new URL(origin));
};

afterEach(() => {
  vi.unstubAllGlobals();
});

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

describe("getBackendBaseUrl", () => {
  it("uses same-origin when Studio is served by the backend", () => {
    setLocationOrigin("http://127.0.0.1:21025");

    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:21025");
  });

  it("keeps the backend dev default when Studio is served by Vite", () => {
    setLocationOrigin("http://localhost:2026");

    expect(getBackendBaseUrl()).toBe("http://localhost:2025");
  });
});
