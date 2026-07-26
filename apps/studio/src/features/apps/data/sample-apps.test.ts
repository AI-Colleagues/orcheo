import { afterEach, describe, expect, it, vi } from "vitest";
import { getHostedAppAddress, getHostedAppUrl } from "./sample-apps";

describe("hosted app URLs", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("includes the local app gateway port", () => {
    vi.stubEnv("VITE_ORCHEO_APPS_BASE_DOMAIN", "apps.localhost");
    expect(getHostedAppAddress("hello-orcheo-local")).toBe(
      "hello-orcheo-local.apps.localhost:2030",
    );
    expect(getHostedAppUrl("hello-orcheo-local")).toBe(
      "http://hello-orcheo-local.apps.localhost:2030/",
    );
  });
});
