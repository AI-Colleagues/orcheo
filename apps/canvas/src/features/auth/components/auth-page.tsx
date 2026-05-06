import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
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
import { Loader2 } from "lucide-react";
import { GoogleLogo, GithubLogo } from "@features/auth/components/auth-logos";
import { toast } from "@/hooks/use-toast";
import { startOidcLogin } from "@features/auth/lib/oidc-client";
import {
  clearAuthSession,
  setDevAuthSession,
} from "@features/auth/lib/auth-session";
import { startDevLogin } from "@/lib/api";

interface OidcInviteContext {
  invitation?: string;
  organization?: string;
  organizationName?: string;
  loginHint?: string;
  screenHint?: string;
}

interface RedirectLocationLike {
  pathname?: string;
  search?: string;
  hash?: string;
}

interface AuthLocationState {
  from?: string | RedirectLocationLike;
}

const parseInviteContext = (search: string): OidcInviteContext => {
  const params = new URLSearchParams(search);
  const normalize = (value: string | null): string | undefined => {
    if (!value) {
      return undefined;
    }
    const trimmed = value.trim();
    return trimmed || undefined;
  };

  return {
    invitation: normalize(params.get("invitation")),
    organization: normalize(params.get("organization")),
    organizationName: normalize(params.get("organization_name")),
    loginHint: normalize(params.get("login_hint")),
    screenHint: normalize(params.get("screen_hint")),
  };
};

const mergeInviteContext = (
  fallback: OidcInviteContext,
  preferred: OidcInviteContext,
): OidcInviteContext => ({
  invitation: preferred.invitation ?? fallback.invitation,
  organization: preferred.organization ?? fallback.organization,
  organizationName: preferred.organizationName ?? fallback.organizationName,
  loginHint: preferred.loginHint ?? fallback.loginHint,
  screenHint: preferred.screenHint ?? fallback.screenHint,
});

const resolveRedirectTo = (state: unknown): string => {
  const from = (state as AuthLocationState | null)?.from;
  if (typeof from === "string") {
    return from.trim() || "/";
  }

  if (from && typeof from === "object") {
    const { pathname = "", search = "", hash = "" } = from;
    const redirectTo = `${pathname}${search}${hash}`;
    return redirectTo.trim() || "/";
  }

  return "/";
};

const extractSearch = (pathWithSearchAndHash: string): string => {
  const value = pathWithSearchAndHash.trim();
  if (!value) {
    return "";
  }

  try {
    return new URL(value, "https://orcheo.local").search;
  } catch {
    return "";
  }
};

