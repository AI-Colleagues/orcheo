import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import Profile from "./profile";

const session = vi.hoisted(() => ({
  tokens: vi.fn((): unknown => null),
  profile: vi.fn((): unknown => null),
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  getAuthTokens: () => session.tokens(),
  getAuthenticatedUserProfile: () => session.profile(),
}));

vi.mock("./profile/components/passkeys-card", () => ({
  PasskeysCard: ({ email }: { email: string | null }) => (
    <div>Passkeys for {email ?? "unknown"}</div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  session.tokens.mockReturnValue(null);
  session.profile.mockReturnValue(null);
});

describe("Profile", () => {
  it("shows passkeys for signed-in accounts", () => {
    session.tokens.mockReturnValue({ accessToken: "token" });
    session.profile.mockReturnValue({
      subject: "u1",
      name: "Alice",
      email: "alice@example.com",
      avatar: null,
      role: null,
    });

    render(<Profile />);

    expect(
      screen.getByText("Passkeys for alice@example.com"),
    ).toBeInTheDocument();
  });

  it("hides passkeys without a first-party session", () => {
    render(<Profile />);

    expect(screen.getByText("Local Developer")).toBeInTheDocument();
    expect(screen.queryByText(/passkeys for/i)).not.toBeInTheDocument();
  });

  it("passes no email when the session has none", () => {
    session.tokens.mockReturnValue({ accessToken: "token" });

    render(<Profile />);

    expect(screen.getByText("Passkeys for unknown")).toBeInTheDocument();
  });
});
