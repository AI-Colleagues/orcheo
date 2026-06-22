import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import Login from "./login";

const useLocationMock = vi.fn();

vi.mock("react-router-dom", () => ({
  useLocation: () => useLocationMock(),
}));

vi.mock("@features/auth/components/auto-login", () => ({
  default: ({ redirectTo }: { redirectTo?: string }) => (
    <div data-testid="auto-login" data-redirect={redirectTo ?? ""} />
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderLogin = (location: {
  pathname?: string;
  search?: string;
  hash?: string;
  state?: unknown;
}): string | null => {
  useLocationMock.mockReturnValue({
    pathname: "/login",
    search: "",
    hash: "",
    state: null,
    ...location,
  });
  render(<Login />);
  return screen.getByTestId("auto-login").getAttribute("data-redirect");
};

describe("Login", () => {
  it("forwards the redirect target from router state", () => {
    expect(
      renderLogin({ state: { from: "/chat/ws/team/ws/typesetter" } }),
    ).toBe("/chat/ws/team/ws/typesetter");
  });

  it("falls back to the redirect query param", () => {
    expect(renderLogin({ search: "?redirect=/chat/abc" })).toBe("/chat/abc");
  });

  it("falls back to the from query param", () => {
    expect(renderLogin({ search: "?from=/chat/ws/team/ws/typesetter" })).toBe(
      "/chat/ws/team/ws/typesetter",
    );
  });

  it("ignores external or protocol-relative redirect targets", () => {
    expect(
      renderLogin({
        search: "?redirect=https://evil.example.com",
        state: { from: "//evil.example.com" },
      }),
    ).toBe("");
  });
});
