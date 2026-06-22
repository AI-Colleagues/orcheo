import { useCallback, useMemo, useRef } from "react";
import type { UseChatKitOptions } from "@openai/chatkit-react";
import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import { cn } from "@/lib/utils";
import type { ColorScheme } from "@/hooks/use-color-scheme";
import type {
  ChatKitStartScreenPrompt as WorkflowChatKitStartScreenPrompt,
  ChatKitSupportedModel as WorkflowChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";
import { buildChatKitAttachmentOptions } from "@features/chatkit/lib/chatkit-attachments";
import {
  buildAuthenticatedChatFetch,
  buildPublicChatFetch,
  getChatKitDomainKey,
  type PublicChatHttpError,
} from "@features/chatkit/lib/chatkit-client";
import { ChatKitSurface } from "@features/chatkit/components/chatkit-surface";
import { buildChatTheme } from "@features/chatkit/lib/chatkit-theme";
import {
  buildModelOptions,
  buildStartScreenPrompts,
} from "@features/chatkit/components/public-chat-config";

interface PublicChatWidgetProps {
  workflowId: string;
  workflowName: string;
  backendBaseUrl?: string;
  /**
   * When true the workflow is published for workspace members only. The widget
   * mints a ChatKit session token (JWT) so the backend can enforce membership.
   */
  requireLogin?: boolean;
  useSessionToken?: boolean;
  /**
   * Workspace slug the workflow belongs to. Sent as `X-Orcheo-Workspace` when
   * minting the session token so membership resolves to the correct workspace.
   */
  workspaceSlug?: string;
  onReady?: () => void;
  onHttpError?: (error: PublicChatHttpError) => void;
  onLog?: (payload: Record<string, unknown>) => void;
  colorScheme?: ColorScheme;
  onThemeRequest?: (scheme: ColorScheme) => Promise<void> | void;
  startScreenPrompts?: WorkflowChatKitStartScreenPrompt[] | null;
  supportedModels?: WorkflowChatKitSupportedModel[] | null;
}

const buildGreeting = (workflowName: string): string =>
  `Welcome to the ${workflowName} public chat.`;

const buildComposerPlaceholder = (workflowName: string): string =>
  `Share a fact for ${workflowName}`;

export function PublicChatWidget({
  workflowId,
  workflowName,
  backendBaseUrl,
  requireLogin = false,
  useSessionToken = requireLogin,
  workspaceSlug,
  onReady,
  onHttpError,
  onLog,
  colorScheme = "light",
  onThemeRequest,
  startScreenPrompts,
  supportedModels,
}: PublicChatWidgetProps) {
  const cachedSessionTokenRef = useRef<{
    token: string;
    expiresAt: number;
  } | null>(null);

  const resolveSessionToken = useCallback(async (): Promise<string> => {
    const cached = cachedSessionTokenRef.current;
    if (cached && cached.expiresAt - Date.now() > 30_000) {
      return cached.token;
    }

    // Use the workflow-scoped session endpoint so the backend resolves the
    // caller's workspace via membership (real OIDC tokens do not carry
    // workspace_ids) and scopes the JWT to this workflow + workspace.
    const url = buildBackendHttpUrl(
      `/api/workflows/${encodeURIComponent(workflowId)}/chatkit/session`,
      backendBaseUrl,
    );
    const headers: Record<string, string> = {};
    if (workspaceSlug) {
      headers["X-Orcheo-Workspace"] = workspaceSlug;
    }
    const response = await authFetch(url, { method: "POST", headers });
    if (!response.ok) {
      throw new Error("Failed to obtain ChatKit session token");
    }
    const data = (await response.json()) as {
      client_secret?: string;
      clientSecret?: string;
      expires_at?: string;
      expiresAt?: string;
    };
    const secret = data.client_secret ?? data.clientSecret;
    if (!secret) {
      throw new Error("ChatKit session response missing client secret");
    }
    const expiresAtRaw = data.expires_at ?? data.expiresAt;
    const expiresAt = expiresAtRaw ? Date.parse(expiresAtRaw) : Number.NaN;
    if (Number.isFinite(expiresAt)) {
      cachedSessionTokenRef.current = { token: secret, expiresAt };
    }
    return secret;
  }, [backendBaseUrl, workflowId, workspaceSlug]);

  const options = useMemo<UseChatKitOptions>(() => {
    const domainKey = getChatKitDomainKey();
    const uploadBase = buildBackendHttpUrl(
      "/api/chatkit/upload",
      backendBaseUrl,
    );
    const uploadUrlObj = new URL(uploadBase);
    uploadUrlObj.searchParams.set("workflow_id", workflowId);
    const uploadUrl = uploadUrlObj.toString();
    const modelOptions = buildModelOptions(supportedModels);

    const fetchImpl =
      requireLogin && useSessionToken
        ? buildAuthenticatedChatFetch({
            workflowId,
            onHttpError,
            metadata: { workflow_name: workflowName },
            getToken: resolveSessionToken,
          })
        : buildPublicChatFetch({
            workflowId,
            onHttpError,
            metadata: { workflow_name: workflowName },
          });

    return {
      api: {
        url: buildBackendHttpUrl("/api/chatkit", backendBaseUrl),
        domainKey,
        fetch: fetchImpl,
        uploadStrategy: { type: "direct", uploadUrl },
      },
      header: {
        enabled: true,
        title: { text: workflowName },
      },
      history: {
        enabled: true,
      },
      theme: buildChatTheme(colorScheme),
      startScreen: {
        greeting: buildGreeting(workflowName),
        prompts: buildStartScreenPrompts(workflowName, startScreenPrompts),
      },
      composer: {
        placeholder: buildComposerPlaceholder(workflowName),
        ...(modelOptions ? { models: modelOptions } : {}),
        tools: [
          {
            id: "search_docs",
            label: "Search docs",
            shortLabel: "Docs",
            placeholderOverride: "Search documentation",
            icon: "book-open",
            pinned: false,
          },
        ],
        attachments: buildChatKitAttachmentOptions(),
      },
      threadItemActions: {
        feedback: false,
      },
      onClientTool: async (invocation) => {
        if (invocation.name === "switch_theme") {
          const requested = invocation.params?.theme;
          if (requested === "light" || requested === "dark") {
            if (onThemeRequest) {
              await onThemeRequest(requested);
              return { success: true };
            }
            return { success: false };
          }
          return { success: false };
        }
        return { success: false };
      },
      onReady,
      onLog,
    };
  }, [
    backendBaseUrl,
    colorScheme,
    onHttpError,
    onLog,
    onReady,
    onThemeRequest,
    startScreenPrompts,
    supportedModels,
    workflowId,
    workflowName,
    requireLogin,
    resolveSessionToken,
    useSessionToken,
  ]);

  return (
    <div
      className={cn(
        "relative h-full w-full overflow-hidden rounded-3xl",
        "border border-slate-200/70 bg-white",
        "shadow-[0_25px_80px_rgba(15,23,42,0.12)]",
        "dark:border-slate-800/70 dark:bg-slate-900",
      )}
    >
      <ChatKitSurface options={options} />
    </div>
  );
}
