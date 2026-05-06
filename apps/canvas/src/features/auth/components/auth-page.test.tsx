import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AuthPage from "@features/auth/components/auth-page";

const { startOidcLoginMock } = vi.hoisted(() => ({
  startOidcLoginMock: vi.fn(),
}));

vi.mock("@features/auth/lib/oidc-client", () => ({
  startOidcLogin: startOidcLoginMock,
}));

describe("AuthPage", () => {
  beforeEach(() => {
    startOidcLoginMock.mockReset();
    startOidcLoginMock.mockResolvedValue(undefined);
    vi.stubEnv("VITE_ORCHEO_AUTH_ISSUER", "https://auth.example.com");
    vi.stubEnv("VITE_ORCHEO_AUTH_CLIENT_ID", "client-id");
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("uses invite params from redirect state when login search is empty", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/login",
            state: {
              from: "/invite?invitation=invite_123&organization=org_abc&organization_name=Acme",
            },
          },
        ]}
      >
        <AuthPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Google" }));

    await waitFor(() => {
      expect(startOidcLoginMock).toHaveBeenCalledWith(
        expect.objectContaining({
          provider: "google",
          redirectTo:
            "/invite?invitation=invite_123&organization=org_abc&organization_name=Acme",
          invitation: "invite_123",
          organization: "org_abc",
          organizationName: "Acme",
        }),
      );
    });
  });

  it("prefers login query invite params over redirect state", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/login",
            search:
              "?invitation=invite_login&organization=org_login&organization_name=LoginOrg",
            state: {
              from: "/invite?invitation=invite_state&organization=org_state&organization_name=StateOrg",
            },
          },
        ]}
      >
        <AuthPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "GitHub" }));

    await waitFor(() => {
      expect(startOidcLoginMock).toHaveBeenCalledWith(
        expect.objectContaining({
          provider: "github",
          invitation: "invite_login",
          organization: "org_login",
          organizationName: "LoginOrg",
        }),
      );
    });
  });

  it("ignores query-like content inside redirect hash fragments", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: "/login",
            state: {
              from: "/invite#fragment?invitation=invite_from_hash",
            },
          },
        ]}
      >
        <AuthPage />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Google" }));

    await waitFor(() => {
      expect(startOidcLoginMock).toHaveBeenCalledWith(
        expect.objectContaining({
          provider: "google",
          redirectTo: "/invite#fragment?invitation=invite_from_hash",
        }),
      );
    });
    expect(startOidcLoginMock).toHaveBeenCalledWith(
      expect.not.objectContaining({
        invitation: "invite_from_hash",
      }),
    );
  });
});
