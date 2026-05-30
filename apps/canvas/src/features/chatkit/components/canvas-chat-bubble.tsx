import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Button } from "@/design-system/ui/button";
import { Skeleton } from "@/design-system/ui/skeleton";
import { cn } from "@/lib/utils";
import { Loader2, MessageSquare } from "lucide-react";
import type { UseChatKitOptions } from "@openai/chatkit-react";
import { useChatInterfaceOptions } from "@features/shared/components/chat-interface-options";
import type { ChatParticipant } from "@features/shared/components/chat-interface.types";
import type { ChatSessionStatus } from "@features/workflow/pages/workflow-canvas/hooks/use-workflow-chat";
import { recordChatTelemetry } from "@features/chatkit/lib/telemetry";
import { useColorScheme } from "@/hooks/use-color-scheme";
import { buildChatTheme } from "@features/chatkit/lib/chatkit-theme";
import {
  buildModelOptions,
  buildStartScreenPrompts,
} from "@features/chatkit/components/public-chat-config";
import type {
  ChatKitStartScreenPrompt,
  ChatKitSupportedModel,
} from "@features/workflow/lib/workflow-storage.types";

const ChatKitSurfaceLazy = lazy(() =>
  import("@features/chatkit/components/chatkit-surface").then((module) => ({
    default: module.ChatKitSurface,
  })),
);

const MINIMAP_SELECTOR = ".react-flow__panel.react-flow__minimap";
const DEFAULT_FLOATING_OFFSET = 96;
const MINIMAP_GAP = 16;

const DEFAULT_PANEL_WIDTH = 448;
const DEFAULT_PANEL_HEIGHT = 520;
const MIN_PANEL_WIDTH = 320;
const MIN_PANEL_HEIGHT = 360;
const PANEL_VIEWPORT_MARGIN = 24;

interface CanvasChatBubbleProps {
  title: string;
  user: ChatParticipant;
  ai: ChatParticipant;
  workflowId: string | null;
  chatkitWorkflowId?: string | null;
  sessionPayload?: Record<string, unknown>;
  backendBaseUrl?: string | null;
  startScreenPrompts?: ChatKitStartScreenPrompt[] | null;
  supportedModels?: ChatKitSupportedModel[] | null;
  getClientSecret: (currentSecret: string | null) => Promise<string>;
  sessionStatus: ChatSessionStatus;
  sessionError: string | null;
  onRetry: () => Promise<string>;
  onResponseStart?: () => void;
  onResponseEnd?: () => void;
  onClientTool?: (tool: {
    name: string;
    params: Record<string, unknown>;
  }) => Promise<Record<string, unknown>>;
  onDismiss?: () => void;
  onOpen?: () => void;
  isExternallyOpen: boolean;
}

