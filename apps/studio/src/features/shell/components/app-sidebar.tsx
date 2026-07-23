import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import { cn } from "@/lib/utils";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import {
  getWorkspaceAppsPath,
  getWorkspaceGalleryPath,
  getWorkspaceSlugFromPathname,
} from "@/lib/workspace-routing";
import {
  ExternalLink,
  Github,
  LayoutGrid,
  PanelLeft,
  Users,
  Vault,
} from "lucide-react";
import ProfileMenu from "./profile-menu";

interface AppSidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onOpenVault: () => void;
}

interface NavItemProps {
  icon: ReactNode;
  label: string;
  active: boolean;
  external?: boolean;
  to?: string;
  onClick?: () => void;
}

function NavItem({ icon, label, active, external, to, onClick }: NavItemProps) {
  const content = (
    <span
      className={cn(
        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      {icon}
      <span className="flex-1 truncate text-left">{label}</span>
      {external && <ExternalLink className="h-3.5 w-3.5 opacity-60" />}
    </span>
  );

  if (to) {
    return (
      <Link to={to} aria-label={label}>
        {content}
      </Link>
    );
  }

  return (
    <button type="button" onClick={onClick} aria-label={label} className="w-full">
      {content}
    </button>
  );
}

const isColleaguesSectionActive = (
  pathname: string,
  workspaceSlug: string | null,
): boolean => {
  if (!workspaceSlug) {
    return pathname === "/";
  }
  const galleryPath = getWorkspaceGalleryPath(workspaceSlug);
  if (pathname === galleryPath) {
    return true;
  }
  const prefix = `/${workspaceSlug}/`;
  if (!pathname.startsWith(prefix)) {
    return false;
  }
  const rest = pathname.slice(prefix.length);
  return !rest.startsWith("apps") && !rest.startsWith("workspace");
};

export default function AppSidebar({
  collapsed,
  onToggleCollapsed,
  onOpenVault,
}: AppSidebarProps) {
  const { pathname } = useLocation();
  const workspaceSlug =
    getWorkspaceSlugFromPathname(pathname) ?? getSelectedWorkspaceSlug();

  const appsPath = getWorkspaceAppsPath(workspaceSlug);

  return (
    <div className="flex h-full flex-col gap-1 p-2">
      <div className="flex items-center gap-2 px-1 py-2">
        <Link
          to={getWorkspaceGalleryPath(workspaceSlug)}
          className="flex min-w-0 items-center gap-2"
        >
          <img src="/favicon.ico" alt="Orcheo" className="h-6 w-6 shrink-0" />
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-sm font-semibold text-foreground">
              Orcheo
            </span>
            <span className="truncate text-[11px] text-muted-foreground">
              by AI Colleagues
            </span>
          </div>
        </Link>
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label="Beta badge"
                className="ml-auto inline-flex cursor-help items-center rounded-md border-0 bg-transparent p-0 text-inherit outline-none"
              >
                <Badge
                  variant="outline"
                  className="border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.2em] text-warning-muted-foreground shadow-none"
                >
                  Beta
                </Badge>
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-sm">
              Beta Disclaimer &amp; Terms: Orcheo Cloud is a free beta for
              evaluation and testing, provided as-is. Data, workflows, and
              credentials may be reset, deleted, or not migrated, so do not
              use it as your only storage for critical production data; read
              the{" "}
              <a
                href="https://ai-colleagues.com/terms"
                target="_blank"
                rel="noreferrer"
                className="font-medium underline underline-offset-2"
              >
                full terms
              </a>
              .
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto py-1">
        <NavItem
          icon={<Users className="h-[18px] w-[18px] shrink-0" />}
          label="AI Colleagues"
          active={isColleaguesSectionActive(pathname, workspaceSlug)}
          to={getWorkspaceGalleryPath(workspaceSlug)}
        />
        <NavItem
          icon={<LayoutGrid className="h-[18px] w-[18px] shrink-0" />}
          label="Apps"
          active={pathname.startsWith(appsPath)}
          to={appsPath}
        />
        <NavItem
          icon={<Vault className="h-[18px] w-[18px] shrink-0" />}
          label="Credential Vault"
          active={false}
          onClick={onOpenVault}
        />
        <NavItem
          icon={<Github className="h-[18px] w-[18px] shrink-0" />}
          label="Feedback & issues"
          active={pathname === "/feedback"}
          external
          to="/feedback"
        />
      </nav>

      <ProfileMenu />
    </div>
  );
}
