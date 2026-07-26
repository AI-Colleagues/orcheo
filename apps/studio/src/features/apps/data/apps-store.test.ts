import { beforeEach, describe, expect, it } from "vitest";
import {
  canPublishApp,
  createApp,
  getApps,
  getPublishBlockedReason,
  resetAppsStoreForTests,
  toggleAppPublish,
} from "./apps-store";
import { SAMPLE_APPS } from "./sample-apps";

describe("apps store", () => {
  beforeEach(() => {
    resetAppsStoreForTests();
  });

  it("partitions app state by workspace", () => {
    const workspaceAApps = getApps("workspace-a");
    const workspaceBApps = getApps("workspace-b");

    expect(workspaceAApps).toHaveLength(SAMPLE_APPS.length);
    expect(workspaceBApps).toHaveLength(0);

    const created = createApp(
      "workspace-b",
      "Workspace B app",
      "workspace-b-app",
    );

    expect(getApps("workspace-a")).not.toContainEqual(created);
    expect(getApps("workspace-b")[0]).toEqual(created);
  });

  it("keeps aliases globally unique and validates their format", () => {
    createApp("workspace-b", "First app", "global-alias");

    expect(() =>
      createApp("workspace-a", "Duplicate app", "global-alias"),
    ).toThrow('The alias "global-alias" is already in use.');
    expect(() => createApp("workspace-a", "Reserved app", "admin")).toThrow(
      '"admin" is reserved',
    );
    expect(() => createApp("workspace-a", "Short app", "ab")).toThrow(
      "between 3 and 48 characters",
    );
  });

  it("does not expose a mutable app-list reference", () => {
    const apps = getApps("workspace-a");

    expect(Object.isFrozen(apps)).toBe(true);
    expect(Object.isFrozen(apps[0])).toBe(true);
    expect(Object.isFrozen(apps[0].deployments[0])).toBe(true);
  });

  it("blocks suspended and archived lifecycle states from publishing", () => {
    const published = getApps("workspace-a")[0];

    for (const state of ["suspended", "archived"] as const) {
      const app = { ...published, state };
      expect(canPublishApp(app)).toBe(false);
      expect(getPublishBlockedReason(app)).toBe(
        `A ${state} app cannot be published.`,
      );
    }
  });

  it("requires an active deployment before publishing", () => {
    const draft = getApps("workspace-a").find(
      (app) => app.id === "app-onboarding-demo",
    );
    expect(draft).toBeDefined();
    expect(canPublishApp(draft!)).toBe(false);

    expect(() =>
      toggleAppPublish("workspace-a", "app-onboarding-demo"),
    ).toThrow("An active deployment is required before publishing.");
    expect(
      getApps("workspace-a").find((app) => app.id === "app-onboarding-demo")
        ?.state,
    ).toBe("draft");
  });

  it("allows published apps with active deployments to unpublish and republish", () => {
    toggleAppPublish("workspace-a", "app-research-digest");
    const unpublished = getApps("workspace-a").find(
      (app) => app.id === "app-research-digest",
    );
    expect(unpublished?.state).toBe("unpublished");
    expect(canPublishApp(unpublished!)).toBe(true);

    toggleAppPublish("workspace-a", "app-research-digest");

    expect(
      getApps("workspace-a").find((app) => app.id === "app-research-digest")
        ?.state,
    ).toBe("published");
  });
});
