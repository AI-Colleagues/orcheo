import {
  Download,
  Globe,
  Lock,
  MoreHorizontal,
  Pause,
  Rocket,
  Trash2,
  Upload,
  LayoutGrid,
} from "lucide-react";
import { Button } from "@/design-system/ui/button";
import { Card } from "@/design-system/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import { getAppsBaseDomain } from "@/lib/config";
import { canPublishApp } from "../data/apps-store";
import type { HostedApp } from "../data/sample-apps";
import {
  AppHealthBadge,
  AppStateBadge,
  AppVisibilityBadge,
} from "./status-badges";

interface AppCardProps {
  app: HostedApp;
  onOpen: (app: HostedApp) => void;
  onTogglePublish: (app: HostedApp) => void;
}

export function AppCard({ app, onOpen, onTogglePublish }: AppCardProps) {
  const activeDeployment = app.deployments.find((d) => d.active);
  const publishAllowed = canPublishApp(app);
  const appsBaseDomain = getAppsBaseDomain();

  return (
    <Card
      className="flex cursor-pointer flex-col gap-3 border-border/70 bg-card p-4 shadow-sm transition-transform hover:-translate-y-0.5"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(app)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(app);
        }
      }}
    >
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <LayoutGrid className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-foreground">
            {app.name}
          </div>
          <div className="mt-0.5 flex items-center gap-1 truncate font-mono text-xs text-muted-foreground">
            {app.visibility === "public" ? (
              <Globe className="h-3 w-3 shrink-0" />
            ) : (
              <Lock className="h-3 w-3 shrink-0" />
            )}
            {app.alias}.{appsBaseDomain}
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0"
              aria-label="App actions"
              onClick={(event) => event.stopPropagation()}
            >
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            onClick={(event) => event.stopPropagation()}
          >
            <DropdownMenuItem>
              <Upload className="mr-2 h-4 w-4" />
              Upload deployment
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Download className="mr-2 h-4 w-4" />
              Export bundle
            </DropdownMenuItem>
            {app.state === "published" ? (
              <DropdownMenuItem onClick={() => onTogglePublish(app)}>
                <Pause className="mr-2 h-4 w-4" />
                Unpublish
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem
                disabled={!publishAllowed}
                onClick={() => onTogglePublish(app)}
              >
                <Rocket className="mr-2 h-4 w-4" />
                Publish
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive">
              <Trash2 className="mr-2 h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <AppVisibilityBadge visibility={app.visibility} />
        <AppStateBadge state={app.state} />
        <AppHealthBadge health={app.health} />
      </div>

      <div className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
        <span>
          {activeDeployment ? activeDeployment.version : "no active build"}
        </span>
        <span>·</span>
        <span>{app.deployments.length} deploys</span>
        <span className="ml-auto">{app.updated}</span>
      </div>
    </Card>
  );
}
