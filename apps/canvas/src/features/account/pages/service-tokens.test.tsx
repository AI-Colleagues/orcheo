import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ServiceTokens from "./service-tokens";

const {
  getActiveWorkspaceMock,
  listServiceTokensMock,
  createServiceTokenMock,
  rotateServiceTokenMock,
  revokeServiceTokenMock,
  toastMock,
} = vi.hoisted(() => ({
  getActiveWorkspaceMock: vi.fn(),
  listServiceTokensMock: vi.fn(),
  createServiceTokenMock: vi.fn(),
  rotateServiceTokenMock: vi.fn(),
  revokeServiceTokenMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getActiveWorkspace: getActiveWorkspaceMock,
  listServiceTokens: listServiceTokensMock,
  createServiceToken: createServiceTokenMock,
  rotateServiceToken: rotateServiceTokenMock,
  revokeServiceToken: revokeServiceTokenMock,
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

interface TokenOverrides {
  identifier?: string;
  secret?: string | null;
  secret_preview?: string | null;
  scopes?: string[];
  workspace_ids?: string[];
  issued_at?: string | null;
  expires_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  rotated_to?: string | null;
}

const tokenFixture = (overrides: TokenOverrides = {}) => ({
  identifier: "tok-1",
  secret: null,
  secret_preview: "wxyz",
  scopes: ["workflows:read"],
  workspace_ids: ["workspace-1"],
  issued_at: "2026-05-17T12:00:00Z",
  expires_at: null,
  last_used_at: null,
  use_count: 0,
  revoked_at: null,
  revocation_reason: null,
  rotated_to: null,
  message: null,
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <ServiceTokens />
    </MemoryRouter>,
  );

describe("ServiceTokens", () => {
  beforeEach(() => {
    getActiveWorkspaceMock.mockReset();
    listServiceTokensMock.mockReset();
    createServiceTokenMock.mockReset();
    rotateServiceTokenMock.mockReset();
    revokeServiceTokenMock.mockReset();
    toastMock.mockReset();

    getActiveWorkspaceMock.mockResolvedValue({
      workspace_id: "workspace-1",
      slug: "acme",
      name: "Acme",
      role: "editor",
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists the workspace's tokens and shows the scoping notice", async () => {
    listServiceTokensMock.mockResolvedValue({
      tokens: [tokenFixture()],
      total: 1,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("tok-1")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/cannot access any other workspace/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("••••••••wxyz")).toBeInTheDocument();
  });

  it("creates a key from the dialog and reveals the secret once", async () => {
    listServiceTokensMock.mockResolvedValue({ tokens: [], total: 0 });
    createServiceTokenMock.mockResolvedValue(
      tokenFixture({ identifier: "tok-new", secret: "super-secret-value" }),
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText(/No API keys for this workspace yet/i),
      ).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Create a new key/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("workflows:read"));
    await user.click(
      within(dialog).getByRole("button", { name: /Create a new key/i }),
    );

    await waitFor(() => {
      expect(createServiceTokenMock).toHaveBeenCalledWith(
        expect.objectContaining({ scopes: ["workflows:read"] }),
      );
    });
    expect(await screen.findByText("super-secret-value")).toBeInTheDocument();
    expect(screen.getByText("API key created")).toBeInTheDocument();
  });

  it("revokes a token after confirming in the dialog", async () => {
    listServiceTokensMock.mockResolvedValue({
      tokens: [tokenFixture()],
      total: 1,
    });
    revokeServiceTokenMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("tok-1")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Revoke/i }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(revokeServiceTokenMock).toHaveBeenCalledWith(
        "tok-1",
        "Revoked via Canvas",
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("tok-1")).not.toBeInTheDocument();
    });
  });

  it("keeps the token when the revoke dialog is cancelled", async () => {
    listServiceTokensMock.mockResolvedValue({
      tokens: [tokenFixture()],
      total: 1,
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("tok-1")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /Revoke/i }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: /Cancel/i }));

    expect(revokeServiceTokenMock).not.toHaveBeenCalled();
    expect(screen.getByText("tok-1")).toBeInTheDocument();
  });

  it("shows an error toast when loading fails", async () => {
    listServiceTokensMock.mockRejectedValue(new Error("network"));

    renderPage();

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Failed to load API keys",
          variant: "destructive",
        }),
      );
    });
  });
});
