import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AddCredentialDialog } from "./add-credential-dialog";
import { EditCredentialDialog } from "./edit-credential-dialog";
import CredentialsVault from "./credentials-vault";
import type { Credential } from "@features/workflow/types/credential-vault";

const credential: Credential = {
  id: "cred-1",
  name: "Canvas API",
  provider: "openai",
  createdAt: "2026-03-10T00:00:00Z",
  updatedAt: "2026-03-10T00:00:00Z",
  access: "shared",
  secrets: {
    secret: "super-secret-value",
  },
};

describe("Credential dialogs", () => {
  const hasPointerCapture = HTMLElement.prototype.hasPointerCapture;
  const setPointerCapture = HTMLElement.prototype.setPointerCapture;
  const releasePointerCapture = HTMLElement.prototype.releasePointerCapture;
  const scrollIntoView = HTMLElement.prototype.scrollIntoView;

  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      configurable: true,
      value: vi.fn(() => false),
    });
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
  });

  afterAll(() => {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      configurable: true,
      value: hasPointerCapture,
    });
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      configurable: true,
      value: setPointerCapture,
    });
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      configurable: true,
      value: releasePointerCapture,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
  });

  it("offers scoped and shared access options in the add dialog", async () => {
    const user = userEvent.setup();

    render(<AddCredentialDialog />);

    await user.click(screen.getByRole("button", { name: "Add Credential" }));
    expect(screen.getByRole("combobox", { name: "Access" })).toHaveTextContent(
      "Shared",
    );
    await user.click(screen.getByRole("combobox", { name: "Access" }));

    expect(screen.getByRole("option", { name: "Scoped" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Shared" })).toBeInTheDocument();
  });

  it("submits shared access by default in the add dialog", async () => {
    const user = userEvent.setup();
    const onAddCredential = vi.fn().mockResolvedValue(undefined);

    render(<AddCredentialDialog onAddCredential={onAddCredential} />);

    await user.click(screen.getByRole("button", { name: "Add Credential" }));
    await user.type(screen.getByLabelText("Name"), "Canvas Test Credential");
    await user.type(screen.getByLabelText("Secret"), "test-secret-123");
    await user.click(screen.getByRole("button", { name: "Save Credential" }));

    expect(onAddCredential).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Canvas Test Credential",
        access: "shared",
        secrets: { secret: "test-secret-123" },
      }),
    );
  });

  it("uses a scrollable vault list container", () => {
    render(<CredentialsVault credentials={[credential]} />);

    expect(screen.getByTestId("credentials-vault-list")).toHaveClass(
      "min-h-0",
      "flex-1",
      "overflow-y-auto",
    );
  });

  it("shows an explicit Show button in the edit dialog", async () => {
    const user = userEvent.setup();

    render(
      <EditCredentialDialog
        credential={credential}
        open
        onOpenChange={() => undefined}
      />,
    );

    const secretInput = screen.getByLabelText("Secret");
    expect(secretInput).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "Show secret value" }));
    expect(secretInput).toHaveAttribute("type", "text");
    expect(
      screen.getByRole("button", { name: "Hide secret value" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Access" }));
    expect(screen.getByRole("option", { name: "Scoped" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Shared" })).toBeInTheDocument();
  });

  it("shows the scoped workflow id in the edit dialog", () => {
    render(
      <EditCredentialDialog
        credential={{
          ...credential,
          access: "scoped",
          workflowId: "wf-42",
        }}
        open
        onOpenChange={() => undefined}
      />,
    );

    const workflowIdNode = screen.getByTestId("edit-credential-workflow-id");
    expect(workflowIdNode).toHaveTextContent("wf-42");
  });

  it("hides the workflow id when the credential is shared", () => {
    render(
      <EditCredentialDialog
        credential={{ ...credential, access: "shared", workflowId: "wf-42" }}
        open
        onOpenChange={() => undefined}
      />,
    );

    expect(
      screen.queryByTestId("edit-credential-workflow-id"),
    ).not.toBeInTheDocument();
  });
});
