import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";

export default function AppAuthorize() {
  const [searchParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const host = searchParams.get("host");
    const redirectUri = searchParams.get("redirect_uri");
    const codeChallenge = searchParams.get("code_challenge");
    const state = searchParams.get("state");
    if (!host || !redirectUri || !codeChallenge || !state) {
      setError("This app authorization request is incomplete.");
      return;
    }
    void authFetch(
      buildBackendHttpUrl("/api/hosted-apps/auth/authorize"),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host,
          redirect_uri: redirectUri,
          code_challenge: codeChallenge,
          state,
        }),
      },
      { includeWorkspaceHeaders: false },
    )
      .then(async (response) => {
        if (!response.ok) {
          const payload = (await response.json().catch(() => null)) as {
            detail?: string;
          } | null;
          throw new Error(payload?.detail || "App authorization was denied.");
        }
        return response.json() as Promise<{ redirect_url: string }>;
      })
      .then(({ redirect_url: redirectUrl }) => {
        globalThis.location.assign(redirectUrl);
      })
      .catch((authorizationError: unknown) => {
        setError(
          authorizationError instanceof Error
            ? authorizationError.message
            : "App authorization failed.",
        );
      });
  }, [searchParams]);

  return (
    <main className="grid min-h-screen place-items-center bg-background p-6">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold">Authorizing hosted app</h1>
        <p
          className={
            error
              ? "mt-3 text-sm text-destructive"
              : "mt-3 text-sm text-muted-foreground"
          }
        >
          {error ?? "Checking your workspace membership…"}
        </p>
      </div>
    </main>
  );
}
