import { Link, useLocation } from "react-router-dom";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { Badge } from "@/design-system/ui/badge";
import { getSelectedWorkspaceSlug } from "@/lib/workspace-session";
import {
  getWorkspaceGalleryPath,
  getWorkspaceSlugFromPathname,
} from "@/lib/workspace-routing";

export default function StudioBrand() {
  const { pathname } = useLocation();
  const workspaceSlug =
    getWorkspaceSlugFromPathname(pathname) ?? getSelectedWorkspaceSlug();

  return (
    <div className="flex min-w-0 items-center gap-3">
      <Link
        to={getWorkspaceGalleryPath(workspaceSlug)}
        className="flex min-w-0 items-center gap-2 whitespace-nowrap"
      >
        <img src="/favicon.ico" alt="Orcheo Logo" className="h-6 w-6" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-semibold text-foreground">Orcheo</span>
          <span className="text-[11px] text-muted-foreground">
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
              className="inline-flex cursor-help items-center rounded-md border-0 bg-transparent p-0 text-inherit outline-none"
            >
              <Badge
                variant="outline"
                className="border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-amber-700 shadow-none dark:text-amber-300"
              >
                Beta
              </Badge>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs">
            Beta Disclaimer & Terms: This environment is experimental. Data
            created here may be reset, deleted, or not migrated to the launched
            version. Orcheo Cloud is currently offered as a free beta
            environment for evaluation and testing purposes only, provided on
            an "as-is" and "as-available" basis without warranties of any kind.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
