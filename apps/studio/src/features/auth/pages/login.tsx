import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { KeyRound, Loader2, Mail } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { toast } from "@/hooks/use-toast";
import {
  AuthApiError,
  beginPasskeySignIn,
  completePasskeySignIn,
  startEmailChallenge,
  verifyEmailCode,
  type PasskeySignInRequest,
} from "@features/auth/lib/auth-api";
import { isAuthenticated } from "@features/auth/lib/auth-session";
import {
  cancelPasskeyPrompt,
  isPasskeyOriginMismatch,
  isPasskeyPromptCancelled,
  passkeyAutofillSupported,
  passkeyErrorMessage,
  passkeysSupported,
} from "@features/auth/lib/passkey-support";
import {
  dismissPasskeyOffer,
  registerPasskey,
  shouldOfferPasskey,
} from "@features/auth/lib/passkeys";

// Only allow same-origin relative paths to avoid open-redirect via state/query.
const sanitizeRedirect = (value: unknown): string | undefined => {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) {
    return undefined;
  }
  return trimmed;
};

const resolveRedirectTo = (state: unknown, search: string): string => {
  const fromState =
    state && typeof state === "object"
      ? (state as { from?: unknown }).from
      : undefined;
  const stateRedirect = sanitizeRedirect(fromState);
  if (stateRedirect) {
    return stateRedirect;
  }
  const params = new URLSearchParams(search);
  return sanitizeRedirect(params.get("redirect") ?? params.get("from")) ?? "/";
};

type Stage = "email" | "sent" | "offer-passkey";

// The server can't use passkeys (404), or this page's address can't.
const passkeysUnusable = (error: unknown): boolean =>
  (error instanceof AuthApiError && error.status === 404) ||
  isPasskeyOriginMismatch(error);

