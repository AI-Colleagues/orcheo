import { describe, expect, it } from "vitest";
import {
  getHostedAppAddress,
  getHostedAppUrl,
} from "./sample-apps";

describe("hosted app URLs", () => {
  it("includes the local app gateway port", () => {
    expect(getHostedAppAddress("hello-orcheo-local")).toBe(
      "hello-orcheo-local.apps.localhost:2030",
    );
    expect(getHostedAppUrl("hello-orcheo-local")).toBe(
      "http://hello-orcheo-local.apps.localhost:2030/",
    );
  });
});
