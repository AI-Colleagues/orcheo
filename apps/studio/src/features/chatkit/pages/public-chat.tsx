import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Button } from "@/design-system/ui/button";
import { Skeleton } from "@/design-system/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/design-system/ui/toggle-group";
import { getBackendBaseUrl } from "@/lib/config";
import { cn } from "@/lib/utils";
import type { Theme } from "@/lib/theme";
import { useThemePreferences } from "@features/account/components/use-theme-preferences";
import { isAuthenticated } from "@features/auth/lib/auth-session";
import {
  ApiRequestError,
  fetchPublicWorkflow,
} from "@features/workflow/lib/workflow-storage-api";
import type { PublicWorkflowMetadata } from "@features/workflow/lib/workflow-storage.types";
import { PublicChatWidget } from "@features/chatkit/components/public-chat-widget";
import { PublicChatErrorBoundary } from "@features/chatkit/components/public-chat-error-boundary";
import type { PublicChatHttpError } from "@features/chatkit/lib/chatkit-client";

type ColorScheme = "light" | "dark";

type WorkflowState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; workflow: PublicWorkflowMetadata };

const sanitizeWorkflowNameForEmail = (value: string): string =>
  value
    .replace(/[\r\n]+/g, " ")
    .replace(/[<>]/g, "")
    .trim();

