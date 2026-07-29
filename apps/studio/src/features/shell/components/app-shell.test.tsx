import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AppShell from "./app-shell";

const useCredentialVaultMock = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/use-credential-vault", () => ({
  default: useCredentialVaultMock,
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  getAuthenticatedUserProfile: () => ({
    subject: "user-123",
    name: "Test User",
    email: "test@example.com",
    avatar: null,
    role: null,
  }),
}));

vi.mock("./app-sidebar", () => ({
  default: () => <nav>Sidebar</nav>,
}));

vi.mock("@features/workflow/components/dialogs/credentials-vault", () => ({
  default: () => <div>Credential vault</div>,
}));

describe("AppShell", () => {
  beforeEach(() => {
    useCredentialVaultMock.mockReset();
    useCredentialVaultMock.mockReturnValue({
      credentials: [],
      isLoading: false,
      onAddCredential: vi.fn(),
      onUpdateCredential: vi.fn(),
      onDeleteCredential: vi.fn(),
      onRevealCredentialSecret: vi.fn(),
    });
  });

  it("attributes credential mutations to the authenticated user", () => {
    render(
      <AppShell>
        <div>Content</div>
      </AppShell>,
    );

    expect(useCredentialVaultMock).toHaveBeenCalledWith({
      actorName: "user-123",
    });
  });
});
