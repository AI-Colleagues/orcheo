import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import AuthVerify from "./verify";

const navigateMock = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
  useSearchParams: () => [searchParams],
}));

const verifyEmailToken = vi.fn(() => Promise.resolve(undefined));

vi.mock("@features/auth/lib/auth-api", () => ({
  verifyEmailToken: (...args: unknown[]) => verifyEmailToken(...args),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  searchParams = new URLSearchParams();
});

describe("AuthVerify", () => {
  it("redeems the token and navigates to the sanitized redirect", async () => {
    searchParams = new URLSearchParams({
      token: "magic",
      redirect: "/workflows",
    });
    render(<AuthVerify />);

    await waitFor(() => expect(verifyEmailToken).toHaveBeenCalledWith("magic"));
    expect(navigateMock).toHaveBeenCalledWith("/workflows", { replace: true });
  });

  it("falls back to root for an unsafe redirect", async () => {
    searchParams = new URLSearchParams({
      token: "magic",
      redirect: "//evil.com",
    });
    render(<AuthVerify />);

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
  });

  it("shows an error when the token is missing", () => {
    searchParams = new URLSearchParams();
    render(<AuthVerify />);

    expect(screen.getByText(/missing its token/i)).toBeInTheDocument();
    expect(verifyEmailToken).not.toHaveBeenCalled();
  });

  it("shows an error when verification fails", async () => {
    searchParams = new URLSearchParams({ token: "bad" });
    verifyEmailToken.mockRejectedValueOnce(new Error("expired link"));
    render(<AuthVerify />);

    expect(await screen.findByText(/expired link/i)).toBeInTheDocument();
  });
});
