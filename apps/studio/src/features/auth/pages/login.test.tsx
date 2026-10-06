import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthApiError } from "@features/auth/lib/auth-api";
import Login from "./login";

const navigateMock = vi.fn();
const locationMock = { pathname: "/login", search: "", hash: "", state: null };

vi.mock("react-router-dom", () => ({
  useLocation: () => locationMock,
  useNavigate: () => navigateMock,
}));

const webauthn = vi.hoisted(() => ({
  supported: vi.fn(() => false),
  autofill: vi.fn(() => Promise.resolve(false)),
  cancelCeremony: vi.fn(),
}));

vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => webauthn.supported(),
  browserSupportsWebAuthnAutofill: () => webauthn.autofill(),
  platformAuthenticatorIsAvailable: () => Promise.resolve(false),
  WebAuthnAbortService: { cancelCeremony: () => webauthn.cancelCeremony() },
  startAuthentication: vi.fn(),
  startRegistration: vi.fn(),
  sendSignal: vi.fn(),
}));

type AsyncMock<T> = (...args: unknown[]) => Promise<T>;

const startEmailChallenge = vi.fn<AsyncMock<void>>(() => Promise.resolve());
const verifyEmailCode = vi.fn<AsyncMock<unknown>>(() =>
  Promise.resolve(undefined),
);
const passkeyRequest = { challengeId: "c-1", options: { challenge: "abc" } };
const beginPasskeySignIn = vi.fn<AsyncMock<typeof passkeyRequest>>(() =>
  Promise.resolve(passkeyRequest),
);
const completePasskeySignIn = vi.fn<AsyncMock<unknown>>(() =>
  Promise.resolve(undefined),
);
const isAuthenticated = vi.fn(() => false);
const shouldOfferPasskey = vi.fn(() => Promise.resolve(false));
const registerPasskey = vi.fn(() =>
  Promise.resolve({ id: "p-1", name: "Chrome on macOS" }),
);
const dismissPasskeyOffer = vi.fn();
const toastMock = vi.fn();

vi.mock("@features/auth/lib/auth-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@features/auth/lib/auth-api")>()),
  startEmailChallenge: (...args: unknown[]) => startEmailChallenge(...args),
  verifyEmailCode: (...args: unknown[]) => verifyEmailCode(...args),
  beginPasskeySignIn: (...args: unknown[]) => beginPasskeySignIn(...args),
  completePasskeySignIn: (...args: unknown[]) => completePasskeySignIn(...args),
}));

vi.mock("@features/auth/lib/auth-session", () => ({
  isAuthenticated: () => isAuthenticated(),
}));

vi.mock("@features/auth/lib/passkeys", () => ({
  shouldOfferPasskey: () => shouldOfferPasskey(),
  registerPasskey: () => registerPasskey(),
  dismissPasskeyOffer: () => dismissPasskeyOffer(),
}));

vi.mock("@/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toastMock(...args),
}));

const pending = () => new Promise<never>(() => undefined);

