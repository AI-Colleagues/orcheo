import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthApiError } from "@features/auth/lib/auth-api";
import type { PasskeyList } from "@features/auth/lib/passkeys";
import { PasskeysCard } from "./passkeys-card";

const mocks = vi.hoisted(() => ({
  supported: vi.fn(() => true),
  listPasskeys: vi.fn(),
  registerPasskey: vi.fn(),
  renamePasskey: vi.fn(),
  deletePasskey: vi.fn(),
  syncAcceptedPasskeys: vi.fn(),
  startEmailChallenge: vi.fn(),
  verifyEmailCode: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => mocks.supported(),
  browserSupportsWebAuthnAutofill: () => Promise.resolve(false),
  platformAuthenticatorIsAvailable: () => Promise.resolve(false),
  WebAuthnAbortService: { cancelCeremony: vi.fn() },
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
  sendSignal: vi.fn(),
}));

vi.mock("@features/auth/lib/passkeys", () => ({
  listPasskeys: () => mocks.listPasskeys(),
  registerPasskey: () => mocks.registerPasskey(),
  renamePasskey: (...args: unknown[]) => mocks.renamePasskey(...args),
  deletePasskey: (...args: unknown[]) => mocks.deletePasskey(...args),
  syncAcceptedPasskeys: (...args: unknown[]) =>
    mocks.syncAcceptedPasskeys(...args),
}));

vi.mock("@features/auth/lib/auth-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@features/auth/lib/auth-api")>()),
  startEmailChallenge: (...args: unknown[]) =>
    mocks.startEmailChallenge(...args),
  verifyEmailCode: (...args: unknown[]) => mocks.verifyEmailCode(...args),
}));

vi.mock("@/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => mocks.toast(...args),
}));

const laptop = {
  id: "p-1",
  name: "Laptop",
  credential_id: "cred-1",
  transports: ["internal"],
  backup_eligible: true,
  backed_up: true,
  created_at: "2026-10-01T12:00:00Z",
  last_used_at: null,
};

const passkeyList = (overrides: Partial<PasskeyList> = {}): PasskeyList => ({
  rp_id: "orcheo.example.com",
  user_handle: "handle",
  passkeys: [laptop],
  ...overrides,
});

const reauthRequired = () =>
  new AuthApiError(
    "Confirm it's you by signing in again before adding a passkey.",
    403,
    "auth.reauthentication_required",
  );

