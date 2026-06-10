import { useCallback, useMemo } from "react";

import { authFetch } from "@/lib/auth-fetch";
import { buildBackendHttpUrl } from "@/lib/config";
import { buildChatKitAttachmentOptions } from "@features/chatkit/lib/chatkit-attachments";
import {
  buildPublicChatFetch,
  getChatKitDomainKey,
} from "@features/chatkit/lib/chatkit-client";
import type { CustomApiConfig, HostedApiConfig } from "@openai/chatkit";
import type { UseChatKitOptions } from "@openai/chatkit-react";

import type { ChatInterfaceProps } from "./chat-interface.types";

type ChatKitApiOptions = NonNullable<UseChatKitOptions["api"]>;
type OptionalApiConfig = UseChatKitOptions["api"] | undefined;

type OptionalHandlers = Pick<
  ChatInterfaceProps,
  "onResponseStart" | "onResponseEnd" | "onThreadChange" | "onLog"
>;

type OptionParams = Pick<
  ChatInterfaceProps,
  | "chatkitOptions"
  | "getClientSecret"
  | "backendBaseUrl"
  | "workflowId"
  | "sessionPayload"
  | "title"
  | "user"
  | "ai"
  | "initialMessages"
> &
  OptionalHandlers;

const normalizeWorkflowId = (value: unknown): string | null => {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed || null;
};

const deriveWorkflowIdFromPayload = (
  payload?: Record<string, unknown>,
): string | null => {
  if (!payload) {
    return null;
  }
  const camel = normalizeWorkflowId(payload["workflowId"]);
  if (camel) {
    return camel;
  }
  return normalizeWorkflowId(payload["workflow_id"]);
};

const hasGetClientSecret = (api: OptionalApiConfig): api is HostedApiConfig =>
  Boolean(api && typeof api.getClientSecret === "function");

const hasCustomApiConfig = (api: OptionalApiConfig): api is CustomApiConfig =>
  Boolean(
    api &&
    typeof api === "object" &&
    typeof api.url === "string" &&
    typeof api.domainKey === "string",
  );

const useInitialGreeting = (
  initialMessages: ChatInterfaceProps["initialMessages"],
  aiId: string,
) =>
  useMemo(() => {
    const greeting = initialMessages?.find(
      (message) =>
        typeof message.content === "string" && message.sender?.id === aiId,
    );
    return greeting?.content as string | undefined;
  }, [aiId, initialMessages]);

const useSessionSecretResolver = ({
  getClientSecret,
  backendBaseUrl,
  sessionPayload,
  title,
  user,
  ai,
}: OptionParams) =>
  useCallback(
    async (currentSecret: string | null) => {
      if (getClientSecret) {
        return getClientSecret(currentSecret);
      }

      const url = buildBackendHttpUrl("/api/chatkit/session", backendBaseUrl);
      const response = await authFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_client_secret: currentSecret,
          currentClientSecret: currentSecret,
          user,
          assistant: ai,
          metadata: { title, ...sessionPayload },
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to fetch ChatKit client secret");
      }

      const data = (await response.json()) as {
        client_secret?: string;
        clientSecret?: string;
      };

      const secret = data.client_secret ?? data.clientSecret;
      if (!secret) {
        throw new Error("ChatKit session response missing client secret");
      }

      return secret;
    },
    [ai, backendBaseUrl, getClientSecret, sessionPayload, title, user],
  );

const useHandlerComposer = () =>
  useCallback(
    <T extends unknown[]>(
      ...handlers: Array<((...args: T) => void) | undefined>
    ) => {
      const valid = handlers.filter(Boolean) as Array<(...args: T) => void>;
      if (valid.length === 0) {
        return undefined;
      }
      return (...args: T) => {
        valid.forEach((handler) => handler(...args));
      };
    },
    [],
  );