export default function PublicChatPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const location = useLocation();
  const [workflowState, setWorkflowState] = useState<WorkflowState>({
    status: workflowId ? "loading" : "error",
    ...(workflowId ? {} : { message: "Workflow identifier missing from URL." }),
  });
  const [chatError, setChatError] = useState<PublicChatHttpError | null>(null);
  const [rateLimitError, setRateLimitError] =
    useState<PublicChatHttpError | null>(null);
  const [isChatReady, setIsChatReady] = useState(false);
  const [systemColorScheme, setSystemColorScheme] = useState<ColorScheme>(() =>
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light",
  );
  const { theme, setTheme } = useThemePreferences({});
  const backendBaseUrl = useMemo(() => getBackendBaseUrl(), []);
  const redirectTo = `${location.pathname}${location.search}${location.hash}`;

  useEffect(() => {
    setChatError(null);
    setRateLimitError(null);
    setIsChatReady(false);
  }, [workflowId]);

  useEffect(() => {
    if (!workflowId) {
      return;
    }
    let cancelled = false;
    setWorkflowState({ status: "loading" });

    fetchPublicWorkflow(workflowId)
      .then((workflow) => {
        if (cancelled) {
          return;
        }
        if (!workflow) {
          setWorkflowState({
            status: "error",
            message: "This workflow does not exist or is no longer available.",
          });
          return;
        }
        if (!workflow.is_public) {
          setWorkflowState({
            status: "error",
            message:
              "This workflow is private. Ask the owner to republish it before trying again.",
          });
          return;
        }
        setWorkflowState({ status: "ready", workflow });
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }
        if (error instanceof ApiRequestError) {
          if (error.status === 403) {
            setWorkflowState({
              status: "error",
              message:
                "This workflow is private. Ask the owner to republish it before trying again.",
            });
            return;
          }
          const message =
            error.status >= 500
              ? "The workflow service is unavailable. Please try again later."
              : "Unable to load workflow metadata.";
          setWorkflowState({ status: "error", message });
          return;
        }
        setWorkflowState({
          status: "error",
          message: "Unexpected error while loading workflow metadata.",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [workflowId]);

  const contactHref = useMemo(() => {
    if (workflowState.status !== "ready") {
      return "mailto:?subject=Orcheo%20workflow%20access";
    }
    const sanitizedName = sanitizeWorkflowNameForEmail(
      workflowState.workflow.name,
    );
    const subject = encodeURIComponent(`Request access to ${sanitizedName}`);
    const link = typeof window !== "undefined" ? window.location.href : "";
    const body = encodeURIComponent(
      `Hi,%0A%0ACould you confirm access for workflow "${sanitizedName}" (handle: ${workflowState.workflow.handle ?? "n/a"}, id: ${workflowState.workflow.id})?%0A%0ALink: ${link}%0A`,
    );
    return `mailto:?subject=${subject}&body=${body}`;
  }, [workflowState]);

  const handleErrorBoundaryReset = useCallback(() => {
    setChatError(null);
    setRateLimitError(null);
    setIsChatReady(false);
  }, []);

  const handleChatHttpError = (error: PublicChatHttpError) => {
    if (error.status === 429 || error.code?.startsWith("chatkit.rate_limit")) {
      setRateLimitError(error);
      return;
    }
    if (error.code === "chatkit.auth.oauth_required") {
      setChatError({
        ...error,
        message:
          "OAuth login is required before this workflow can be used. Sign in and try again.",
      });
      setIsChatReady(false);
      return;
    }
    if (error.status === 401 || error.status === 403) {
      setChatError({
        ...error,
        message:
          "You do not have access to this workflow yet. Ask the owner to confirm it is still published.",
      });
      setIsChatReady(false);
      return;
    }
    setChatError({
      ...error,
      message:
        error.message ||
        "ChatKit could not start this conversation. Please try again shortly.",
    });
    setIsChatReady(false);
  };

  const resolvedColorScheme: ColorScheme =
    theme === "system" ? systemColorScheme : theme;

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (event: MediaQueryListEvent) => {
      setSystemColorScheme(event.matches ? "dark" : "light");
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", handleChange);
    } else {
      media.addListener(handleChange);
    }
    return () => {
      if (typeof media.removeEventListener === "function") {
        media.removeEventListener("change", handleChange);
      } else {
        media.removeListener(handleChange);
      }
    };
  }, []);

  const handleThemeRequest = useCallback(
    (requestedScheme: ColorScheme) => {
      setTheme(requestedScheme);
    },
    [setTheme],
  );

  const renderChatColumn = () => {
    const currentWorkflowName =
      workflowState.status === "ready"
        ? workflowState.workflow.name
        : "this workflow";

    const renderBody = () => {
      if (workflowState.status === "loading") {
        return (
          <div className="space-y-4">
            <Skeleton className="h-10 w-40 rounded-full" />
            <Skeleton className="h-[520px] w-full rounded-3xl" />
          </div>
        );
      }

      if (workflowState.status === "error") {
        return (
          <div className="rounded-3xl border border-slate-200/80 bg-white/70 p-6 text-slate-900 shadow-sm dark:border-slate-800/60 dark:bg-slate-950/40 dark:text-white">
            <p className="text-lg font-semibold">Chat unavailable</p>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              We cannot open a ChatKit session until the workflow loads.
            </p>
            <Button asChild variant="secondary" className="mt-4">
              <Link to="/">Return home</Link>
            </Button>
          </div>
        );
      }

      if (
        workflowState.status === "ready" &&
        workflowState.workflow.require_login &&
        !isAuthenticated()
      ) {
        return (
          <div className="rounded-3xl border border-slate-200/80 bg-white/70 p-6 text-slate-900 shadow-sm dark:border-slate-800/60 dark:bg-slate-950/40 dark:text-white">
            <p className="text-lg font-semibold">Login required</p>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              The owner requires OAuth login before this chat can start.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button asChild>
                <Link to="/login" state={{ from: redirectTo }}>
                  Sign in to continue
                </Link>
              </Button>
              <Button asChild variant="outline">
                <a href={contactHref}>Contact owner</a>
              </Button>
            </div>
          </div>
        );
      }

      return (
        <div className="space-y-4">
          {rateLimitError && (
            <Alert className="border-amber-500/50 bg-amber-500/[0.08] text-amber-100">
              <AlertTitle>Slow down for a moment</AlertTitle>
              <AlertDescription>
                {rateLimitError.message ||
                  "Too many requests were sent for this workflow. Please wait before retrying."}
              </AlertDescription>
              <div className="mt-3">
                <Button
                  size="sm"
                  variant="outline"
                  className="border-amber-400/60 text-amber-100"
                  onClick={() => setRateLimitError(null)}
                >
                  Dismiss
                </Button>
              </div>
            </Alert>
          )}

          {chatError ? (
            <div className="space-y-4 rounded-3xl border border-red-500/40 bg-red-500/10 p-6 text-center">
              <p className="font-medium text-red-100">{chatError.message}</p>
              <div className="flex flex-wrap justify-center gap-3">
                <Button asChild variant="outline">
                  <a href={contactHref}>Contact owner</a>
                </Button>
              </div>
            </div>
          ) : (
            <div className="relative min-h-[520px]">
              {!isChatReady && (
                <div className="absolute inset-0 flex flex-col gap-4 rounded-3xl bg-white/90 p-6 shadow-sm dark:bg-slate-950/80">
                  <Skeleton className="h-10 w-1/2 self-center" />
                  <Skeleton className="h-full w-full" />
                </div>
              )}
              <div
                className={cn(
                  "h-[520px] w-full",
                  isChatReady ? "opacity-100" : "opacity-0",
                  "transition-opacity duration-200",
                )}
              >
                <PublicChatWidget
                  key={workflowState.workflow.id}
                  workflowId={workflowState.workflow.id}
                  workflowName={workflowState.workflow.name}
                  startScreenPrompts={
                    workflowState.workflow.chatkit?.start_screen_prompts
                  }
                  supportedModels={
                    workflowState.workflow.chatkit?.supported_models
                  }
                  backendBaseUrl={backendBaseUrl}
                  onHttpError={handleChatHttpError}
                  onReady={() => setIsChatReady(true)}
                  colorScheme={resolvedColorScheme}
                  onThemeRequest={handleThemeRequest}
                />
              </div>
            </div>
          )}
        </div>
      );
    };

    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-2">
            <p className="text-3xl font-semibold text-slate-900 dark:text-white">
              Chat with “{currentWorkflowName}”
            </p>
            <p className="text-base text-slate-600 dark:text-slate-300">
              Chat sessions open automatically for published workflows unless
              the owner requires OAuth login.
            </p>
            {workflowState.status === "ready" && (
              <div className="flex flex-wrap gap-2 text-xs text-slate-500 dark:text-slate-400">
                {workflowState.workflow.handle && (
                  <code className="rounded bg-slate-100 px-2 py-1 dark:bg-slate-900">
                    handle: {workflowState.workflow.handle}
                  </code>
                )}
                <code className="rounded bg-slate-100 px-2 py-1 dark:bg-slate-900">
                  id: {workflowState.workflow.id}
                </code>
              </div>
            )}
          </div>
          <ThemeToggleButtonGroup value={theme} onChange={setTheme} />
        </div>
        {renderBody()}
      </div>
    );
  };

  return (
    <PublicChatErrorBoundary onReset={handleErrorBoundaryReset}>
      <div className="h-screen overflow-hidden bg-white text-slate-900 dark:bg-slate-950 dark:text-white">
        <div className="mx-auto flex h-full max-w-6xl flex-col px-4 py-6 lg:py-8">
          <div className="flex flex-1 items-center justify-center">
            <div className="w-full max-w-3xl">{renderChatColumn()}</div>
          </div>
        </div>
      </div>
    </PublicChatErrorBoundary>
  );
}

