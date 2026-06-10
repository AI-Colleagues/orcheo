import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CredentialsTable } from "./credentials-table";
import type { Credential } from "@features/workflow/types/credential-vault";

const credentials: Credential[] = [
  {
    id: "cred-1",
    name: "wecom_app_secret_eventually",
    provider: "WeCom",
    createdAt: "2026-02-20T00:00:00Z",
    updatedAt: "2026-02-20T00:00:00Z",
    access: "shared",
    status: "healthy",
    secrets: {
      token: "super-secret-value",
    },
  },
];

describe("CredentialsTable", () => {
  afterEach(() => {
    cleanup();
  });

  it("uses overflow-safe layout classes in the vault modal", () => {
    const { container } = render(
      <CredentialsTable
        credentials={credentials}
        searchQuery=""
        onDeleteCredential={vi.fn()}
      />,
    );

    expect(container.firstChild).toHaveClass("min-w-0");
    expect(container.firstChild).toHaveClass("overflow-hidden");
    expect(screen.getByRole("table")).toHaveClass("min-w-[820px]");
    expect(screen.getByText("Provider")).toBeInTheDocument();
    expect(screen.getByText("•••••••••••••••")).toHaveClass("w-[120px]");
    expect(screen.getByText("•••••••••••••••")).not.toHaveClass("truncate");
  });

  it("renders the full secret value without truncation once revealed", async () => {
    const user = userEvent.setup();

    render(
      <CredentialsTable
        credentials={credentials}
        searchQuery=""
        onDeleteCredential={vi.fn()}
      />,
    );

    await user.click(
      screen.getByLabelText("Show secret for wecom_app_secret_eventually"),
    );

    const secretValue = screen.getByText("super-secret-value");
    expect(secretValue).toBeInTheDocument();
    expect(secretValue).not.toHaveClass("truncate");
    expect(secretValue).toHaveClass("overflow-x-auto");
    expect(secretValue).toHaveClass("whitespace-nowrap");
  });

  it("prefills the current secret when opening edit", async () => {
    const user = userEvent.setup();
    const onRevealCredentialSecret = vi
      .fn()
      .mockResolvedValue("super-secret-value");

    render(
      <CredentialsTable
        credentials={[
          {
            ...credentials[0],
            secrets: undefined,
            secretPreview: "masked-preview",
          },
        ]}
        searchQuery=""
        onDeleteCredential={vi.fn()}
        onUpdateCredential={vi.fn()}
        onRevealCredentialSecret={onRevealCredentialSecret}
      />,
    );

    await user.click(
      screen.getByLabelText(
        "Credential actions for wecom_app_secret_eventually",
      ),
    );
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));

    expect(onRevealCredentialSecret).toHaveBeenCalledWith("cred-1");
    expect(await screen.findByLabelText("Secret")).toHaveValue(
      "super-secret-value",
    );
  });
});
