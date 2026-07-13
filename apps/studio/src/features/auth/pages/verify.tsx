import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { verifyEmailToken } from "@features/auth/lib/auth-api";

// Only allow same-origin relative paths to avoid open-redirect.
const sanitizeRedirect = (value: string | null): string => {
  if (!value) {
    return "/";
  }
  const trimmed = value.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) {
    return "/";
  }
  return trimmed;
};

type VerifyState = "working" | "error";

export default function AuthVerify() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token");
  const redirectTo = sanitizeRedirect(searchParams.get("redirect"));
  const [state, setState] = useState<VerifyState>("working");
  const [message, setMessage] = useState("Signing you in…");
  // The magic-link token is single-use; guard against React 18 StrictMode's
  // double-invoke redeeming it twice.
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) {
      return;
    }
    startedRef.current = true;

    if (!token) {
      setState("error");
      setMessage("This sign-in link is missing its token.");
      return;
    }

    void (async () => {
      try {
        await verifyEmailToken(token);
        navigate(redirectTo, { replace: true });
      } catch (err) {
        setState("error");
        setMessage(
          err instanceof Error
            ? err.message
            : "This sign-in link is invalid or has expired.",
        );
      }
    })();
  }, [token, redirectTo, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-cream dark:bg-background p-6 text-foreground">
      <Card className="w-full max-w-md border-border bg-card text-card-foreground shadow-xl">
        <CardHeader>
          <CardTitle>Signing in</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-4 text-center text-sm text-muted-foreground">
          {state === "working" ? (
            <div className="flex items-center gap-3">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>{message}</span>
            </div>
          ) : (
            <>
              <p>{message}</p>
              <Button onClick={() => navigate("/login", { replace: true })}>
                Back to sign in
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
