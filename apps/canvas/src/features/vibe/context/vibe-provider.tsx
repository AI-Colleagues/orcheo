import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import { usePageContext } from "@/hooks/use-page-context";
import {
  getSelectedWorkspaceSlug,
  setSelectedWorkspaceSlug,
} from "@/lib/workspace-session";
import { getWorkspaceSlugFromPathname } from "@/lib/workspace-routing";
import { useVibeAgents } from "@features/vibe/hooks/use-vibe-agents";
import { useVibeWorkflow } from "@features/vibe/hooks/use-vibe-workflow";
import { useVibeContextString } from "@features/vibe/hooks/use-vibe-context-string";
import { VibeContext } from "./vibe-context";

export function VibeProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const { readyProviders } = useVibeAgents();
  const { pageContext } = usePageContext();
  const { pathname } = useLocation();
  const contextString = useVibeContextString(pageContext);
  const workspaceSlug = useMemo(
    () => getWorkspaceSlugFromPathname(pathname),
    [pathname],
  );

  useLayoutEffect(() => {
    if (workspaceSlug) {
      setSelectedWorkspaceSlug(workspaceSlug);
      return;
    }

    const selectedSlug = getSelectedWorkspaceSlug();
    if (selectedSlug) {
      setSelectedWorkspaceSlug(selectedSlug);
    }
  }, [workspaceSlug]);

  const { workflowId: agentWorkflowId, isProvisioning } = useVibeWorkflow(
    readyProviders,
    workspaceSlug,
  );

  const toggleOpen = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  const value = useMemo(
    () => ({
      isOpen,
      toggleOpen,
      readyProviders,
      agentWorkflowId,
      isProvisioning,
      contextString,
    }),
    [
      isOpen,
      toggleOpen,
      readyProviders,
      agentWorkflowId,
      isProvisioning,
      contextString,
    ],
  );

  return <VibeContext.Provider value={value}>{children}</VibeContext.Provider>;
}
