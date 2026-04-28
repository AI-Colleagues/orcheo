// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useCredentialVault from "./use-credential-vault";

const { authFetchMock, toastMock } = vi.hoisted(() => ({
  authFetchMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/lib/auth-fetch", () => ({
  authFetch: authFetchMock,
}));

vi.mock("@/lib/config", () => ({
  buildBackendHttpUrl: (path: string, baseUrl?: string) =>
    `${baseUrl ?? "http://backend.test"}${path}`,
  getBackendBaseUrl: () => "http://backend.test",
}));

vi.mock("@/hooks/use-toast", () => ({
  toast: toastMock,
}));

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
    },
    ...init,
  });
}

function HookHarness() {
  const {
    credentials,
    isLoading,
    onDeleteCredential,
    onRevealCredentialSecret,
    onUpdateCredential,
  } = useCredentialVault();

  return (
    <div>
      <div data-testid="loading">{isLoading ? "loading" : "ready"}</div>
      <div data-testid="workflow-id">{credentials[0]?.workflowId ?? ""}</div>
      <button
        type="button"
        onClick={() => void onUpdateCredential("cred-1", { name: "Renamed" })}
      >
        update
      </button>
      <button
        type="button"
        onClick={() => void onRevealCredentialSecret("cred-1")}
      >
        reveal
      </button>
      <button type="button" onClick={() => void onDeleteCredential("cred-1")}>
        delete
      </button>
    </div>
  );
}

describe("useCredentialVault", () => {
  beforeEach(() => {
    authFetchMock.mockReset();
    toastMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("reuses the credential workflow id for scoped vault actions", async () => {
    authFetchMock
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: "cred-1",
            name: "Scoped credential",
            provider: "wecom",
            kind: "secret",
            workflow_id: "wf-archived",
            created_at: "2026-03-10T00:00:00Z",
            updated_at: "2026-03-10T00:00:00Z",
            last_rotated_at: null,
            owner: "tester",
            access: "scoped",
            status: "unknown",
            secret_preview: "••••••••",
          },
        ]),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: "cred-1",
          name: "Renamed",
          provider: "wecom",
          kind: "secret",
          workflow_id: "wf-archived",
          created_at: "2026-03-10T00:00:00Z",
          updated_at: "2026-03-11T00:00:00Z",
          last_rotated_at: null,
          owner: "tester",
          access: "scoped",
          status: "unknown",
          secret_preview: "••••••••",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: "cred-1",
          secret: "super-secret",
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    const user = userEvent.setup();
    render(<HookHarness />);

    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("ready"),
    );
    expect(screen.getByTestId("workflow-id")).toHaveTextContent("wf-archived");

    await user.click(screen.getByRole("button", { name: "update" }));
    await user.click(screen.getByRole("button", { name: "reveal" }));
    await user.click(screen.getByRole("button", { name: "delete" }));

    await waitFor(() => expect(authFetchMock).toHaveBeenCalledTimes(4));

    const updateRequest = authFetchMock.mock.calls[1];
    const updatePayload = JSON.parse(
      String((updateRequest[1] as RequestInit | undefined)?.body ?? ""),
    ) as { workflow_id?: string };
    expect(updatePayload.workflow_id).toBe("wf-archived");

    expect(String(authFetchMock.mock.calls[2][0])).toContain(
      "workflow_id=wf-archived",
    );
    expect(String(authFetchMock.mock.calls[3][0])).toContain(
      "workflow_id=wf-archived",
    );
  });
});
