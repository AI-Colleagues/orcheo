import { useEffect, useMemo, useState } from "react";
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
  "email:accounts:read":
    "See which email accounts are linked, without reading mail",
  "email:read": "Read mail from linked email accounts",
  "email:write": "Send and manage mail and linked email accounts",
  "vault:write": "Add, change and delete credentials",
  "apps:read": "View hosted apps, bindings and deployments",
  "apps:write": "Create and change hosted apps and deployments",
  "apps:publish": "Publish and unpublish hosted apps",
  "workspaces:read": "View workspace administration, members and invitations",
  "workspaces:write": "Create and delete workspaces and manage membership",
  "admin:tokens:read": "View service token metadata without secrets",
  "admin:tokens:write":
    "Create and revoke service tokens through a private form",
};

// Mirrors ``_IMPLIED_SCOPES`` in the backend's mcp_server/scopes.py: reading
// or sending mail always comes with seeing which mailboxes are linked.
const IMPLIED_SCOPES: Record<string, string[]> = {
  "email:read": ["email:accounts:read"],
  "email:write": ["email:accounts:read"],
};

const impliedBy = (scope: string, kept: Set<string>) =>
  [...kept].find((other) => IMPLIED_SCOPES[other]?.includes(scope));

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
  // Everything requested starts ticked; unticking narrows the grant.
  const [kept, setKept] = useState<Set<string>>(new Set());
  const account = getAuthenticatedUserProfile();

  // A scope another kept scope implies is granted either way, so it shows as
  // kept and is sent with the rest.
  const granted = useMemo(
    () =>
      (request?.scopes ?? []).filter(
        (scope) => kept.has(scope) || impliedBy(scope, kept) !== undefined,
      ),
    [request, kept],
  );

  const toggle = (scope: string) =>
    setKept((current) => {
      const next = new Set(current);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });

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
        const loaded = (await response.json()) as AuthorizationRequest;
        setRequest(loaded);
        setKept(new Set(loaded.scopes));
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
          body: JSON.stringify(
            approve ? { approve, scopes: granted } : { approve },
          ),
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
              <fieldset>
                <legend className="mb-1 font-medium">
                  Choose what it can do:
                </legend>
                <p className="mb-3 text-xs text-muted-foreground">
                  Untick anything you don't want to allow. Tools that need an
                  unticked permission are hidden from {clientName}; to allow one
                  later, remove the connector and add it again.
                </p>
                <ul className="space-y-2">
                  {request.scopes.map((scope) => {
                    const implier = impliedBy(scope, kept);
                    const label = SCOPE_LABELS[scope];
                    return (
                      <li key={scope}>
                        <label className="flex items-start gap-2">
                          <input
                            type="checkbox"
                            className="mt-0.5 h-4 w-4 shrink-0 rounded border-border accent-primary"
                            checked={granted.includes(scope)}
                            disabled={submitting || implier !== undefined}
                            onChange={() => toggle(scope)}
                          />
                          <span className="flex flex-col">
                            <span>{label ?? scope}</span>
                            {label ? (
                              <span className="font-mono text-xs text-muted-foreground">
                                {scope}
                              </span>
                            ) : null}
                            {implier ? (
                              <span className="text-xs text-muted-foreground">
                                Included with {implier}
                              </span>
                            ) : null}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </fieldset>
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
              <Button
                disabled={submitting || granted.length === 0}
                onClick={() => void decide(true)}
              >
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
