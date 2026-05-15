import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

let selectedWorkspaceSlug: string | null = null;

vi.mock("@/lib/workspace-session", () => ({
  clearSelectedWorkspaceSlug: () => {
    selectedWorkspaceSlug = null;
  },
  getSelectedWorkspaceSlug: () => selectedWorkspaceSlug,
  getWorkspaceHeaderName: () => "X-Orcheo-Workspace",
  getWorkspaceSelectionHeaders: () =>
    selectedWorkspaceSlug
      ? { "X-Orcheo-Workspace": selectedWorkspaceSlug }
      : {},
  setSelectedWorkspaceSlug: (slug: string | null) => {
    selectedWorkspaceSlug = slug?.trim() ? slug.trim() : null;
  },
}));

import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";

function PathProbe() {
  const { pathname } = useLocation();

  return <div data-testid="pathname">{pathname}</div>;
}

describe("ActiveWorkspaceIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
    selectedWorkspaceSlug = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the active workspace name when available", async () => {
    const user = userEvent.setup();

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

    render(
      <MemoryRouter>
        <ActiveWorkspaceIndicator />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
    });

    const trigger = screen.getByRole("button", { name: /workspace/i });
    expect(trigger).toHaveClass("h-9", "rounded-md", "border-input", "bg-transparent");

    await user.click(trigger);

    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: "Acme" })).toBeInTheDocument();
    });
  });

  it("stays visible while the workspace cannot be resolved", async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error("unavailable"));

    render(
      <MemoryRouter>
        <ActiveWorkspaceIndicator />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
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

    render(
      <MemoryRouter>
        <ActiveWorkspaceIndicator />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
    });

    expect(
      screen.queryByRole("dialog", { name: /create workspace/i }),
    ).not.toBeInTheDocument();
  });

  it("fills the workspace slug from the name until manually overridden", async () => {
    const user = userEvent.setup();

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

    render(
      <MemoryRouter>
        <ActiveWorkspaceIndicator />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /workspace/i }));

    await user.click(await screen.findByRole("menuitem", { name: /create workspace/i }));

    const nameInput = await screen.findByLabelText(/^name$/i);
    const slugInput = screen.getByLabelText(/workspace url name/i);

    await user.clear(nameInput);
    await user.type(nameInput, "Acme Research");

    expect(slugInput).toHaveValue("acme-research");

    await user.clear(slugInput);
    await user.type(slugInput, "acme-labs");

    await user.clear(nameInput);
    await user.type(nameInput, "New Acme");

    expect(slugInput).toHaveValue("acme-labs");
  });

  it("navigates to the matching workspace route without reloading", async () => {
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

    render(
      <MemoryRouter initialEntries={["/stale-workspace/flow-123"]}>
        <PathProbe />
        <ActiveWorkspaceIndicator />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("pathname")).toHaveTextContent(
        "/acme/flow-123",
      );
    });
  });
});
