/**
 * BrowserContextProvider — relays Canvas page context to the local
 * `orcheo browser-aware` HTTP server so coding agents stay in sync.
 *
 * Usage:
 *   const { setPageContext } = useBrowserContext();
 *   setPageContext({ page: "canvas", workflowId: "abc", workflowName: "My Flow" });
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { PageContext } from "@/hooks/use-page-context";

const CONTEXT_URL = "http://localhost:3333/context";
const HEARTBEAT_ACTIVE_MS = 5_000;
const HEARTBEAT_IDLE_MS = 30_000;
const MAX_RETRIES = 2;
const INITIAL_BACKOFF_MS = 500;
const BRIDGE_UNAVAILABLE_WARNING =
  "Browser context bridge is unavailable. Start `orcheo browser-aware` and refresh the page to reconnect.";

function getOrCreateSessionId(): string {
  const key = "orcheo_browser_session_id";
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(key, id);
  }
  return id;
}

async function postContext(
  sessionId: string,
  ctx: PageContext,
  focused: boolean,
): Promise<boolean> {
  const body = JSON.stringify({
    session_id: sessionId,
    page: ctx.page,
    workflow_id: ctx.workflowId ?? null,
    workflow_name: ctx.workflowName ?? null,
    focused,
    timestamp: new Date().toISOString(),
  });

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(CONTEXT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (res.ok || res.status < 500) return true;
    } catch {
      // Network error — retry with backoff if attempts remain.
    }
    if (attempt < MAX_RETRIES) {
      await new Promise((r) =>
        setTimeout(r, INITIAL_BACKOFF_MS * 2 ** attempt),
      );
    }
  }

  return false;
}

export function useBrowserContext() {
  const sessionIdRef = useRef<string>(getOrCreateSessionId());
  const contextRef = useRef<PageContext>({ page: "other" });
  const heartbeatRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disabledRef = useRef(false);
  const warningShownRef = useRef(false);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearTimeout(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  const disableBridge = useCallback(() => {
    if (disabledRef.current) return;

    disabledRef.current = true;
    stopHeartbeat();
    if (!warningShownRef.current) {
      console.warn(BRIDGE_UNAVAILABLE_WARNING);
      warningShownRef.current = true;
    }
  }, [stopHeartbeat]);

  const sendContext = useCallback(
    async (ctx: PageContext, focused: boolean) => {
      if (disabledRef.current) return;

      const ok = await postContext(sessionIdRef.current, ctx, focused);
      if (!ok) {
        disableBridge();
      }
    },
    [disableBridge],
  );

  const startHeartbeat = useCallback(() => {
    if (heartbeatRef.current || disabledRef.current) return;

    const tick = async () => {
      await sendContext(contextRef.current, document.hasFocus());
      if (disabledRef.current) {
        heartbeatRef.current = null;
        return;
      }

      // Reduce frequency when the tab is not focused.
      const interval = document.hasFocus()
        ? HEARTBEAT_ACTIVE_MS
        : HEARTBEAT_IDLE_MS;
      heartbeatRef.current = setTimeout(() => {
        void tick();
      }, interval);
    };
    heartbeatRef.current = setTimeout(() => {
      void tick();
    }, HEARTBEAT_ACTIVE_MS);
  }, [sendContext]);

  // Visibility / focus listeners — start/stop heartbeat.
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        // Fire immediately on becoming visible, then start heartbeat.
        void sendContext(contextRef.current, document.hasFocus());
        startHeartbeat();
      } else {
        stopHeartbeat();
      }
    };

    const handleFocus = () => {
      void sendContext(contextRef.current, true);
    };

    const handleBlur = () => {
      void sendContext(contextRef.current, false);
    };

    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);

    // Start heartbeat if tab is already visible.
    if (document.visibilityState === "visible") {
      startHeartbeat();
    }

    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
      stopHeartbeat();
    };
  }, [sendContext, startHeartbeat, stopHeartbeat]);

  const [pageContext, setPageContextState] = useState<PageContext>({
    page: "other",
  });

  const setPageContext = useCallback(
    (ctx: PageContext) => {
      contextRef.current = ctx;
      setPageContextState(ctx);
      // Fire immediately on context change.
      void sendContext(ctx, document.hasFocus());
    },
    [sendContext],
  );

  const setVaultOpen = useCallback(
    (open: boolean) => {
      const updated = { ...contextRef.current, vaultOpen: open };
      contextRef.current = updated;
      setPageContextState(updated);
      void sendContext(updated, document.hasFocus());
    },
    [sendContext],
  );

  return { setPageContext, setVaultOpen, pageContext };
}
