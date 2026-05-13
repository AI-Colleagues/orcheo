import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let selectedWorkspaceSlug: string | null = null;

vi.mock("@/lib/workspace-session", () => ({
  clearSelectedWorkspaceSlug: () => {
    selectedWorkspaceSlug = null;
    window.dispatchEvent(new Event("orcheo-workspace-selection-changed"));
  },
  getSelectedWorkspaceSlug: () => selectedWorkspaceSlug,
  getWorkspaceHeaderName: () => "X-Orcheo-Workspace",
  getWorkspaceSelectionHeaders: () =>
    selectedWorkspaceSlug
      ? { "X-Orcheo-Workspace": selectedWorkspaceSlug }
      : {},
  setSelectedWorkspaceSlug: (slug: string | null) => {
    selectedWorkspaceSlug = slug?.trim() ? slug.trim() : null;
    window.dispatchEvent(new Event("orcheo-workspace-selection-changed"));
  },
  WORKSPACE_SELECTION_CHANGED_EVENT: "orcheo-workspace-selection-changed",
}));

import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";

describe("ActiveWorkspaceIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
    selectedWorkspaceSlug = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the active workspace slug when available", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/workspaces/me")) {
        return {
          ok: true,
          json: async () => ({
            memberships: [
              {
                workspace_id: "workspace-1",
                slug: "acme",
                name: "Acme",
                role: "owner",
                status: "active",
              },
            ],
          }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<ActiveWorkspaceIndicator />);

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
      expect(screen.getByText("acme")).toBeInTheDocument();
    });
  });

  it("stays visible while the workspace cannot be resolved", async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error("unavailable"));

    render(<ActiveWorkspaceIndicator />);

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
      expect(screen.getByText("No workspace")).toBeInTheDocument();
    });
  });

  it("does not auto-open the create-workspace dialog when memberships are empty", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/workspaces/me")) {
        return {
          ok: true,
          json: async () => ({ memberships: [] }),
        } as Response;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<ActiveWorkspaceIndicator />);

    await waitFor(() => {
      expect(screen.getByText("No workspace")).toBeInTheDocument();
    });

    expect(
      screen.queryByRole("dialog", { name: /create workspace/i }),
    ).not.toBeInTheDocument();
  });
});
