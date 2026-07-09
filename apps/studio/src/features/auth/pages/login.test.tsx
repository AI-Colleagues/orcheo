import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import Login from "./login";

const navigateMock = vi.fn();
const locationMock = { pathname: "/login", search: "", hash: "", state: null };

vi.mock("react-router-dom", () => ({
  useLocation: () => locationMock,
  useNavigate: () => navigateMock,
}));

const startEmailChallenge = vi.fn(() => Promise.resolve());
const verifyEmailCode = vi.fn(() => Promise.resolve(undefined));
const isAuthenticated = vi.fn(() => false);

vi.mock("@features/auth/lib/auth-api", () => ({
  startEmailChallenge: (...args: unknown[]) => startEmailChallenge(...args),
  verifyEmailCode: (...args: unknown[]) => verifyEmailCode(...args),
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  isAuthenticated: () => isAuthenticated(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  isAuthenticated.mockReturnValue(false);
  locationMock.state = null;
  locationMock.search = "";
});

describe("Login", () => {
  it("redirects authenticated sessions to the landing page", async () => {
    isAuthenticated.mockReturnValue(true);

    render(<Login />);

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
    expect(startEmailChallenge).not.toHaveBeenCalled();
  });

  it("preserves the sanitized redirect target for authenticated sessions", async () => {
    isAuthenticated.mockReturnValue(true);
    locationMock.search = "?redirect=/workflows/abc";

    render(<Login />);

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/workflows/abc", {
        replace: true,
      }),
    );
    expect(startEmailChallenge).not.toHaveBeenCalled();
  });

  it("sends an email challenge then verifies the OTP code", async () => {
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );

    await waitFor(() =>
      expect(startEmailChallenge).toHaveBeenCalledWith(
        "alice@example.com",
        "login",
        "/",
      ),
    );
    expect(await screen.findByLabelText(/sign-in code/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/sign-in code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    await waitFor(() =>
      expect(verifyEmailCode).toHaveBeenCalledWith(
        "alice@example.com",
        "123456",
      ),
    );
    expect(navigateMock).toHaveBeenCalledWith("/", { replace: true });
  });

  it("redirects to the sanitized post-login target", async () => {
    locationMock.state = { from: "/workflows" } as never;
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "999000");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/workflows", {
        replace: true,
      }),
    );
  });

  it("surfaces an error when sending the email fails", async () => {
    startEmailChallenge.mockRejectedValueOnce(new Error("rate limited"));
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );

    expect(await screen.findByText(/rate limited/i)).toBeInTheDocument();
  });

  it("falls back to the redirect query param", async () => {
    locationMock.search = "?redirect=/chat/abc";
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "999000");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/chat/abc", { replace: true }),
    );
  });

  it("falls back to the from query param", async () => {
    locationMock.search = "?from=/chat/ws/team/ws/typesetter";
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "999000");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/chat/ws/team/ws/typesetter", {
        replace: true,
      }),
    );
  });

  it("ignores external or protocol-relative redirect targets", async () => {
    locationMock.search = "?redirect=https://evil.example.com";
    locationMock.state = { from: "//evil.example.com" } as never;
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "999000");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
  });
});