export const useChatInterfaceOptions = ({
  chatkitOptions,
  getClientSecret,
  backendBaseUrl,
  workflowId,
  sessionPayload,
  title,
  user,
  ai,
  initialMessages,
  onResponseStart,
  onResponseEnd,
  onThreadChange,
  onLog,
}: OptionParams): UseChatKitOptions => {
  const resolveSessionSecret = useSessionSecretResolver({
    chatkitOptions,
    getClientSecret,
    backendBaseUrl,
    sessionPayload,
    title,
    user,
    ai,
    initialMessages,
    onResponseStart,
    onResponseEnd,
    onThreadChange,
    onLog,
  });
  const composeHandlers = useHandlerComposer();
  const initialGreeting = useInitialGreeting(initialMessages, ai.id);
  const resolvedWorkflowId = useMemo(
    () =>
      normalizeWorkflowId(workflowId) ??
      deriveWorkflowIdFromPayload(sessionPayload),
    [sessionPayload, workflowId],
  );
  const defaultApiOptions = useMemo<
    Partial<ChatKitApiOptions> | undefined
  >(() => {
    if (!resolvedWorkflowId) {
      return undefined;
    }
    const url = buildBackendHttpUrl("/api/chatkit", backendBaseUrl);
    const domainKey = getChatKitDomainKey();
    const uploadBase = buildBackendHttpUrl(
      "/api/chatkit/upload",
      backendBaseUrl,
    );
    const uploadUrlObj = new URL(uploadBase);
    uploadUrlObj.searchParams.set("workflow_id", resolvedWorkflowId);
    const uploadStrategy = {
      type: "direct",
      uploadUrl: uploadUrlObj.toString(),
    } as const;
    if (typeof window === "undefined") {
      return { url, domainKey, uploadStrategy };
    }
    // If a session getter is provided, prefer JWT-authenticated requests to the
    // custom backend. Otherwise, fall back to cookie/public mode.
    const makeJwtFetch = (
      tokenResolver: (current: string | null) => Promise<string>,
    ): typeof fetch => {
      const base = buildPublicChatFetch({
        workflowId: resolvedWorkflowId,
        metadata: sessionPayload,
      });
      return async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const token = await tokenResolver(null);
        const headers = new Headers(init.headers ?? {});
        if (token) {
          headers.set("Authorization", `Bearer ${token}`);
        }
        return base(input, { ...init, headers });
      };
    };

    const fetchImpl = getClientSecret
      ? makeJwtFetch(getClientSecret)
      : buildPublicChatFetch({
          workflowId: resolvedWorkflowId,
          metadata: sessionPayload,
        });

    return {
      url,
      domainKey,
      fetch: fetchImpl,
      uploadStrategy,
    };
  }, [backendBaseUrl, getClientSecret, resolvedWorkflowId, sessionPayload]);
  const providedApi = chatkitOptions?.api;
  const providedHostedSecret = useMemo(() => {
    if (hasGetClientSecret(providedApi)) {
      return providedApi.getClientSecret;
    }
    return null;
  }, [providedApi]);
  const providedCustomApi = useMemo(() => {
    if (hasCustomApiConfig(providedApi)) {
      return providedApi;
    }
    return undefined;
  }, [providedApi]);
  // When a workflow is present (Studio bubble), force Custom API mode so
  // requests go to our backend instead of the hosted ChatKit service.
  const hostedSecret = resolvedWorkflowId
    ? null
    : (getClientSecret ??
      providedHostedSecret ??
      (!defaultApiOptions && !providedCustomApi ? resolveSessionSecret : null));

  return useMemo(() => {
    const merged = {
      ...(chatkitOptions as UseChatKitOptions),
    } as UseChatKitOptions;
    let apiConfig: ChatKitApiOptions;
    if (hostedSecret) {
      apiConfig = {
        getClientSecret: hostedSecret,
      };
    } else {
      const mergedCustom: Partial<CustomApiConfig> = {
        ...(defaultApiOptions ?? {}),
        ...(providedCustomApi ?? {}),
      };
      apiConfig = mergedCustom as ChatKitApiOptions;
    }

    merged.api = apiConfig;

    if (!merged.header) {
      merged.header = {
        enabled: true,
        title: { enabled: true, text: title },
      };
    }

    if (!merged.startScreen && initialGreeting) {
      merged.startScreen = { greeting: initialGreeting };
    }

    if (resolvedWorkflowId) {
      merged.composer = {
        ...(merged.composer ?? {}),
        attachments:
          merged.composer?.attachments ?? buildChatKitAttachmentOptions(),
      };
    }

    merged.onResponseStart = composeHandlers(
      chatkitOptions?.onResponseStart,
      onResponseStart,
    );
    merged.onResponseEnd = composeHandlers(
      chatkitOptions?.onResponseEnd,
      onResponseEnd,
    );
    merged.onThreadChange = composeHandlers(
      chatkitOptions?.onThreadChange,
      onThreadChange,
    );
    merged.onThreadLoadStart = chatkitOptions?.onThreadLoadStart;
    merged.onThreadLoadEnd = chatkitOptions?.onThreadLoadEnd;
    merged.onLog = composeHandlers(chatkitOptions?.onLog, onLog);
    merged.onError = chatkitOptions?.onError;

    return merged;
  }, [
    chatkitOptions,
    composeHandlers,
    defaultApiOptions,
    hostedSecret,
    initialGreeting,
    onLog,
    onResponseEnd,
    onResponseStart,
    onThreadChange,
    resolvedWorkflowId,
    providedCustomApi,
    title,
  ]);
};
