import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceMembers from "./workspace-members";

const {
  getActiveWorkspaceMock,
  listWorkspaceMembersMock,
  addWorkspaceMemberMock,
  updateWorkspaceMemberRoleMock,
  removeWorkspaceMemberMock,
  getAuthenticatedUserProfileMock,
  toastMock,
} = vi.hoisted(() => ({
  getActiveWorkspaceMock: vi.fn(),
  listWorkspaceMembersMock: vi.fn(),
  addWorkspaceMemberMock: vi.fn(),
  updateWorkspaceMemberRoleMock: vi.fn(),
  removeWorkspaceMemberMock: vi.fn(),
  getAuthenticatedUserProfileMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getActiveWorkspace: getActiveWorkspaceMock,
  listWorkspaceMembers: listWorkspaceMembersMock,
  addWorkspaceMember: addWorkspaceMemberMock,
  updateWorkspaceMemberRole: updateWorkspaceMemberRoleMock,
  removeWorkspaceMember: removeWorkspaceMemberMock,
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  getAuthenticatedUserProfile: getAuthenticatedUserProfileMock,
  clearAuthSession: vi.fn(),
}));

vi.mock("@/lib/workspace-session", () => ({
  getSelectedWorkspaceSlug: () => "acme",
}));

vi.mock("@/hooks/use-toast", () => ({
  toast: toastMock,
}));

vi.mock("@/hooks/use-credential-vault", () => ({
  default: () => ({
    credentials: [],
    isLoading: false,
    onAddCredential: vi.fn(),
    onUpdateCredential: vi.fn(),
    onDeleteCredential: vi.fn(),
    onRevealCredentialSecret: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-page-context", () => ({
  usePageContext: () => ({
    setPageContext: vi.fn(),
    setVaultOpen: vi.fn(),
    pageContext: { page: "workspace" },
  }),
}));

vi.mock("@features/shared/components/top-navigation", () => ({
  default: () => <header data-testid="top-nav" />,
}));

const memberFixture = (overrides: Partial<{
  id: string;
  workspace_id: string;
  user_id: string;
  role: "owner" | "admin" | "editor" | "viewer";
  created_at: string;
}> = {}) => ({
  id: "membership-1",
  workspace_id: "workspace-1",
  user_id: "user-1",
  role: "owner" as const,
  created_at: "2026-05-17T12:00:00Z",
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <WorkspaceMembers />
    </MemoryRouter>,
  );

describe("WorkspaceMembers", () => {
  beforeEach(() => {
    getActiveWorkspaceMock.mockReset();
    listWorkspaceMembersMock.mockReset();
    addWorkspaceMemberMock.mockReset();
    updateWorkspaceMemberRoleMock.mockReset();
    removeWorkspaceMemberMock.mockReset();
    getAuthenticatedUserProfileMock.mockReset();
    toastMock.mockReset();

    getAuthenticatedUserProfileMock.mockReturnValue({
      subject: "user-1",
      name: "Owner",
      email: null,
      avatar: null,
      role: null,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the forbidden message for editor/viewer roles", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "editor",
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText(/You do not have permission to view workspace members/i),
      ).toBeInTheDocument();
    });
    expect(listWorkspaceMembersMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/Add Member/i)).not.toBeInTheDocument();
  });

  it("lists members and lets admins add a new member", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([memberFixture()]);
    addWorkspaceMemberMock.mockResolvedValue(
      memberFixture({
        id: "membership-2",
        user_id: "user-2",
        role: "editor",
      }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("user-1")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/User ID/i), "user-2");
    await user.click(screen.getByRole("button", { name: /^Add$/ }));

    await waitFor(() => {
      expect(addWorkspaceMemberMock).toHaveBeenCalledWith("acme", {
        user_id: "user-2",
        role: "editor",
      });
    });
    await waitFor(() => {
      expect(screen.getByText("user-2")).toBeInTheDocument();
    });
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Member added successfully" }),
    );
  });

  it("blocks self-removal but allows removing other members", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([
      memberFixture({ user_id: "user-1", role: "owner" }),
      memberFixture({
        id: "membership-2",
        user_id: "user-2",
        role: "editor",
      }),
    ]);
    removeWorkspaceMemberMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("user-2")).toBeInTheDocument();
    });

    const removeButtons = screen.getAllByRole("button", { name: /Remove/i });
    expect(removeButtons).toHaveLength(1);

    await user.click(removeButtons[0]);

    await waitFor(() => {
      expect(removeWorkspaceMemberMock).toHaveBeenCalledWith("acme", "user-2");
    });
    await waitFor(() => {
      expect(screen.queryByText("user-2")).not.toBeInTheDocument();
    });
  });

  it("shows an error toast when loading fails", async () => {
    getActiveWorkspaceMock.mockRejectedValue(new Error("network"));

    renderPage();

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Failed to load workspace members",
          variant: "destructive",
        }),
      );
    });
  });
});
