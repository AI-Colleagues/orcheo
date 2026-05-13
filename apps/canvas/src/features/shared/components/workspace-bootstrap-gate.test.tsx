import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceBootstrapGate } from "./workspace-bootstrap-gate";

const { getAuthenticatedUserProfileMock, getMyWorkspacesMock } = vi.hoisted(
  () => ({
    getAuthenticatedUserProfileMock: vi.fn(),
    getMyWorkspacesMock: vi.fn(),
  }),
);

vi.mock("@features/auth/lib/auth-session", () => ({
  getAuthenticatedUserProfile: getAuthenticatedUserProfileMock,
}));

vi.mock("@/lib/api", () => ({
  createWorkspace: vi.fn(),
  getMyWorkspaces: getMyWorkspacesMock,
}));

let selectedWorkspaceSlug: string | null = null;

vi.mock("@/lib/workspace-session", () => ({
  clearSelectedWorkspaceSlug: () => {
    selectedWorkspaceSlug = null;
  },
  getSelectedWorkspaceSlug: () => selectedWorkspaceSlug,
  setSelectedWorkspaceSlug: (slug: string | null) => {
    selectedWorkspaceSlug = slug?.trim() ? slug.trim() : null;
  },
}));

describe("WorkspaceBootstrapGate", () => {
  beforeEach(() => {
    getAuthenticatedUserProfileMock.mockReturnValue({
      subject: "user-1",
      name: "Morgan Lee",
      email: "morgan@example.com",
      avatar: null,
      role: "member",
    });
    getMyWorkspacesMock.mockResolvedValue({ memberships: [] });
    selectedWorkspaceSlug = null;
  });

  afterEach(() => {
    cleanup();
  });

  it("blocks authenticated content until a workspace exists", async () => {
    render(
      <WorkspaceBootstrapGate>
        <div>Workspace content</div>
      </WorkspaceBootstrapGate>,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/create your first workspace/i),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText("Workspace content")).not.toBeInTheDocument();
    expect(
      (screen.getByLabelText(/workspace name/i) as HTMLInputElement).value,
    ).toBe("Morgan Lee's workspace");
  });
});