export function CanvasChatBubble({
  title,
  user,
  ai,
  workflowId,
  chatkitWorkflowId,
  sessionPayload,
  backendBaseUrl,
  startScreenPrompts,
  supportedModels,
  getClientSecret,
  sessionStatus,
  sessionError,
  onRetry,
  onResponseStart,
  onResponseEnd,
  onClientTool,
  onDismiss,
  onOpen,
  isExternallyOpen,
}: CanvasChatBubbleProps) {
  const [isPanelOpen, setIsPanelOpen] = useState(false);
  const [shouldLoadChat, setShouldLoadChat] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [floatingOffset, setFloatingOffset] = useState(DEFAULT_FLOATING_OFFSET);
  const [panelSize, setPanelSize] = useState({
    width: DEFAULT_PANEL_WIDTH,
    height: DEFAULT_PANEL_HEIGHT,
  });
  const resizeStateRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    startWidth: number;
    startHeight: number;
  } | null>(null);
  const colorScheme = useColorScheme();
  const modelOptions = useMemo(
    () => buildModelOptions(supportedModels),
    [supportedModels],
  );
  const prompts = useMemo(
    () => buildStartScreenPrompts(title, startScreenPrompts),
    [startScreenPrompts, title],
  );

  useEffect(() => {
    if (isExternallyOpen) {
      setIsPanelOpen(true);
      setShouldLoadChat(true);
    } else {
      setIsPanelOpen(false);
    }
  }, [isExternallyOpen]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }

    let resizeObserver: ResizeObserver | null = null;
    let mutationObserver: MutationObserver | null = null;
    let observedElement: Element | null = null;

    const updateOffset = () => {
      if (typeof window === "undefined") {
        return;
      }

      const minimap = document.querySelector<HTMLElement>(MINIMAP_SELECTOR);

      if (!minimap) {
        setFloatingOffset(DEFAULT_FLOATING_OFFSET);
        return;
      }

      const rect = minimap.getBoundingClientRect();
      const offset = window.innerHeight - rect.top + MINIMAP_GAP;
      setFloatingOffset(Math.max(offset, DEFAULT_FLOATING_OFFSET));

      if (
        typeof ResizeObserver !== "undefined" &&
        minimap !== observedElement
      ) {
        resizeObserver?.disconnect();
        resizeObserver = new ResizeObserver(() => updateOffset());
        resizeObserver.observe(minimap);
        observedElement = minimap;
      }
    };

    const handleResize = () => updateOffset();

    updateOffset();
    window.addEventListener("resize", handleResize);

    if (typeof MutationObserver !== "undefined" && document.body) {
      mutationObserver = new MutationObserver(() => updateOffset());
      mutationObserver.observe(document.body, {
        childList: true,
        subtree: true,
      });
    }

    return () => {
      window.removeEventListener("resize", handleResize);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
    };
  }, []);

  const floatingPositionStyle = useMemo(
    () => ({
      bottom: floatingOffset,
    }),
    [floatingOffset],
  );

  const handleResizePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      // Only react to the primary (left) button.
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      // Route every subsequent pointer event to this element until release, so
      // fast moves over the ChatKit iframe / off-window can't drop the pointerup.
      event.currentTarget.setPointerCapture(event.pointerId);
      resizeStateRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startWidth: panelSize.width,
        startHeight: panelSize.height,
      };
    },
    [panelSize],
  );

  const handleResizePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const state = resizeStateRef.current;
      if (!state || event.pointerId !== state.pointerId) {
        return;
      }

      // Panel is anchored bottom-right, so dragging the handle up/left grows it.
      const deltaX = state.startX - event.clientX;
      const deltaY = state.startY - event.clientY;

      const maxWidth =
        typeof window === "undefined"
          ? Number.POSITIVE_INFINITY
          : window.innerWidth - PANEL_VIEWPORT_MARGIN * 2;
      const maxHeight =
        typeof window === "undefined"
          ? Number.POSITIVE_INFINITY
          : window.innerHeight - PANEL_VIEWPORT_MARGIN * 2;

      const nextWidth = Math.min(
        Math.max(state.startWidth + deltaX, MIN_PANEL_WIDTH),
        Math.max(maxWidth, MIN_PANEL_WIDTH),
      );
      const nextHeight = Math.min(
        Math.max(state.startHeight + deltaY, MIN_PANEL_HEIGHT),
        Math.max(maxHeight, MIN_PANEL_HEIGHT),
      );

      setPanelSize({ width: nextWidth, height: nextHeight });
    },
    [],
  );

  const handleResizePointerEnd = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const state = resizeStateRef.current;
      if (!state || event.pointerId !== state.pointerId) {
        return;
      }
      resizeStateRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    },
    [],
  );

  const handleFabClick = () => {
    setIsPanelOpen(true);
    setShouldLoadChat(true);
    recordChatTelemetry("canvas.chat.open", {
      workflowId,
      source: "bubble",
    });
    onOpen?.();
  };

  const handleCollapse = () => {
    setIsPanelOpen(false);
    recordChatTelemetry("canvas.chat.close", {
      workflowId,
      source: "bubble",
    });
  };

  const handleDismiss = () => {
    handleCollapse();
    onDismiss?.();
  };

  const handleRetry = useCallback(async () => {
    setIsRetrying(true);
    try {
      await onRetry();
    } finally {
      setIsRetrying(false);
    }
  }, [onRetry]);

  const chatKitOptions: UseChatKitOptions = useChatInterfaceOptions({
    chatkitOptions: {
      header: {
        enabled: true,
        title: {
          enabled: true,
          text: title,
        },
        rightAction: {
          icon: "close",
          onClick: handleDismiss,
        },
      },
      composer: {
        placeholder: `Ask ${title} a question`,
        ...(modelOptions ? { models: modelOptions } : {}),
      },
      startScreen: {
        greeting: `You're chatting with ${title}.`,
        prompts,
      },
      onClientTool,
      theme: buildChatTheme(colorScheme),
    },
    getClientSecret,
    backendBaseUrl: backendBaseUrl ?? undefined,
    sessionPayload: {
      ...sessionPayload,
      workflowId: chatkitWorkflowId ?? workflowId,
      workflowLabel: title,
    },
    workflowId: chatkitWorkflowId ?? workflowId,
    title,
    user,
    ai,
    initialMessages: [
      {
        id: "canvas-chat-greeting",
        content: `You're chatting with ${title}.`,
        sender: {
          ...ai,
          isAI: true,
        },
        timestamp: new Date(),
      },
    ],
    onResponseStart,
    onResponseEnd,
  });

  const statusView = useMemo(() => {
    if (sessionStatus === "loading") {
      return (
        <div className="flex h-full flex-col items-center justify-center space-y-3 text-sm text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <p>Starting a secure chat session…</p>
        </div>
      );
    }

    if (sessionStatus === "error") {
      return (
        <Alert variant="destructive" className="mt-4 text-left">
          <AlertTitle>Chat unavailable</AlertTitle>
          <AlertDescription className="mt-1 text-sm">
            {sessionError ||
              "We couldn't reach the chat service. Try again in a moment."}
          </AlertDescription>
          <div className="mt-3 flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRetry}
              disabled={isRetrying}
            >
              {isRetrying && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Retry
            </Button>
          </div>
        </Alert>
      );
    }

    return null;
  }, [handleRetry, isRetrying, sessionError, sessionStatus]);

  return (
    <>
      {!isPanelOpen && (
        <Button
          className="fixed right-6 z-50 h-14 w-14 rounded-full shadow-xl"
          style={floatingPositionStyle}
          size="icon"
          onClick={handleFabClick}
        >
          <MessageSquare className="h-5 w-5" />
          <span className="sr-only">Open ChatKit</span>
        </Button>
      )}

      {isPanelOpen && (
        <div
          className="fixed right-6 z-50 flex max-h-[calc(100vh-2rem)] max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-border bg-card text-foreground shadow-2xl"
          style={{
            ...floatingPositionStyle,
            width: panelSize.width,
            height: panelSize.height,
          }}
        >
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize chat window"
            onPointerDown={handleResizePointerDown}
            onPointerMove={handleResizePointerMove}
            onPointerUp={handleResizePointerEnd}
            onPointerCancel={handleResizePointerEnd}
            className="group absolute left-0 top-0 z-10 flex h-7 w-7 cursor-nwse-resize touch-none items-center justify-center rounded-tl-2xl"
          >
            <span className="pointer-events-none flex h-4 w-4 -translate-x-px -translate-y-px items-center justify-center">
              <svg
                viewBox="0 0 16 16"
                fill="none"
                className="h-full w-full text-muted-foreground/40 transition-colors group-hover:text-muted-foreground"
              >
                <path
                  d="M14 2H5a3 3 0 0 0-3 3v9"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </span>
          </div>
          <div className="flex-1 overflow-hidden px-2 py-2">
            {statusView}
            {sessionStatus !== "error" && shouldLoadChat && (
              <Suspense
                fallback={
                  <div className="flex h-full w-full flex-col gap-3">
                    <Skeleton className="h-10 w-1/2 self-center" />
                    <Skeleton className="h-full w-full" />
                  </div>
                }
              >
                <ChatKitSurfaceLazy
                  options={chatKitOptions}
                  className={cn(
                    sessionStatus !== "ready" &&
                      "pointer-events-none opacity-50",
                  )}
                />
              </Suspense>
            )}
          </div>
        </div>
      )}
    </>
  );
}
