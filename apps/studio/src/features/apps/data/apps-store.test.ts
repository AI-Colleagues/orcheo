import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./apps-api";
import { createApp, useApps } from "./apps-store";

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
});