export default function AuthPage() {
  const location = useLocation();
  const [providerLoading, setProviderLoading] = useState<
    "google" | "github" | null
  >(null);
  const [devEmail, setDevEmail] = useState("local@orcheo.local");
  const [devName, setDevName] = useState("Local Developer");
  const [devLoginLoading, setDevLoginLoading] = useState(false);
  const redirectTo = useMemo(
    () => resolveRedirectTo(location.state),
    [location.state],
  );
  const inviteContext = useMemo(() => {
    const fromRedirectState = parseInviteContext(extractSearch(redirectTo));
    const fromLoginSearch = parseInviteContext(location.search);

    return mergeInviteContext(fromRedirectState, fromLoginSearch);
  }, [location.search, redirectTo]);
  const oidcConfigured = Boolean(
    (import.meta.env?.VITE_ORCHEO_AUTH_ISSUER ?? "").trim() &&
      (import.meta.env?.VITE_ORCHEO_AUTH_CLIENT_ID ?? "").trim(),
  );

  const startLocalLogin = async () => {
    setDevLoginLoading(true);
    try {
      const response = await startDevLogin({
        provider: "local",
        email: devEmail,
        name: devName,
      });
      clearAuthSession();
      setDevAuthSession({
        provider: response.provider,
        subject: response.subject,
        displayName: response.display_name,
      });
      toast({
        title: "Signed in locally",
        description: `Welcome ${response.display_name}.`,
      });
      const target = new URL(redirectTo, window.location.origin);
      target.searchParams.set(
        "dev_session",
        encodeURIComponent(
          JSON.stringify({
            provider: response.provider,
            subject: response.subject,
            displayName: response.display_name,
          }),
        ),
      );
      window.location.assign(target.toString());
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unable to sign in locally.";
      toast({
        title: "Local sign-in failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setDevLoginLoading(false);
    }
  };

  const startProviderLogin = async (provider: "google" | "github") => {
    setProviderLoading(provider);
    try {
      await startOidcLogin({ provider, redirectTo, ...inviteContext });
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Unable to start the login flow.";
      toast({
        title: "Login failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setProviderLoading(null);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-100 text-foreground dark:bg-slate-950">
      <div
        className="absolute inset-0 bg-gradient-to-br from-slate-100 via-slate-200 to-slate-100 dark:from-slate-950 dark:via-slate-900/80 dark:to-black"
        aria-hidden="true"
      />
      <div
        className="absolute inset-0 opacity-40 mix-blend-soft-light dark:opacity-60"
        style={{
          backgroundImage:
            "radial-gradient(circle at 20% 20%, rgba(148, 163, 184, 0.2), transparent 45%), radial-gradient(circle at 80% 30%, rgba(56, 189, 248, 0.25), transparent 50%), radial-gradient(circle at 50% 80%, rgba(45, 212, 191, 0.2), transparent 55%)",
        }}
        aria-hidden="true"
      />
      <Card className="relative z-10 mx-auto min-w-80 max-w-md border-border bg-card/80 backdrop-blur-xl dark:border-primary/25 dark:bg-primary/5">
        <CardHeader className="space-y-1">
          <div className="flex items-center justify-center mb-2">
            <Link to="/" className="flex items-center gap-2 font-semibold">
              <img src="/favicon.ico" alt="Orcheo Logo" className="h-8 w-8" />
              <span className="text-xl font-bold">Orcheo Canvas</span>
            </Link>
          </div>
          <CardTitle className="text-2xl">Sign in</CardTitle>
          <CardDescription>
            {oidcConfigured
              ? "Continue with your OAuth provider, or use the local dev login below."
              : "Use the local dev login below for this development stack."}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {oidcConfigured ? (
            <div className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                className="w-full"
                onClick={() => startProviderLogin("google")}
                disabled={providerLoading !== null || devLoginLoading}
              >
                {providerLoading === "google" ? (
                  <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                ) : (
                  <GoogleLogo className="h-5 w-5 mr-2" />
                )}
                {providerLoading === "google" ? "Signing in…" : "Google"}
              </Button>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => startProviderLogin("github")}
                disabled={providerLoading !== null || devLoginLoading}
              >
                {providerLoading === "github" ? (
                  <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                ) : (
                  <GithubLogo className="h-5 w-5 mr-2" />
                )}
                {providerLoading === "github" ? "Signing in…" : "GitHub"}
              </Button>
            </div>
          ) : null}
          <div className="grid gap-3 rounded-lg border border-dashed border-border/70 bg-background/70 p-4">
            <div className="space-y-1">
              <h3 className="font-medium">Local development login</h3>
              <p className="text-sm text-muted-foreground">
                This creates a developer session backed by the local backend so
                you can test workspace separation without a real IdP.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="dev-name">Name</Label>
              <Input
                id="dev-name"
                value={devName}
                onChange={(event) => setDevName(event.target.value)}
                placeholder="Local Developer"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="dev-email">Email</Label>
              <Input
                id="dev-email"
                value={devEmail}
                onChange={(event) => setDevEmail(event.target.value)}
                placeholder="local@orcheo.local"
              />
            </div>
            <Button
              className="w-full"
              onClick={startLocalLogin}
              disabled={devLoginLoading || providerLoading !== null}
            >
              {devLoginLoading ? (
                <Loader2 className="h-5 w-5 mr-2 animate-spin" />
              ) : null}
              {devLoginLoading ? "Signing in…" : "Continue locally"}
            </Button>
          </div>
          {oidcConfigured ? (
            <div className="mt-2 text-center text-sm text-muted-foreground">
              Need access? Contact your admin.
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
