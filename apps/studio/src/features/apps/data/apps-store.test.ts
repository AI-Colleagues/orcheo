import { beforeEach, describe, expect, it } from "vitest";
import {
  canPublishApp,
  createApp,
  getApps,
  getPublishBlockedReason,
  resetAppsStoreForTests,
  toggleAppPublish,
} from "./apps-store";
import { SAMPLE_APPS, SAMPLE_APPS_WORKSPACE_SLUG } from "./sample-apps";

describe("apps store", () => {
  beforeEach(() => {
    resetAppsStoreForTests();
  });

  it("seeds sample apps in a fixed workspace and partitions app state", () => {
    const workspaceBApps = getApps("workspace-b");
    const sampleWorkspaceApps = getApps(SAMPLE_APPS_WORKSPACE_SLUG);

    expect(workspaceBApps).toHaveLength(0);
    expect(sampleWorkspaceApps).toHaveLength(SAMPLE_APPS.length);

    const created = createApp(
      "workspace-b",
      "Workspace B app",
      "workspace-b-app",
    );

    expect(getApps(SAMPLE_APPS_WORKSPACE_SLUG)).not.toContainEqual(created);
    expect(getApps("workspace-b")[0]).toEqual(created);
  });

  it("keeps aliases globally unique and validates their format", () => {
    createApp("workspace-b", "First app", "global-alias");

    expect(() =>
      createApp(SAMPLE_APPS_WORKSPACE_SLUG, "Duplicate app", "global-alias"),
    ).toThrow('The alias "global-alias" is already in use.');
    expect(() =>
      createApp(SAMPLE_APPS_WORKSPACE_SLUG, "Reserved app", "admin"),
    ).toThrow('"admin" is reserved');
    expect(() =>
      createApp(SAMPLE_APPS_WORKSPACE_SLUG, "Short app", "ab"),
    ).toThrow("between 3 and 48 characters");
  });

  it("reserves sample aliases before the sample workspace is queried", () => {
    expect(() =>
      createApp(SAMPLE_APPS_WORKSPACE_SLUG, "Duplicate status", "status"),
    ).toThrow('The alias "status" is already in use.');
  });

  it("does not expose a mutable app-list reference", () => {
    const apps = getApps(SAMPLE_APPS_WORKSPACE_SLUG);

    expect(Object.isFrozen(apps)).toBe(true);
    expect(Object.isFrozen(apps[0])).toBe(true);
    expect(Object.isFrozen(apps[0].deployments[0])).toBe(true);
  });

  it("blocks suspended and archived lifecycle states from publishing", () => {
    const published = getApps(SAMPLE_APPS_WORKSPACE_SLUG)[0];

    for (const state of ["suspended", "archived"] as const) {
      const app = { ...published, state };
      expect(canPublishApp(app)).toBe(false);
      expect(getPublishBlockedReason(app)).toBe(
        `A ${state} app cannot be published.`,
      );
    }
  });

  it("requires an active deployment before publishing", () => {
    const draft = getApps(SAMPLE_APPS_WORKSPACE_SLUG).find(
      (app) => app.id === "app-onboarding-demo",
    );
    expect(draft).toBeDefined();
    expect(canPublishApp(draft!)).toBe(false);

    expect(() =>
      toggleAppPublish(SAMPLE_APPS_WORKSPACE_SLUG, "app-onboarding-demo"),
    ).toThrow("An active deployment is required before publishing.");
    expect(
      getApps(SAMPLE_APPS_WORKSPACE_SLUG).find(
        (app) => app.id === "app-onboarding-demo",
      )?.state,
    ).toBe("draft");
  });

  it("allows published apps with active deployments to unpublish and republish", () => {
    toggleAppPublish(SAMPLE_APPS_WORKSPACE_SLUG, "app-research-digest");
    const unpublished = getApps(SAMPLE_APPS_WORKSPACE_SLUG).find(
      (app) => app.id === "app-research-digest",
    );
    expect(unpublished?.state).toBe("unpublished");
    expect(canPublishApp(unpublished!)).toBe(true);

    toggleAppPublish(SAMPLE_APPS_WORKSPACE_SLUG, "app-research-digest");

    expect(
      getApps(SAMPLE_APPS_WORKSPACE_SLUG).find(
        (app) => app.id === "app-research-digest",
      )?.state,
    ).toBe("published");
  });
});
