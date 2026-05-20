import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Input } from "@/design-system/ui/input";
import {
  disconnectExternalAgent,
  getExternalAgentLoginSession,
  getExternalAgents,
  refreshExternalAgents,
  startExternalAgentLogin,
  submitExternalAgentLoginInput,
  type ExternalAgentLoginSession,
  type ExternalAgentLoginSessionState,
  type ExternalAgentProviderName,
  type ExternalAgentProviderState,
  type ExternalAgentProviderStatus,
} from "@/lib/api";

const PROVIDER_ORDER: ExternalAgentProviderName[] = [
  "claude_code",
  "codex",
  "gemini",
];
const SESSION_TERMINAL_STATES = new Set<ExternalAgentLoginSessionState>([
  "authenticated",
  "failed",
  "timed_out",
]);

type ActiveSessions = Partial<Record<ExternalAgentProviderName, string>>;
type SessionMap = Partial<
  Record<ExternalAgentProviderName, ExternalAgentLoginSession>
>;

function useCopyFeedback() {
  const [copiedValue, setCopiedValue] = useState<string | null>(null);

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedValue(text);
    setTimeout(() => setCopiedValue(null), 2000);
  }, []);

  return { copiedValue, copy };
}

const badgeVariantForState = (
  state: ExternalAgentProviderState,
): "default" | "secondary" | "destructive" | "outline" => {
  if (state === "ready") {
    return "default";
  }
  if (state === "error") {
    return "destructive";
  }
  if (state === "needs_login" || state === "not_installed") {
    return "outline";
  }
  return "secondary";
};

const labelForProviderState = (state: ExternalAgentProviderState): string => {
  switch (state) {
    case "checking":
      return "Checking";
    case "installing":
      return "Installing";
    case "not_installed":
      return "Not installed";
    case "needs_login":
      return "Needs login";
    case "authenticating":
      return "Connecting";
    case "ready":
      return "Connected";
    case "error":
      return "Error";
    default:
      return "Unknown";
  }
};

const labelForSessionState = (
  state: ExternalAgentLoginSessionState,
): string => {
  switch (state) {
    case "installing":
      return "Installing runtime";
    case "awaiting_oauth":
      return "Awaiting browser sign-in";
    case "authenticated":
      return "Authenticated";
    case "failed":
      return "Failed";
    case "timed_out":
      return "Timed out";
    default:
      return "Preparing";
  }
};

const isTerminalSessionState = (
  state: ExternalAgentLoginSessionState,
): boolean => SESSION_TERMINAL_STATES.has(state);

