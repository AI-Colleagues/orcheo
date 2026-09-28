import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import { getAuthenticatedUserProfile } from "@features/auth/lib/auth-session";

interface AuthorizationRequest {
  request_id: string;
  client_id: string;
  client_name: string | null;
  client_uri: string | null;
  redirect_host: string;
  scopes: string[];
}

const SCOPE_LABELS: Record<string, string> = {
  "workflows:read": "View workflows, runs and traces",
  "workflows:write": "Create, change and delete workflows",
  "workflows:execute": "Run workflows",
  "vault:read": "See which credentials exist",
  "vault:write": "Add, change and delete credentials",
};

const readError = async (response: Response, fallback: string) => {
  const payload = (await response.json().catch(() => null)) as {
    detail?: unknown;
  } | null;
  return typeof payload?.detail === "string" ? payload.detail : fallback;
};

export default function OAuthConsent() {
  const [searchParams] = useSearchParams();
  const requestId = searchParams.get("request");
  const [request, setRequest] = useState<AuthorizationRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const account = getAuthenticatedUserProfile();

  useEffect(() => {
    if (!requestId) {
      setError("This authorization link is incomplete.");
      return;
    }
    void authFetch(
      buildBackendHttpUrl(
        `/api/oauth/requests/${encodeURIComponent(requestId)}`,
      ),
      {},
      { includeWorkspaceHeaders: false },
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            await readError(response, "This authorization request is invalid."),
          );
        }
        setRequest((await response.json()) as AuthorizationRequest);
      })
      .catch((loadError: unknown) => {
        setError(
          loadError instanceof Error
            ? loadError.message
            : "This authorization request is invalid.",
        );
      });
  }, [requestId]);

  const decide = async (approve: boolean) => {
    if (!request) return;
    setSubmitting(true);
    try {
      const response = await authFetch(
        buildBackendHttpUrl(
          `/api/oauth/requests/${encodeURIComponent(request.request_id)}/decision`,
        ),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approve }),
        },
        { includeWorkspaceHeaders: false },
      );
      if (!response.ok) {
        throw new Error(await readError(response, "Authorization failed."));
      }
      const { redirect_url: redirectUrl } = (await response.json()) as {
        redirect_url: string;
      };
      globalThis.location.assign(redirectUrl);
    } catch (decisionError) {
      setError(
        decisionError instanceof Error
          ? decisionError.message
          : "Authorization failed.",
      );
      setSubmitting(false);
    }
  };

  const clientName = request?.client_name || "An application";

  return (
    <div className="flex min-h-screen items-center justify-center bg-cream dark:bg-background p-6 text-foreground">
      <Card className="w-full max-w-md border-border bg-card text-card-foreground shadow-xl">
        {error ? (
          <CardHeader className="text-center">
            <CardTitle className="text-xl">Can't authorize</CardTitle>
            <CardDescription className="text-destructive">
              {error}
            </CardDescription>
          </CardHeader>
        ) : !request ? (
          <CardContent className="flex justify-center py-10">
            <Loader2
              aria-label="Loading"
              className="h-6 w-6 animate-spin text-muted-foreground"
            />
          </CardContent>
        ) : (
          <>
            <CardHeader className="text-center">
              <ShieldCheck className="mx-auto mb-2 h-8 w-8 text-primary" />
              <CardTitle className="text-xl">Authorize {clientName}</CardTitle>
              <CardDescription>
                {clientName} wants to use Orcheo as{" "}
                <span className="font-medium text-foreground">
                  {account?.email ?? account?.name ?? "you"}
                </span>
                .
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 text-sm">
              <div>
                <p className="mb-2 font-medium">It will be able to:</p>
                <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                  {request.scopes.map((scope) => (
                    <li key={scope}>{SCOPE_LABELS[scope] ?? scope}</li>
                  ))}
                </ul>
              </div>
              <p className="text-muted-foreground">
                Approving sends you back to{" "}
                <span className="font-medium text-foreground">
                  {request.redirect_host}
                </span>
                . Only approve applications you trust; Orcheo has not reviewed
                this one. Credential secrets are never shared with it.
              </p>
            </CardContent>
            <CardFooter className="flex justify-end gap-2">
              <Button
                variant="outline"
                disabled={submitting}
                onClick={() => void decide(false)}
              >
                Deny
              </Button>
              <Button disabled={submitting} onClick={() => void decide(true)}>
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Approve"
                )}
              </Button>
            </CardFooter>
          </>
        )}
      </Card>
    </div>
  );
}
