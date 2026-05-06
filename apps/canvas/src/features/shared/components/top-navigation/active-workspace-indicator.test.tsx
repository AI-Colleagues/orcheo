import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";

describe("ActiveWorkspaceIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the active workspace slug when available", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/workspaces/active")) {
        return {
          ok: true,
          json: async () => ({
            workspace_id: "workspace-1",
            slug: "acme",
            name: "Acme",
            role: "owner",
          }),
        } as Response;
      }
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
      expect(screen.getByText("Loading…")).toBeInTheDocument();
    });
  });
});
