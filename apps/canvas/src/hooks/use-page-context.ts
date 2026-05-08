/**
 * Hook for accessing the browser context from page components.
 */

import { createContext, useContext } from "react";

export interface PageContext {
  page:
    | "gallery"
    | "canvas"
    | "remediations"
    | "execution"
    | "settings"
    | "profile"
    | "help"
    | "other";
  workflowId?: string | null;
  workflowName?: string | null;
  executionId?: string | null;
  activeTab?: string | null;
  vaultOpen?: boolean;
}

interface BrowserContextValue {
  setPageContext: (ctx: PageContext) => void;
  setVaultOpen: (open: boolean) => void;
  pageContext: PageContext;
}

const DEFAULT_PAGE_CONTEXT: PageContext = { page: "other" };

export const BrowserContext = createContext<BrowserContextValue>({
  setPageContext: () => {},
  setVaultOpen: () => {},
  pageContext: DEFAULT_PAGE_CONTEXT,
});

export function usePageContext(): BrowserContextValue {
  return useContext(BrowserContext);
}