const enablePasskeys = ({ autofill = false } = {}) => {
  webauthn.supported.mockReturnValue(true);
  webauthn.autofill.mockResolvedValue(autofill);
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  isAuthenticated.mockReturnValue(false);
  webauthn.supported.mockReturnValue(false);
  webauthn.autofill.mockResolvedValue(false);
  shouldOfferPasskey.mockResolvedValue(false);
  beginPasskeySignIn.mockResolvedValue(passkeyRequest);
  completePasskeySignIn.mockResolvedValue(undefined);
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

describe("Login with passkeys", () => {
  it("hides passkeys when the browser can't use them", () => {
    render(<Login />);

    expect(
      screen.queryByRole("button", { name: /sign in with a passkey/i }),
    ).not.toBeInTheDocument();
    expect(beginPasskeySignIn).not.toHaveBeenCalled();
  });

  it("signs in with the passkey button", async () => {
    enablePasskeys();
    const user = userEvent.setup();
    render(<Login />);

    expect(screen.getByLabelText(/email address/i)).toHaveAttribute(
      "autocomplete",
      "username webauthn",
    );
    await user.click(
      screen.getByRole("button", { name: /sign in with a passkey/i }),
    );

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
    expect(beginPasskeySignIn).toHaveBeenCalledTimes(1);
    expect(completePasskeySignIn).toHaveBeenCalledWith(passkeyRequest);
  });

  it("offers saved passkeys in the email autofill", async () => {
    enablePasskeys({ autofill: true });

    render(<Login />);

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
    expect(beginPasskeySignIn).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(completePasskeySignIn).toHaveBeenCalledWith(passkeyRequest, {
      useBrowserAutofill: true,
    });
  });

  it("hides passkeys when the server can't verify them", async () => {
    enablePasskeys({ autofill: true });
    beginPasskeySignIn.mockRejectedValueOnce(
      new AuthApiError("Passkeys are not available on this server.", 404),
    );

    render(<Login />);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /sign in with a passkey/i }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(/not available/i)).not.toBeInTheDocument();
  });

  it("stays quiet when autofill can't start for other reasons", async () => {
    enablePasskeys({ autofill: true });
    beginPasskeySignIn.mockRejectedValueOnce(new TypeError("network down"));

    render(<Login />);

    await waitFor(() => expect(beginPasskeySignIn).toHaveBeenCalled());
    expect(
      screen.getByRole("button", { name: /sign in with a passkey/i }),
    ).toBeInTheDocument();
    expect(completePasskeySignIn).not.toHaveBeenCalled();
  });

  it("explains a refused autofill passkey and offers autofill again", async () => {
    enablePasskeys({ autofill: true });
    completePasskeySignIn
      .mockRejectedValueOnce(
        new AuthApiError(
          "This passkey is not registered with Orcheo.",
          400,
          "auth.passkey_unknown",
        ),
      )
      .mockImplementation(pending);

    render(<Login />);

    expect(
      await screen.findByText(/not registered with orcheo/i),
    ).toBeInTheDocument();
    await waitFor(() => expect(beginPasskeySignIn).toHaveBeenCalledTimes(2));
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("stops offering passkeys when this address can't use them", async () => {
    enablePasskeys({ autofill: true });
    completePasskeySignIn.mockRejectedValueOnce(
      Object.assign(new Error("bad rp id"), {
        name: "SecurityError",
        code: "ERROR_INVALID_RP_ID",
      }),
    );

    render(<Login />);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /sign in with a passkey/i }),
      ).not.toBeInTheDocument(),
    );
    expect(beginPasskeySignIn).toHaveBeenCalledTimes(1);
  });

  it("ignores autofill prompts the app cancelled", async () => {
    enablePasskeys({ autofill: true });
    completePasskeySignIn.mockRejectedValueOnce(
      new DOMException("Cancelled", "AbortError"),
    );

    render(<Login />);

    await waitFor(() => expect(completePasskeySignIn).toHaveBeenCalled());
    expect(screen.queryByText(/cancelled/i)).not.toBeInTheDocument();
    expect(beginPasskeySignIn).toHaveBeenCalledTimes(1);
  });

  it("ignores autofill prompts the person dismissed", async () => {
    enablePasskeys({ autofill: true });
    completePasskeySignIn.mockRejectedValueOnce(
      Object.assign(new Error("dismissed"), { name: "NotAllowedError" }),
    );

    render(<Login />);

    await waitFor(() => expect(completePasskeySignIn).toHaveBeenCalled());
    expect(screen.queryByText(/closed or timed out/i)).not.toBeInTheDocument();
  });

  it("cancels the autofill prompt when moving on to the code", async () => {
    enablePasskeys({ autofill: true });
    completePasskeySignIn.mockImplementation(pending);
    const user = userEvent.setup();
    render(<Login />);
    await waitFor(() => expect(completePasskeySignIn).toHaveBeenCalled());

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );

    await screen.findByLabelText(/sign-in code/i);
    expect(webauthn.cancelCeremony).toHaveBeenCalled();
    const [signal] = beginPasskeySignIn.mock.calls[0] as [AbortSignal];
    expect(signal.aborted).toBe(true);
  });

  it("explains a dismissed passkey prompt", async () => {
    enablePasskeys();
    completePasskeySignIn.mockRejectedValueOnce(
      Object.assign(new Error("dismissed"), { name: "NotAllowedError" }),
    );
    const user = userEvent.setup();
    render(<Login />);

    await user.click(
      screen.getByRole("button", { name: /sign in with a passkey/i }),
    );

    expect(
      await screen.findByText(/passkey prompt was closed or timed out/i),
    ).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("hides the passkey button when the server can't use passkeys", async () => {
    enablePasskeys();
    beginPasskeySignIn.mockRejectedValueOnce(
      new AuthApiError("Passkeys are not available on this server.", 404),
    );
    const user = userEvent.setup();
    render(<Login />);

    await user.click(
      screen.getByRole("button", { name: /sign in with a passkey/i }),
    );

    expect(
      await screen.findByText(/not available on this server/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /sign in with a passkey/i }),
    ).not.toBeInTheDocument();
  });

  it("offers to create a passkey after an emailed-code sign-in", async () => {
    enablePasskeys();
    shouldOfferPasskey.mockResolvedValue(true);
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify code/i }));

    expect(
      await screen.findByText(/sign in faster next time/i),
    ).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /create a passkey/i }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }),
    );
    expect(registerPasskey).toHaveBeenCalled();
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Passkey added" }),
    );
  });

  it("lets people skip the passkey offer", async () => {
    enablePasskeys();
    shouldOfferPasskey.mockResolvedValue(true);
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify code/i }));
    await user.click(await screen.findByRole("button", { name: /not now/i }));

    expect(dismissPasskeyOffer).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith("/", { replace: true });
    expect(registerPasskey).not.toHaveBeenCalled();
  });

  it("keeps the passkey offer open when creation fails", async () => {
    enablePasskeys();
    shouldOfferPasskey.mockResolvedValue(true);
    registerPasskey.mockRejectedValueOnce(
      new AuthApiError("Your passkey could not be verified.", 400),
    );
    const user = userEvent.setup();
    render(<Login />);

    await user.type(
      screen.getByLabelText(/email address/i),
      "alice@example.com",
    );
    await user.click(
      screen.getByRole("button", { name: /continue with email/i }),
    );
    await user.type(await screen.findByLabelText(/sign-in code/i), "123456");
    await user.click(screen.getByRole("button", { name: /verify code/i }));
    await user.click(
      await screen.findByRole("button", { name: /create a passkey/i }),
    );

    expect(
      await screen.findByText(/could not be verified/i),
    ).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
