import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { startOidcLogin } from "@features/auth/lib/oidc-client";

interface AutoLoginProps {
  redirectTo?: string;
  invitation?: string;
  organization?: string;
  organizationName?: string;
  loginHint?: string;
  screenHint?: string;
}

export default function AutoLogin({
  redirectTo,
  invitation,
  organization,
  organizationName,
  loginHint,
  screenHint,
}: AutoLoginProps) {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    startOidcLogin({
      redirectTo,
      invitation,
      organization,
      organizationName,
      loginHint,
      screenHint,
      prompt: "login",
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Unable to start login.");
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}
