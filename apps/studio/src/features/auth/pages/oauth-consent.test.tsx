import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import OAuthConsent from "./oauth-consent";

let searchParams = new URLSearchParams("request=req-1");

vi.mock("react-router-dom", () => ({
  useSearchParams: () => [searchParams],
}));

const authFetch = vi.fn();
vi.mock("@/lib/auth-fetch", () => ({
  authFetch: (...args: unknown[]) => authFetch(...args),
}));

vi.mock("@/lib/config", () => ({
  buildBackendHttpUrl: (path: string) => `http://backend${path}`,
}));

const profile = vi.fn(() => ({ email: "dana@example.com", name: "Dana" }));
vi.mock("@features/auth/lib/auth-session", () => ({
  getAuthenticatedUserProfile: () => profile(),
}));

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const pendingRequest = {
  request_id: "req-1",
  client_id: "client-1",
  client_name: "Claude Code",
  client_uri: null,
  redirect_host: "127.0.0.1:33418",
  scopes: [
    "workflows:read",
    "vault:write",
    "workspaces:write",
    "admin:tokens:write",
    "custom:scope",
  ],
};

const assign = vi.fn();

beforeEach(() => {
  vi.stubGlobal("location", { ...globalThis.location, assign });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  searchParams = new URLSearchParams("request=req-1");
});

describe("OAuthConsent", () => {
  it("describes the requesting client and its permissions", async () => {
    authFetch.mockResolvedValueOnce(jsonResponse(pendingRequest));

    render(<OAuthConsent />);

    expect(await screen.findByText("Authorize Claude Code")).toBeTruthy();
    expect(screen.getByText("dana@example.com")).toBeTruthy();
    expect(screen.getByText("View workflows, runs and traces")).toBeTruthy();
    expect(screen.getByText("Add, change and delete credentials")).toBeTruthy();
    expect(
      screen.getByText("Create and delete workspaces and manage membership"),
    ).toBeTruthy();
    expect(
      screen.getByText("Create and revoke service tokens through a private form"),
    ).toBeTruthy();
    expect(screen.getByText("custom:scope")).toBeTruthy();
    expect(screen.getByText("127.0.0.1:33418")).toBeTruthy();
    expect(authFetch).toHaveBeenCalledWith(
      "http://backend/api/oauth/requests/req-1",
      {},
      { includeWorkspaceHeaders: false },
    );
  });

  it.each([
    [true, "http://127.0.0.1:33418/callback?code=abc&state=s"],
    [false, "http://127.0.0.1:33418/callback?error=access_denied"],
  ])("posts the decision (approve=%s) and redirects", async (approve, url) => {
    const user = userEvent.setup();
    authFetch
      .mockResolvedValueOnce(jsonResponse(pendingRequest))
      .mockResolvedValueOnce(jsonResponse({ redirect_url: url }));

    render(<OAuthConsent />);
    await user.click(
      await screen.findByRole("button", { name: approve ? "Approve" : "Deny" }),
    );

    await waitFor(() => expect(assign).toHaveBeenCalledWith(url));
    const [decisionUrl, init] = authFetch.mock.calls[1];
    expect(decisionUrl).toBe(
      "http://backend/api/oauth/requests/req-1/decision",
    );
    expect(JSON.parse(init.body)).toEqual({ approve });
  });

  it("shows why a decision failed", async () => {
    const user = userEvent.setup();
    authFetch
      .mockResolvedValueOnce(jsonResponse(pendingRequest))
      .mockResolvedValueOnce(
        jsonResponse(
          { detail: "This authorization request has expired." },
          404,
        ),
      );

    render(<OAuthConsent />);
    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText("This authorization request has expired."),
    ).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });

  it("explains expired or unknown requests", async () => {
    authFetch.mockResolvedValueOnce(new Response("oops", { status: 500 }));

    render(<OAuthConsent />);

    expect(
      await screen.findByText("This authorization request is invalid."),
    ).toBeTruthy();
  });

  it("rejects links without a request id", async () => {
    searchParams = new URLSearchParams();
    profile.mockReturnValueOnce(null as never);

    render(<OAuthConsent />);

    expect(
      await screen.findByText("This authorization link is incomplete."),
    ).toBeTruthy();
    expect(authFetch).not.toHaveBeenCalled();
  });

  it("falls back to generic wording when details are missing", async () => {
    const user = userEvent.setup();
    profile.mockReturnValue(null as never);
    authFetch
      .mockResolvedValueOnce(
        jsonResponse({ ...pendingRequest, client_name: null }),
      )
      .mockRejectedValueOnce("network down");

    render(<OAuthConsent />);

    expect(await screen.findByText("Authorize An application")).toBeTruthy();
    expect(screen.getByText("you")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Authorization failed.")).toBeTruthy();
  });

  it("reports load failures that are not errors", async () => {
    authFetch.mockRejectedValueOnce("offline");

    render(<OAuthConsent />);

    expect(
      await screen.findByText("This authorization request is invalid."),
    ).toBeTruthy();
  });
});
