/**
 * BrowserContextProvider — wraps the app to relay Canvas page context
 * to the local `orcheo browser-aware` HTTP server.
 */

import { useMemo, type ReactNode } from "react";
import { useBrowserContext } from "@/hooks/use-browser-context";
import { BrowserContext } from "@/hooks/use-page-context";

export function BrowserContextProvider({ children }: { children: ReactNode }) {
  const { setPageContext, setVaultOpen, pageContext } = useBrowserContext();
  const value = useMemo(
    () => ({ setPageContext, setVaultOpen, pageContext }),
    [setPageContext, setVaultOpen, pageContext],
  );
  return (
    <BrowserContext.Provider value={value}>{children}</BrowserContext.Provider>
  );
}