beforeEach(() => {
  mocks.supported.mockReturnValue(true);
  mocks.listPasskeys.mockResolvedValue(passkeyList());
  mocks.startEmailChallenge.mockResolvedValue(undefined);
  mocks.verifyEmailCode.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PasskeysCard", () => {
  it("lists the account's passkeys", async () => {
    render(<PasskeysCard email="alice@example.com" />);

    expect(await screen.findByText("Laptop")).toBeInTheDocument();
    expect(screen.getByText("Synced")).toBeInTheDocument();
    expect(screen.getByText(/last used never/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add a passkey/i }),
    ).toBeInTheDocument();
  });

  it("shows when there are no passkeys yet", async () => {
    mocks.listPasskeys.mockResolvedValue(passkeyList({ passkeys: [] }));

    render(<PasskeysCard email="alice@example.com" />);

    expect(await screen.findByText(/no passkeys yet/i)).toBeInTheDocument();
  });

  it("shows a loading failure", async () => {
    mocks.listPasskeys.mockRejectedValue(new Error("Unable to load."));

    render(<PasskeysCard email="alice@example.com" />);

    expect(await screen.findByText("Unable to load.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add a passkey/i }),
    ).not.toBeInTheDocument();
  });

  it("explains when this browser can't use passkeys", async () => {
    mocks.supported.mockReturnValue(false);

    render(<PasskeysCard email="alice@example.com" />);

    expect(
      await screen.findByText(/browser can't use passkeys here/i),
    ).toBeInTheDocument();
    await screen.findByText("Laptop");
    expect(
      screen.queryByRole("button", { name: /add a passkey/i }),
    ).not.toBeInTheDocument();
  });

  it("explains when the server can't use passkeys", async () => {
    mocks.listPasskeys.mockResolvedValue(passkeyList({ rp_id: null }));

    render(<PasskeysCard email="alice@example.com" />);

    expect(
      await screen.findByText(/aren't available on this orcheo server/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add a passkey/i }),
    ).not.toBeInTheDocument();
  });

  it("adds a passkey", async () => {
    const phone = { ...laptop, id: "p-2", name: "Phone", backed_up: false };
    mocks.listPasskeys
      .mockResolvedValueOnce(passkeyList())
      .mockResolvedValueOnce(passkeyList({ passkeys: [laptop, phone] }));
    mocks.registerPasskey.mockResolvedValue(phone);
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );

    expect(await screen.findByText("Phone")).toBeInTheDocument();
    expect(mocks.toast).toHaveBeenCalledWith({
      title: "Passkey added",
      description: 'You can now sign in with "Phone".',
    });
  });

  it("confirms it's you before adding a passkey after an old sign-in", async () => {
    mocks.registerPasskey
      .mockRejectedValueOnce(reauthRequired())
      .mockResolvedValueOnce({ ...laptop, id: "p-2", name: "Phone" });
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/confirm it's you/i)).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: /email me a code/i }),
    );
    expect(mocks.startEmailChallenge).toHaveBeenCalledWith(
      "alice@example.com",
      "login",
    );
    await user.type(within(dialog).getByLabelText(/sign-in code/i), "123456");
    await user.click(within(dialog).getByRole("button", { name: /confirm/i }));

    await waitFor(() => expect(mocks.registerPasskey).toHaveBeenCalledTimes(2));
    expect(mocks.verifyEmailCode).toHaveBeenCalledWith(
      "alice@example.com",
      "123456",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the confirmation open when the code is wrong", async () => {
    mocks.registerPasskey.mockRejectedValueOnce(reauthRequired());
    mocks.verifyEmailCode.mockRejectedValueOnce(
      new Error("Invalid or expired code."),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /email me a code/i }),
    );
    await user.type(within(dialog).getByLabelText(/sign-in code/i), "000000");
    await user.click(within(dialog).getByRole("button", { name: /confirm/i }));

    expect(
      await within(dialog).findByText("Invalid or expired code."),
    ).toBeInTheDocument();
    expect(mocks.registerPasskey).toHaveBeenCalledTimes(1);

    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("shows when the confirmation email can't be sent", async () => {
    mocks.registerPasskey.mockRejectedValueOnce(reauthRequired());
    mocks.startEmailChallenge.mockRejectedValueOnce(
      new Error("Too many attempts."),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: /email me a code/i }),
    );

    expect(
      await within(dialog).findByText("Too many attempts."),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByLabelText(/sign-in code/i),
    ).not.toBeInTheDocument();
  });

  it("asks people without a known email to sign in again", async () => {
    mocks.registerPasskey.mockRejectedValueOnce(reauthRequired());
    const user = userEvent.setup();
    render(<PasskeysCard email={null} />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "Sign in again to add a passkey" }),
      ),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reports passkeys that could not be added", async () => {
    mocks.registerPasskey.mockRejectedValueOnce(
      Object.assign(new Error("exists"), {
        name: "InvalidStateError",
        code: "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED",
      }),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        title: "Couldn't add a passkey",
        description: "This device already has a passkey for your account.",
        variant: "destructive",
      }),
    );
  });

  it("stays quiet when the passkey prompt was cancelled", async () => {
    mocks.registerPasskey.mockRejectedValueOnce(
      new DOMException("Cancelled", "AbortError"),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /add a passkey/i }),
    );

    await waitFor(() => expect(mocks.registerPasskey).toHaveBeenCalled());
    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it("renames a passkey", async () => {
    mocks.renamePasskey.mockResolvedValue({ ...laptop, name: "Desk" });
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /rename laptop/i }),
    );
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/name/i);
    await user.clear(input);
    await user.type(input, "  Desk ");
    await user.click(within(dialog).getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(mocks.renamePasskey).toHaveBeenCalledWith("p-1", "Desk"),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.listPasskeys).toHaveBeenCalledTimes(2);
  });

  it("reports a failed rename and can be cancelled", async () => {
    mocks.renamePasskey.mockRejectedValue(
      new AuthApiError("Passkey not found.", 404),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /rename laptop/i }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        title: "Couldn't rename the passkey",
        description: "Passkey not found.",
        variant: "destructive",
      }),
    );
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("removes a passkey and tells the browser", async () => {
    const remaining = passkeyList({ passkeys: [] });
    mocks.listPasskeys
      .mockResolvedValueOnce(passkeyList())
      .mockResolvedValueOnce(remaining);
    mocks.deletePasskey.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /remove laptop/i }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/"Laptop"/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /^remove$/i }));

    await waitFor(() =>
      expect(mocks.syncAcceptedPasskeys).toHaveBeenCalledWith(remaining),
    );
    expect(mocks.deletePasskey).toHaveBeenCalledWith("p-1");
    expect(mocks.toast).toHaveBeenCalledWith({ title: "Passkey removed" });
    expect(await screen.findByText(/no passkeys yet/i)).toBeInTheDocument();
  });

  it("does not sync the browser when the refreshed list can't load", async () => {
    mocks.listPasskeys
      .mockResolvedValueOnce(passkeyList())
      .mockRejectedValueOnce(new Error("Unable to load."));
    mocks.deletePasskey.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /remove laptop/i }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: /^remove$/i,
      }),
    );

    expect(await screen.findByText("Unable to load.")).toBeInTheDocument();
    expect(mocks.syncAcceptedPasskeys).not.toHaveBeenCalled();
  });

  it("reports a failed removal", async () => {
    mocks.deletePasskey.mockRejectedValue(
      new AuthApiError("Unable to remove the passkey.", 503),
    );
    const user = userEvent.setup();
    render(<PasskeysCard email="alice@example.com" />);

    await user.click(
      await screen.findByRole("button", { name: /remove laptop/i }),
    );
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: /^remove$/i,
      }),
    );

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        title: "Couldn't remove the passkey",
        description: "Unable to remove the passkey.",
        variant: "destructive",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
  });
});
