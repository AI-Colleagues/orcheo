import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Loader2, Mail } from "lucide-react";
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
import {
  startEmailChallenge,
  verifyEmailCode,
} from "@features/auth/lib/auth-api";
import { isAuthenticated } from "@features/auth/lib/auth-session";

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

type Stage = "email" | "sent";

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

  useEffect(() => {
    if (isAuthenticated()) {
      navigate(redirectTo, { replace: true });
    }
  }, [navigate, redirectTo]);

  const handleSendEmail = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await startEmailChallenge(email.trim(), "login", redirectTo);
      setStage("sent");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to send the sign-in email.",
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
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "That code is invalid or expired.",
      );
    } finally {
      setBusy(false);
    }
  };

  const resetToEmail = () => {
    setStage("email");
    setCode("");
    setError(null);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#ebd3a6] p-6 text-foreground">
      <Card className="w-full max-w-md border-border bg-card text-card-foreground shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-xl">Sign in to Orcheo</CardTitle>
          <CardDescription>
            {stage === "email"
              ? "Enter your email and we'll send you a sign-in link and code."
              : `We sent a sign-in link and code to ${email}.`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {stage === "email" ? (
            <form className="flex flex-col gap-4" onSubmit={handleSendEmail}>
              <div className="flex flex-col gap-2">
                <Label htmlFor="email">Email address</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
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
          ) : (
            <form className="flex flex-col gap-4" onSubmit={handleVerifyCode}>
              <p className="text-sm text-muted-foreground">
                Click the link in your email, or enter the code below.
              </p>
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
        </CardContent>
      </Card>
    </div>
  );
}
