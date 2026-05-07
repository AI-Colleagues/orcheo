import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const {
  isAuthenticatedMock,
  getAuthTokensMock,
  startOidcLoginMock,
  tryRefreshTokensMock,
} = vi.hoisted(() => ({
  isAuthenticatedMock: vi.fn(),
  getAuthTokensMock: vi.fn(),
  startOidcLoginMock: vi.fn().mockResolvedValue(undefined),
  tryRefreshTokensMock: vi.fn().mockResolvedValue(false),
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  isAuthenticated: isAuthenticatedMock,
  getAuthTokens: getAuthTokensMock,
}));

vi.mock("@features/auth/lib/oidc-client", () => ({
  startOidcLogin: startOidcLoginMock,
  tryRefreshTokens: tryRefreshTokensMock,
}));

describe("RequireAuth", () => {
  beforeEach(() => {
    isAuthenticatedMock.mockReset();
    isAuthenticatedMock.mockReturnValue(false);
    getAuthTokensMock.mockReset();
    getAuthTokensMock.mockReturnValue(null);
    startOidcLoginMock.mockReset();
    startOidcLoginMock.mockResolvedValue(undefined);
    tryRefreshTokensMock.mockReset();
    tryRefreshTokensMock.mockResolvedValue(false);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const renderWithAuth = async (issuerValue: string | undefined) => {
    vi.stubEnv("VITE_ORCHEO_AUTH_ISSUER", issuerValue ?? "");

    // Re-import to pick up the new env value
    vi.resetModules();
    const { default: RequireAuth } =
      await import("@features/auth/components/require-auth");

    return render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<RequireAuth />}>
            <Route path="/" element={<div>protected content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  };

  it("allows access when auth issuer is empty", async () => {
    await renderWithAuth("");
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("allows access when auth issuer is undefined", async () => {
    await renderWithAuth(undefined);
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("allows access when auth issuer is a placeholder string", async () => {
    await renderWithAuth("__VITE_ORCHEO_AUTH_ISSUER__");
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("redirects to Auth0 when auth issuer is a valid URL and not authenticated", async () => {
    await renderWithAuth("https://auth.example.com");
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(startOidcLoginMock).toHaveBeenCalledOnce());
    expect(startOidcLoginMock).toHaveBeenCalledWith(
      expect.objectContaining({ redirectTo: "/" }),
    );
  });

  it("allows access when auth issuer is valid and user is authenticated", async () => {
    isAuthenticatedMock.mockReturnValue(true);
    await renderWithAuth("https://auth.example.com");
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("allows access when auth issuer is a non-URL string", async () => {
    await renderWithAuth("not-a-url");
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });

  it("redirects to Auth0 with http localhost issuer when not authenticated", async () => {
    await renderWithAuth("http://localhost:8080");
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    await waitFor(() => expect(startOidcLoginMock).toHaveBeenCalledOnce());
  });

  it("silently refreshes when access token is expired but refresh token is stored", async () => {
    getAuthTokensMock.mockReturnValue({ refreshToken: "rt_valid" });
    tryRefreshTokensMock.mockResolvedValue(true);

    await renderWithAuth("https://auth.example.com");

    await waitFor(() => expect(tryRefreshTokensMock).toHaveBeenCalledOnce());
    expect(screen.getByText("protected content")).toBeInTheDocument();
    expect(startOidcLoginMock).not.toHaveBeenCalled();
  });

  it("redirects to Auth0 when token refresh fails", async () => {
    getAuthTokensMock.mockReturnValue({ refreshToken: "rt_expired" });
    tryRefreshTokensMock.mockResolvedValue(false);

    await renderWithAuth("https://auth.example.com");

    await waitFor(() => expect(tryRefreshTokensMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(startOidcLoginMock).toHaveBeenCalledOnce());
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("does not attempt refresh when no tokens are stored", async () => {
    getAuthTokensMock.mockReturnValue(null);

    await renderWithAuth("https://auth.example.com");

    await waitFor(() => expect(startOidcLoginMock).toHaveBeenCalledOnce());
    expect(tryRefreshTokensMock).not.toHaveBeenCalled();
  });
});