const THEME_OPTIONS: Array<{
  icon: ReactNode;
  label: string;
  value: Theme;
}> = [
  {
    value: "light",
    label: "Light",
    icon: <Sun className="h-4 w-4" />,
  },
  {
    value: "dark",
    label: "Dark",
    icon: <Moon className="h-4 w-4" />,
  },
  {
    value: "system",
    label: "System",
    icon: <Monitor className="h-4 w-4" />,
  },
];

const isThemeValue = (value: string): value is Theme =>
  THEME_OPTIONS.some((option) => option.value === value);

interface ThemeToggleButtonGroupProps {
  className?: string;
  value: Theme;
  onChange: (theme: Theme) => void;
}

function ThemeToggleButtonGroup({
  className,
  value,
  onChange,
}: ThemeToggleButtonGroupProps) {
  const handleThemeChange = (value: string) => {
    if (!value || !isThemeValue(value)) {
      return;
    }
    onChange(value);
  };

  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={handleThemeChange}
      aria-label="Select display theme"
      className={cn(
        "rounded-full border border-slate-200 bg-white/90 px-1 py-1 shadow-[inset_0_-1px_4px_rgba(15,23,42,0.12)] backdrop-blur-sm dark:border-slate-800 dark:bg-slate-950/70",
        className,
      )}
      variant="default"
      size="default"
    >
      {THEME_OPTIONS.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          aria-label={`Use ${option.label.toLowerCase()} theme`}
          className="h-9 w-9 rounded-full border border-transparent p-0 text-slate-400 transition-all hover:bg-transparent hover:text-slate-900 dark:text-slate-400 dark:hover:text-white data-[state=on]:border-slate-900/20 data-[state=on]:bg-slate-900 data-[state=on]:text-white data-[state=on]:shadow-[0_4px_12px_rgba(15,23,42,0.3)] dark:data-[state=on]:border-white/30 dark:data-[state=on]:bg-white dark:data-[state=on]:text-slate-900"
        >
          {option.icon}
          <span className="sr-only">{option.label}</span>
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
