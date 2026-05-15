import { describe, expect, it } from "vitest";
import { buildVibeContextString } from "./use-vibe-context-string";

describe("buildVibeContextString", () => {
  it("describes the gallery view", () => {
    expect(buildVibeContextString({ page: "gallery" })).toBe(
      "The user is on Gallery.",
    );
  });

  it("describes the active workflow trace view and vault state", () => {
    expect(
      buildVibeContextString({
        page: "canvas",
        workflowId: "wf-123",
        workflowName: "Demo Flow",
        activeTab: "trace",
        vaultOpen: true,
      }),
    ).toBe(
      "The user is on workflow wf-123. The workflow name is Demo Flow. The user is viewing traces for workflow wf-123. Credential Vault is opened.",
    );
  });

  it("describes the workflow canvas view for new workflows", () => {
    expect(
      buildVibeContextString({
        page: "canvas",
        activeTab: "workflow",
      }),
    ).toBe(
      "The user is creating a new workflow. The user is viewing the workflow canvas.",
    );
  });
});