export default function Login() {
  const location = useLocation();
  const navigate = useNavigate();
  const redirectTo = useMemo(
    () => resolveRedirectTo(location.state, location.search),
    [location.state, location.search],
  );

  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passkeysAvailable, setPasskeysAvailable] = useState(passkeysSupported);
  // Bumped to offer passkeys in the email autofill again after a failed try.
  const [autofillRound, setAutofillRound] = useState(0);

  const finishSignIn = useCallback(() => {
    navigate(redirectTo, { replace: true });
  }, [navigate, redirectTo]);

  useEffect(() => {
    if (isAuthenticated()) {
      navigate(redirectTo, { replace: true });
    }
  }, [navigate, redirectTo]);

  // While the email field is shown, offer saved passkeys in its autofill.
  useEffect(() => {
    if (stage !== "email" || !passkeysAvailable || isAuthenticated()) {
      return;
    }
    const controller = new AbortController();
    const offerPasskeys = async () => {
      if (!(await passkeyAutofillSupported()) || controller.signal.aborted) {
        return;
      }
      let request: PasskeySignInRequest;
      try {
        request = await beginPasskeySignIn(controller.signal);
      } catch (err) {
        // Nobody has asked for a passkey yet, so stay quiet.
        if (passkeysUnusable(err)) {
          setPasskeysAvailable(false);
        }
        return;
      }
      if (controller.signal.aborted) {
        return;
      }
      try {
        await completePasskeySignIn(request, { useBrowserAutofill: true });
        finishSignIn();
      } catch (err) {
        if (controller.signal.aborted || isPasskeyPromptCancelled(err)) {
          return;
        }
        if (passkeysUnusable(err)) {
          setPasskeysAvailable(false);
        } else if (err instanceof AuthApiError) {
          // A passkey was picked but refused; let the person pick again.
          setError(err.message);
          setAutofillRound((round) => round + 1);
        }
      }
    };
    void offerPasskeys();
    return () => {
      controller.abort();
      cancelPasskeyPrompt();
    };
  }, [stage, passkeysAvailable, autofillRound, finishSignIn]);

  const handleSendEmail = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await startEmailChallenge(email.trim(), "login");
      setStage("sent");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to send the sign-in email.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyCode = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await verifyEmailCode(email.trim(), code.trim());
      if (passkeysAvailable && (await shouldOfferPasskey())) {
        setStage("offer-passkey");
        return;
      }
      finishSignIn();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "That code is invalid or expired.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handlePasskeySignIn = async () => {
    setError(null);
    setBusy(true);
    try {
      await completePasskeySignIn(await beginPasskeySignIn());
      finishSignIn();
    } catch (err) {
      if (passkeysUnusable(err)) {
        setPasskeysAvailable(false);
      }
      setError(
        passkeyErrorMessage(err, "Passkey sign-in failed. Please try again."),
      );
      // This prompt replaced the autofill request; offer passkeys there again.
      setAutofillRound((round) => round + 1);
    } finally {
      setBusy(false);
    }
  };

  const handleCreatePasskey = async () => {
    setError(null);
    setBusy(true);
    try {
      const passkey = await registerPasskey();
      toast({
        title: "Passkey added",
        description: `Next time, sign in with "${passkey.name}".`,
      });
      finishSignIn();
    } catch (err) {
      setError(passkeyErrorMessage(err, "Your passkey could not be added."));
    } finally {
      setBusy(false);
    }
  };

  const handleSkipPasskey = () => {
    dismissPasskeyOffer();
    finishSignIn();
  };

  const resetToEmail = () => {
    setStage("email");
    setCode("");
    setError(null);
  };

  const description = {
    email: "Enter your email and we'll send you a sign-in code.",
    sent: `We sent a sign-in code to ${email}.`,
    "offer-passkey":
      "Create a passkey to sign in with your fingerprint, face, or screen " +
      "lock instead of an emailed code.",
  }[stage];

  return (
    <div className="flex min-h-screen items-center justify-center bg-cream dark:bg-background p-6 text-foreground">
      <Card className="w-full max-w-md border-border bg-card text-card-foreground shadow-xl">
        <CardHeader className="text-center">
          {stage === "offer-passkey" && (
            <KeyRound className="mx-auto mb-2 h-8 w-8 text-primary" />
          )}
          <CardTitle className="text-xl">
            {stage === "offer-passkey"
              ? "Sign in faster next time"
              : "Sign in to Orcheo"}
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          {stage === "email" && (
            <>
              <form className="flex flex-col gap-4" onSubmit={handleSendEmail}>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="email">Email address</Label>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="username webauthn"
                    required
                    autoFocus
                    placeholder="you@example.com"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </div>
                {error && <p className="text-sm text-destructive">{error}</p>}
                <Button type="submit" disabled={busy || !email.trim()}>
                  {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      <Mail className="mr-2 h-4 w-4" />
                      Continue with email
                    </>
                  )}
                </Button>
              </form>
              {passkeysAvailable && (
                <>
                  <div className="my-4 flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="h-px flex-1 bg-border" />
                    or
                    <span className="h-px flex-1 bg-border" />
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={() => void handlePasskeySignIn()}
                    disabled={busy}
                  >
                    <KeyRound className="mr-2 h-4 w-4" />
                    Sign in with a passkey
                  </Button>
                </>
              )}
            </>
          )}
          {stage === "sent" && (
            <form className="flex flex-col gap-4" onSubmit={handleVerifyCode}>
              <div className="flex flex-col gap-2">
                <Label htmlFor="code">Sign-in code</Label>
                <Input
                  id="code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                  autoFocus
                  placeholder="123456"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                />
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button type="submit" disabled={busy || !code.trim()}>
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Verify code"
                )}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={resetToEmail}
                disabled={busy}
              >
                Use a different email
              </Button>
            </form>
          )}
          {stage === "offer-passkey" && (
            <div className="flex flex-col gap-4">
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button
                type="button"
                onClick={() => void handleCreatePasskey()}
                disabled={busy}
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Create a passkey"
                )}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={handleSkipPasskey}
                disabled={busy}
              >
                Not now
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
