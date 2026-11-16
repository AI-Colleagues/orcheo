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
    "email:accounts:read",
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
      screen.getByText(
        "Create and revoke service tokens through a private form",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/email accounts are linked/)).toBeTruthy();
    expect(screen.getByText("custom:scope")).toBeTruthy();
    // Each known scope shows its code under its description.
    expect(screen.getByText("vault:write")).toBeTruthy();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes).toHaveLength(pendingRequest.scopes.length);
    expect(boxes.every((box) => box.checked && !box.disabled)).toBe(true);
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
    expect(JSON.parse(init.body)).toEqual(
      approve ? { approve, scopes: pendingRequest.scopes } : { approve },
    );
  });

  it("grants only the permissions left ticked", async () => {
    const user = userEvent.setup();
    authFetch
      .mockResolvedValueOnce(jsonResponse(pendingRequest))
      .mockResolvedValueOnce(jsonResponse({ redirect_url: "http://cb" }));

    render(<OAuthConsent />);
    await user.click(
      await screen.findByRole("checkbox", { name: /delete credentials/ }),
    );
    await user.click(screen.getByRole("checkbox", { name: /service tokens/ }));
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://cb"));
    expect(JSON.parse(authFetch.mock.calls[1][1].body)).toEqual({
      approve: true,
      scopes: [
        "workflows:read",
        "workspaces:write",
        "email:accounts:read",
        "custom:scope",
      ],
    });
  });

  it("keeps a permission another kept one includes", async () => {
    const user = userEvent.setup();
    authFetch
      .mockResolvedValueOnce(
        jsonResponse({
          ...pendingRequest,
          scopes: ["email:accounts:read", "email:read"],
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ redirect_url: "http://cb" }));

    render(<OAuthConsent />);
    const accounts = (await screen.findByRole("checkbox", {
      name: /email accounts are linked/,
    })) as HTMLInputElement;
    const mail = screen.getByRole("checkbox", {
      name: /Read mail/,
    }) as HTMLInputElement;

    expect(accounts.checked && accounts.disabled).toBe(true);
    expect(screen.getByText("Included with email:read")).toBeTruthy();

    await user.click(mail);
    expect(accounts.disabled).toBe(false);
    await user.click(accounts);
    expect(accounts.checked).toBe(false);
    expect(
      (screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    // Ticking mail again brings the mailbox listing back with it.
    await user.click(mail);
    expect(accounts.checked && accounts.disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://cb"));
    expect(JSON.parse(authFetch.mock.calls[1][1].body).scopes).toEqual([
      "email:accounts:read",
      "email:read",
    ]);
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
