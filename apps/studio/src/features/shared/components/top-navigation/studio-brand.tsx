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
                className="border-warning/40 bg-warning/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-warning-muted-foreground shadow-none"
              >
                Beta
              </Badge>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-sm">
            Beta Disclaimer & Terms: Orcheo Cloud is a free beta for evaluation
            and testing, provided as-is. Data, workflows, and credentials may be
            reset, deleted, or not migrated, so do not use it as your only
            storage for critical production data; read the{" "}
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
    </div>
  );
}
