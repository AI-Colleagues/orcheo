import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActiveWorkspaceIndicator from "@/features/shared/components/top-navigation/active-workspace-indicator";
import { getActiveWorkspace } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getActiveWorkspace: vi.fn(),
}));

describe("ActiveWorkspaceIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the active workspace slug when available", async () => {
    vi.mocked(getActiveWorkspace).mockResolvedValueOnce({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "owner",
    });

    render(<ActiveWorkspaceIndicator />);

    await waitFor(() => {
      expect(screen.getByText("Workspace")).toBeInTheDocument();
      expect(screen.getByText("acme")).toBeInTheDocument();
    });
  });

  it("stays hidden when the workspace cannot be resolved", async () => {
    vi.mocked(getActiveWorkspace).mockRejectedValueOnce(
      new Error("unavailable"),
    );

    render(<ActiveWorkspaceIndicator />);

    await waitFor(() => {
      expect(getActiveWorkspace).toHaveBeenCalledTimes(1);
    });
    expect(
      screen.queryByTestId("active-workspace-indicator"),
    ).not.toBeInTheDocument();
  });
});
