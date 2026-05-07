import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { isAuthenticated, getAuthTokens } from "@features/auth/lib/auth-session";
import { tryRefreshTokens } from "@features/auth/lib/oidc-client";
import AutoLogin from "@features/auth/components/auto-login";

const isValidHttpUrl = (value: unknown): boolean => {
  if (!value || typeof value !== "string") return false;
  try {
    const url = new URL(value.trim());
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
};

const authEnabled = isValidHttpUrl(import.meta.env.VITE_ORCHEO_AUTH_ISSUER);

type AuthState = "authenticated" | "refreshing" | "unauthenticated";

const resolveInitialAuthState = (): AuthState => {
  if (!authEnabled || isAuthenticated()) return "authenticated";
  if (getAuthTokens()?.refreshToken) return "refreshing";
  return "unauthenticated";
};

export default function RequireAuth() {
  const location = useLocation();
  const redirectTo = `${location.pathname}${location.search}${location.hash}`;
  const [authState, setAuthState] = useState<AuthState>(resolveInitialAuthState);

  useEffect(() => {
    if (authState !== "refreshing") return;
    tryRefreshTokens().then((refreshed) => {
      setAuthState(refreshed ? "authenticated" : "unauthenticated");
    });
  }, [authState]);

  if (authState === "authenticated") return <Outlet />;

  if (authState === "refreshing") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return <AutoLogin redirectTo={redirectTo} />;
}
