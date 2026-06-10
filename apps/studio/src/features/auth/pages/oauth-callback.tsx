import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Loader2 } from "lucide-react";
import {
  completeOidcLogin,
  consumePostLoginRedirect,
} from "@features/auth/lib/oidc-client";

export default function OAuthCallback() {
  const navigate = useNavigate();
  const [message, setMessage] = useState("Completing sign-in…");

  useEffect(() => {
    const handleCallback = async () => {
      const url = new URL(window.location.href);
      const error = url.searchParams.get("error");
      const errorDescription = url.searchParams.get("error_description");
      if (error) {
        console.error("OAuth login failed.", { error, errorDescription });
        setMessage("OAuth login failed. Please try again.");
        return;
      }

      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) {
        setMessage("Missing OAuth response details.");
        return;
      }

      try {
        await completeOidcLogin({ code, state });
        const redirectTo = consumePostLoginRedirect() ?? "/";
        navigate(redirectTo, { replace: true });
      } catch (err) {
        console.error("Unable to complete login.", err);
        setMessage("Unable to complete login.");
      }
    };

    void handleCallback();
  }, [navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 text-foreground">
      <Card className="max-w-md border-primary/25 bg-primary/5">
        <CardHeader>
          <CardTitle>Signing in</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center gap-3 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>{message}</span>
        </CardContent>
      </Card>
    </div>
  );
}
