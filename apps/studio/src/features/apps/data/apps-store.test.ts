import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./apps-api";
import {
  canPublishApp,
  createApp,
  getPublishBlockedReason,
  useApp,
  useApps,
} from "./apps-store";

vi.mock("./apps-api", () => ({
  createHostedApp: vi.fn(),
  getHostedApp: vi.fn(),
  listAppAudit: vi.fn(),
  listBindings: vi.fn(),
  listCollections: vi.fn(),
  listDeployments: vi.fn(),
  listHostedApps: vi.fn(),
  publishHostedApp: vi.fn(),
  unpublishHostedApp: vi.fn(),
  uploadHostedAppBundle: vi.fn(),
}));

const app = {
  id: "app-1",
  workspace_id: "workspace-1",
  alias: "portal",
  name: "Portal",
  description: null,
  visibility: "public" as const,
  publication_state: "draft" as const,
  state: "draft" as const,
  is_archived: false,
  active_release_id: null,
  active_deployment_id: null,
  permission_revision: 1,
  published_permission_revision: null,
  created_at: "2026-07-24T10:00:00Z",
  updated_at: "2026-07-24T10:00:00Z",
};

describe("Hosted Apps workspace data", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listHostedApps).mockResolvedValue([app]);
  });

  it("refetches with a new workspace-aware key", async () => {
    const { result, rerender } = renderHook(
      ({ workspace }) => useApps(workspace),
      { initialProps: { workspace: "workspace-a" } },
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.apps[0]?.alias).toBe("portal");
    rerender({ workspace: "workspace-b" });
    await waitFor(() => expect(api.listHostedApps).toHaveBeenCalledTimes(2));
  });

  it("surfaces API errors without retaining another workspace's apps", async () => {
    vi.mocked(api.listHostedApps).mockRejectedValueOnce(
      new Error("App access denied."),
    );
    const { result } = renderHook(() => useApps("workspace-a"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.apps).toEqual([]);
    expect(result.current.error).toBe("App access denied.");
  });

  it("creates through the authenticated API instead of local sample state", async () => {
    vi.mocked(api.createHostedApp).mockResolvedValue(app);
    await act(async () => {
      const created = await createApp("Portal", "portal");
      expect(created.id).toBe("app-1");
    });
    expect(api.createHostedApp).toHaveBeenCalledWith("Portal", "portal");
  });

  it("blocks invalid lifecycle states and apps without a ready deployment", () => {
    const baseApp = {
      id: "app-1",
      name: "Portal",
      alias: "portal",
      visibility: "public" as const,
      health: "unknown" as const,
      updated: "just now",
      deployments: [],
      bindings: [],
      collections: [],
      permissionRevision: 1,
    };

    for (const state of ["suspended", "archived"] as const) {
      const hostedApp = { ...baseApp, state };
      expect(canPublishApp(hostedApp)).toBe(false);
      expect(getPublishBlockedReason(hostedApp)).toBe(
        `A ${state} app cannot be published.`,
      );
    }

    const draft = { ...baseApp, state: "draft" as const };
    expect(canPublishApp(draft)).toBe(false);
    expect(getPublishBlockedReason(draft)).toBe(
      "Upload and validate a deployment before publishing.",
    );
  });

  it("maps deployment manifest bindings for publish review", async () => {
    vi.mocked(api.getHostedApp).mockResolvedValue({
      ...app,
      publication_state: "published",
      state: "published",
      active_release_id: "release-1",
      active_deployment_id: "deployment-1",
    });
    vi.mocked(api.listDeployments).mockResolvedValue([
      {
        id: "deployment-1",
        status: "ready",
        archive_sha256: "a".repeat(64),
        manifest_sha256: "b".repeat(64),
        app_manifest: {
          schema_version: 1,
          bindings: {
            greet: {
              workflow: "hosted-app-greeting",
              version: 1,
              access_mode: "anonymous",
              input_schema: {},
              output_projection: {},
              visitor_can_read_output: true,
              visitor_can_read_sanitized_errors: true,
              limits: { per_app_per_minute: 200 },
            },
            farewell: {
              workflow: "hosted-app-farewell",
              version: 1,
              access_mode: "anonymous",
              input_schema: {},
              output_projection: {},
              visitor_can_read_output: true,
              visitor_can_read_sanitized_errors: true,
              limits: { per_app_per_minute: 200 },
            },
          },
        },
        validation_error_code: null,
        validation_error_message: null,
        created_at: "2026-07-26T08:00:00Z",
      },
    ]);
    vi.mocked(api.listBindings).mockResolvedValue([]);
    vi.mocked(api.listCollections).mockResolvedValue([]);
    vi.mocked(api.listAppAudit).mockResolvedValue([]);

    const { result } = renderHook(() => useApp("app-1", "workspace-a"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(
      result.current.app?.deployments[0]?.manifestBindings?.map(
        (binding) => binding.name,
      ),
    ).toEqual(["greet", "farewell"]);
    expect(result.current.app?.deployments[0]?.active).toBe(true);
  });
});
