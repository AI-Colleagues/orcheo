import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceMembers from "./workspace-members";

const {
  getActiveWorkspaceMock,
  listWorkspaceMembersMock,
  listWorkspaceInvitationsMock,
  createWorkspaceInvitationMock,
  revokeWorkspaceInvitationMock,
  updateWorkspaceMemberRoleMock,
  removeWorkspaceMemberMock,
  getAuthenticatedUserProfileMock,
  toastMock,
} = vi.hoisted(() => ({
  getActiveWorkspaceMock: vi.fn(),
  listWorkspaceMembersMock: vi.fn(),
  listWorkspaceInvitationsMock: vi.fn(),
  createWorkspaceInvitationMock: vi.fn(),
  revokeWorkspaceInvitationMock: vi.fn(),
  updateWorkspaceMemberRoleMock: vi.fn(),
  removeWorkspaceMemberMock: vi.fn(),
  getAuthenticatedUserProfileMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getActiveWorkspace: getActiveWorkspaceMock,
  listWorkspaceMembers: listWorkspaceMembersMock,
  listWorkspaceInvitations: listWorkspaceInvitationsMock,
  createWorkspaceInvitation: createWorkspaceInvitationMock,
  revokeWorkspaceInvitation: revokeWorkspaceInvitationMock,
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

vi.mock("@features/account/components/external-agents-section", () => ({
  default: () => <section data-testid="external-agents-section" />,
}));

const memberFixture = (
  overrides: Partial<{
    id: string;
    workspace_id: string;
    user_id: string;
    email: string | null;
    user_name: string | null;
    role: "owner" | "admin" | "editor" | "viewer";
    created_at: string;
  }> = {},
) => ({
  id: "membership-1",
  workspace_id: "workspace-1",
  user_id: "user-1",
  role: "owner" as const,
  created_at: "2026-05-17T12:00:00Z",
  ...overrides,
});

const invitationFixture = (
  overrides: Partial<{
    id: string;
    workspace_id: string;
    email: string;
    role: "owner" | "admin" | "editor" | "viewer";
    status: "pending" | "accepted" | "revoked";
    expires_at: string;
  }> = {},
) => ({
  id: "invitation-1",
  workspace_id: "workspace-1",
  email: "invitee@example.com",
  role: "editor" as const,
  status: "pending" as const,
  invited_by: "user-1",
  accepted_by: null,
  created_at: "2026-05-17T12:00:00Z",
  expires_at: "2026-05-20T12:00:00Z",
  accepted_at: null,
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
    listWorkspaceInvitationsMock.mockReset();
    createWorkspaceInvitationMock.mockReset();
    revokeWorkspaceInvitationMock.mockReset();
    updateWorkspaceMemberRoleMock.mockReset();
    removeWorkspaceMemberMock.mockReset();
    getAuthenticatedUserProfileMock.mockReset();
    toastMock.mockReset();

    listWorkspaceInvitationsMock.mockResolvedValue([]);
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
        screen.getByText(
          /You do not have permission to view workspace members/i,
        ),
      ).toBeInTheDocument();
    });
    expect(listWorkspaceMembersMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/Invite Member/i)).not.toBeInTheDocument();
  });

  it("lists members and lets admins invite a new member by email", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([memberFixture()]);
    createWorkspaceInvitationMock.mockResolvedValue(
      invitationFixture({ email: "new@example.com", role: "editor" }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("user-1")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/Email/i), "new@example.com");
    await user.click(screen.getByRole("button", { name: /Send invite/i }));

    await waitFor(() => {
      expect(createWorkspaceInvitationMock).toHaveBeenCalledWith("acme", {
        email: "new@example.com",
        role: "editor",
      });
    });
    // The new pending invitation appears in the list.
    await waitFor(() => {
      expect(screen.getByText("new@example.com")).toBeInTheDocument();
    });
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Invitation sent" }),
    );
  });

  it("validates the email before sending an invitation", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([memberFixture()]);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("user-1")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/Email/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: /Send invite/i }));

    expect(
      screen.getByText(/Enter a valid email address/i),
    ).toBeInTheDocument();
    expect(createWorkspaceInvitationMock).not.toHaveBeenCalled();
  });

  it("lists pending invitations and lets admins revoke them", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([memberFixture()]);
    listWorkspaceInvitationsMock.mockResolvedValue([
      invitationFixture({ email: "pending@example.com" }),
    ]);
    revokeWorkspaceInvitationMock.mockResolvedValue(
      invitationFixture({ email: "pending@example.com", status: "revoked" }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("pending@example.com")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Revoke/i }));

    await waitFor(() => {
      expect(revokeWorkspaceInvitationMock).toHaveBeenCalledWith(
        "acme",
        "invitation-1",
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("pending@example.com")).not.toBeInTheDocument();
    });
  });

  it("shows the email and name when present, keeping the subject visible", async () => {
    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "admin",
    });
    listWorkspaceMembersMock.mockResolvedValue([
      memberFixture({
        user_id: "auth0|user-1",
        email: "owner@example.com",
        user_name: "Owner Name",
      }),
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    });
    expect(screen.getByText("Owner Name")).toBeInTheDocument();
    // The opaque subject is still shown as a secondary identifier.
    expect(screen.getByText("auth0|user-1")).toBeInTheDocument();
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