const formatTimestamp = (value: string | null): string | null => {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

const sortStatuses = (
  providers: ExternalAgentProviderStatus[],
): ExternalAgentProviderStatus[] =>
  [...providers].sort(
    (left, right) =>
      PROVIDER_ORDER.indexOf(left.provider) -
      PROVIDER_ORDER.indexOf(right.provider),
  );

const ExternalAgentsSection = () => {
  const [providerStatuses, setProviderStatuses] = useState<
    ExternalAgentProviderStatus[]
  >([]);
  const [loginSessions, setLoginSessions] = useState<SessionMap>({});
  const [activeSessions, setActiveSessions] = useState<ActiveSessions>({});
  const [isLoadingStatuses, setIsLoadingStatuses] = useState(true);
  const [isRefreshingStatuses, setIsRefreshingStatuses] = useState(false);
  const [pendingProvider, setPendingProvider] =
    useState<ExternalAgentProviderName | null>(null);
  const [pendingDisconnectProvider, setPendingDisconnectProvider] =
    useState<ExternalAgentProviderName | null>(null);
  const [pendingInputProvider, setPendingInputProvider] =
    useState<ExternalAgentProviderName | null>(null);
  const [sessionInputs, setSessionInputs] = useState<
    Partial<Record<ExternalAgentProviderName, string>>
  >({});
  const [statusError, setStatusError] = useState<string | null>(null);
  const { copiedValue, copy } = useCopyFeedback();

  const syncActiveSessions = useCallback(
    (providers: ExternalAgentProviderStatus[]) => {
      setActiveSessions((current) => {
        const next: ActiveSessions = { ...current };
        for (const provider of providers) {
          if (provider.active_session_id) {
            next[provider.provider] = provider.active_session_id;
          }
        }
        return next;
      });
    },
    [],
  );

  const loadProviderStatuses = useCallback(async () => {
    try {
      const response = await getExternalAgents();
      const providers = sortStatuses(response.providers);
      setProviderStatuses(providers);
      syncActiveSessions(providers);
      setStatusError(null);
    } catch (error) {
      setStatusError(
        error instanceof Error
          ? error.message
          : "Failed to load external agent status.",
      );
    } finally {
      setIsLoadingStatuses(false);
    }
  }, [syncActiveSessions]);

  const queueStatusRefresh = useCallback(async () => {
    setIsRefreshingStatuses(true);
    try {
      const response = await refreshExternalAgents();
      const providers = sortStatuses(response.providers);
      setProviderStatuses(providers);
      syncActiveSessions(providers);
      setStatusError(null);
    } catch (error) {
      setStatusError(
        error instanceof Error
          ? error.message
          : "Failed to refresh external agent status.",
      );
    } finally {
      setIsRefreshingStatuses(false);
    }
  }, [syncActiveSessions]);

  useEffect(() => {
    void loadProviderStatuses();
  }, [loadProviderStatuses]);

  useEffect(() => {
    const interval = setInterval(() => {
      void loadProviderStatuses();
    }, 4_000);
    return () => clearInterval(interval);
  }, [loadProviderStatuses]);

  useEffect(() => {
    const entries = Object.entries(activeSessions) as Array<
      [ExternalAgentProviderName, string]
    >;
    if (entries.length === 0) {
      return;
    }

    let cancelled = false;
    const pollSessions = async () => {
      for (const [provider, sessionId] of entries) {
        try {
          const session = await getExternalAgentLoginSession(sessionId);
          if (cancelled) {
            return;
          }
          setLoginSessions((current) => ({
            ...current,
            [provider]: session,
          }));
          if (isTerminalSessionState(session.state)) {
            setActiveSessions((current) => {
              const next = { ...current };
              delete next[provider];
              return next;
            });
            void loadProviderStatuses();
          }
        } catch (error) {
          if (cancelled) {
            return;
          }
          setStatusError(
            error instanceof Error
              ? error.message
              : "Failed to poll external agent login session.",
          );
          setActiveSessions((current) => {
            const next = { ...current };
            delete next[provider];
            return next;
          });
        }
      }
    };

    void pollSessions();
    const interval = setInterval(() => {
      void pollSessions();
    }, 1_500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeSessions, loadProviderStatuses]);

  const handleConnect = useCallback(
    async (provider: ExternalAgentProviderName) => {
      setPendingProvider(provider);
      try {
        const session = await startExternalAgentLogin(provider);
        setLoginSessions((current) => ({
          ...current,
          [provider]: session,
        }));
        setActiveSessions((current) => ({
          ...current,
          [provider]: session.session_id,
        }));
        setStatusError(null);
        void loadProviderStatuses();
      } catch (error) {
        setStatusError(
          error instanceof Error
            ? error.message
            : "Failed to start the worker login flow.",
        );
      } finally {
        setPendingProvider(null);
      }
    },
    [loadProviderStatuses],
  );

  const handleSubmitSessionInput = useCallback(
    async (provider: ExternalAgentProviderName, sessionId: string) => {
      const inputText = sessionInputs[provider]?.trim();
      if (!inputText) {
        setStatusError("Enter the authentication code before submitting it.");
        return;
      }
      setPendingInputProvider(provider);
      try {
        const session = await submitExternalAgentLoginInput(sessionId, {
          input_text: inputText,
        });
        setLoginSessions((current) => ({
          ...current,
          [provider]: session,
        }));
        setActiveSessions((current) => ({
          ...current,
          [provider]: session.session_id,
        }));
        setSessionInputs((current) => ({
          ...current,
          [provider]: "",
        }));
        setStatusError(null);
      } catch (error) {
        setStatusError(
          error instanceof Error
            ? error.message
            : "Failed to submit worker login input.",
        );
      } finally {
        setPendingInputProvider(null);
      }
    },
    [sessionInputs],
  );

  const handleDisconnect = useCallback(
    async (provider: ExternalAgentProviderName) => {
      setPendingDisconnectProvider(provider);
      try {
        const updated = await disconnectExternalAgent(provider);
        setProviderStatuses((current) =>
          sortStatuses(
            current.map((status) =>
              status.provider === provider ? updated : status,
            ),
          ),
        );
        setLoginSessions((current) => {
          const next = { ...current };
          delete next[provider];
          return next;
        });
        setActiveSessions((current) => {
          const next = { ...current };
          delete next[provider];
          return next;
        });
        setSessionInputs((current) => ({
          ...current,
          [provider]: "",
        }));
        setStatusError(null);
        void loadProviderStatuses();
      } catch (error) {
        setStatusError(
          error instanceof Error
            ? error.message
            : "Failed to disconnect the worker auth state.",
        );
      } finally {
        setPendingDisconnectProvider(null);
      }
    },
    [loadProviderStatuses],
  );

  const providerCards = useMemo(
    () =>
      providerStatuses.map((provider) => {
        const currentSession = loginSessions[provider.provider];
        const authoritativeSessionId =
          activeSessions[provider.provider] ?? provider.active_session_id;
        const session =
          currentSession &&
          ((authoritativeSessionId &&
            currentSession.session_id === authoritativeSessionId) ||
            (!authoritativeSessionId &&
              isTerminalSessionState(currentSession.state)))
            ? currentSession
            : null;
        const checkedAt = formatTimestamp(provider.checked_at);
        const lastAuthAt = formatTimestamp(provider.last_auth_ok_at);
        const isBusy =
          pendingProvider === provider.provider ||
          pendingDisconnectProvider === provider.provider ||
          provider.state === "checking" ||
          provider.state === "installing" ||
          provider.state === "authenticating";
        const isReady = provider.state === "ready";
        const buttonLabel = isReady
          ? "Disconnect"
          : provider.state === "not_installed"
            ? "Install and connect"
            : "Connect";
        const showCodexDeviceAuthReminder =
          provider.provider === "codex" &&
          (provider.state === "not_installed" ||
            provider.state === "needs_login") &&
          !activeSessions[provider.provider];
        const showSessionInput =
          (provider.provider === "claude_code" ||
            provider.provider === "gemini") &&
          session !== null &&
          !isTerminalSessionState(session.state);
        const sessionInputPlaceholder =
          provider.provider === "gemini"
            ? "Paste Gemini verification code"
            : "Paste Claude auth code or redirect URL";
        const sessionInputHelpText =
          provider.provider === "gemini"
            ? "Paste the Gemini verification code shown after browser sign-in so the worker can finish authentication."
            : "Paste the full Claude browser value here. Canvas accepts the final redirect URL, a full code#state value, or just the one-time code shown at the end of sign-in.";

        return (
          <Card key={provider.provider} className="border-border/80">
            <CardHeader className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <CardTitle className="text-base">
                    {provider.display_name}
                  </CardTitle>
                  <CardDescription>
                    Worker-scoped OAuth and runtime status for{" "}
                    {provider.display_name}.
                  </CardDescription>
                </div>
                <Badge variant={badgeVariantForState(provider.state)}>
                  {labelForProviderState(provider.state)}
                </Badge>
              </div>
              <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                <span>
                  Version: {provider.resolved_version ?? "Not installed"}
                </span>
                {checkedAt && <span>Checked: {checkedAt}</span>}
                {lastAuthAt && <span>Last auth: {lastAuthAt}</span>}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {provider.detail ??
                  "Canvas will manage the worker-side runtime and OAuth flow."}
              </p>
              {showCodexDeviceAuthReminder && (
                <p className="text-sm text-muted-foreground">
                  Before signing in, make sure{" "}
                  <span className="font-medium text-foreground">
                    &quot;Enable device code authorization for Codex&quot;
                  </span>{" "}
                  is toggled on in ChatGPT Security Settings.
                </p>
              )}

              {session && (
                <div className="rounded-md border bg-muted/30 p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">
                      {labelForSessionState(session.state)}
                    </Badge>
                    {session.resolved_version && (
                      <span className="text-muted-foreground">
                        Runtime {session.resolved_version}
                      </span>
                    )}
                  </div>
                  {session.detail && (
                    <p className="mt-2 text-muted-foreground">
                      {session.detail}
                    </p>
                  )}
                  {session.auth_url && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <p className="text-xs text-muted-foreground">
                        Canvas detected the browser sign-in link.
                      </p>
                      <Button variant="outline" size="sm" asChild>
                        <a
                          href={session.auth_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <ExternalLink className="mr-2 h-3.5 w-3.5" />
                          Open sign-in
                        </a>
                      </Button>
                    </div>
                  )}
                  {session.device_code && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <code className="rounded bg-background px-2 py-1 text-xs">
                        {session.device_code}
                      </code>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => copy(session.device_code ?? "")}
                      >
                        {copiedValue === session.device_code
                          ? "Copied!"
                          : "Copy code"}
                      </Button>
                    </div>
                  )}
                  {session.recent_output && (
                    <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-background p-3 text-xs text-muted-foreground">
                      {session.recent_output}
                    </pre>
                  )}
                  {showSessionInput && (
                    <div className="mt-3 space-y-2">
                      <p className="text-xs text-muted-foreground">
                        {sessionInputHelpText}
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        <Input
                          value={sessionInputs[provider.provider] ?? ""}
                          onChange={(event) => {
                            const value = event.target.value;
                            setSessionInputs((current) => ({
                              ...current,
                              [provider.provider]: value,
                            }));
                          }}
                          placeholder={sessionInputPlaceholder}
                          className="max-w-sm bg-background"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            void handleSubmitSessionInput(
                              provider.provider,
                              session.session_id,
                            );
                          }}
                          disabled={pendingInputProvider === provider.provider}
                        >
                          {pendingInputProvider === provider.provider && (
                            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                          )}
                          Submit code
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  onClick={() => {
                    if (isReady) {
                      void handleDisconnect(provider.provider);
                      return;
                    }
                    void handleConnect(provider.provider);
                  }}
                  disabled={isBusy}
                >
                  {isBusy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {buttonLabel}
                </Button>
                {provider.executable_path && (
                  <code className="rounded bg-muted px-2 py-1 text-xs">
                    {provider.executable_path}
                  </code>
                )}
              </div>
            </CardContent>
          </Card>
        );
      }),
    [
      activeSessions,
      copiedValue,
      copy,
      handleDisconnect,
      handleConnect,
      handleSubmitSessionInput,
      loginSessions,
      pendingDisconnectProvider,
      pendingProvider,
      pendingInputProvider,
      providerStatuses,
      sessionInputs,
    ],
  );

  return (
    <div className="space-y-4">
      {statusError && (
        <Alert variant="destructive">
          <AlertTitle>External agent status failed</AlertTitle>
          <AlertDescription>{statusError}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>External Agents</CardTitle>
              <CardDescription>
                Connect Claude Code, Codex, and Gemini CLI once per workspace
                from Canvas. OAuth happens on the execution worker and is scoped
                to this workspace.
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void queueStatusRefresh();
              }}
              disabled={isRefreshingStatuses}
            >
              {isRefreshingStatuses ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoadingStatuses && providerStatuses.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Loading worker runtime status...
            </p>
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">{providerCards}</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default ExternalAgentsSection;
