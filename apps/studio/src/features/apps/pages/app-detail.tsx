import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  ExternalLink,
  Globe,
  Lock,
  MoreHorizontal,
  Pause,
  Rocket,
  Trash2,
  Upload,
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
import { usePageContext } from "@/hooks/use-page-context";
import { getAppsBaseDomain } from "@/lib/config";
import { getWorkspaceAppsPath } from "@/lib/workspace-routing";
import {
  canPublishApp,
  getPublishBlockedReason,
  toggleAppPublish,
  useApp,
} from "../data/apps-store";
import {
  AppHealthBadge,
  AppStateBadge,
  AppVisibilityBadge,
  IntentBadge,
} from "../components/status-badges";

export default function AppDetail() {
  const { workspaceSlug, appId } = useParams<{
    workspaceSlug?: string;
    appId: string;
  }>();
  const navigate = useNavigate();
  const app = useApp(workspaceSlug, appId);
  const { setPageContext } = usePageContext();
  const appsBaseDomain = getAppsBaseDomain();

  useEffect(() => {
    setPageContext({ page: "other" });
  }, [setPageContext]);

  if (!app) {
    return (
      <main className="flex h-full min-h-0 items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">App not found.</p>
      </main>
    );
  }

  const published = app.state === "published";
  const publishAllowed = canPublishApp(app);
  const publishBlockedReason = getPublishBlockedReason(app);

  return (
    <main className="flex h-full min-h-0 flex-col overflow-auto p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <button
          type="button"
          onClick={() => navigate(getWorkspaceAppsPath(workspaceSlug))}
          className="flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Apps
        </button>

        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              {app.name}
            </h1>
            <div className="mt-1.5 flex items-center gap-1.5 font-mono text-sm text-muted-foreground">
              {app.visibility === "public" ? (
                <Globe className="h-3.5 w-3.5" />
              ) : (
                <Lock className="h-3.5 w-3.5" />
              )}
              {app.alias}.{appsBaseDomain}
              <ExternalLink className="h-3 w-3 opacity-60" />
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <AppVisibilityBadge visibility={app.visibility} />
              <AppStateBadge state={app.state} />
              <AppHealthBadge health={app.health} />
            </div>
          </div>

          {published ? (
            <Button
              variant="outline"
              onClick={() => toggleAppPublish(workspaceSlug, app.id)}
            >
              <Pause className="mr-2 h-4 w-4" />
              Unpublish
            </Button>
          ) : (
            <Button
              disabled={!publishAllowed}
              title={publishBlockedReason ?? undefined}
              onClick={() => toggleAppPublish(workspaceSlug, app.id)}
            >
              <Rocket className="mr-2 h-4 w-4" />
              Publish
            </Button>
          )}

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem>
                <Upload className="mr-2 h-4 w-4" />
                Upload deployment
              </DropdownMenuItem>
              <DropdownMenuItem>
                <Download className="mr-2 h-4 w-4" />
                Export bundle
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="text-destructive">
                <Trash2 className="mr-2 h-4 w-4" />
                Delete app
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {[
            { label: "Deployments", value: app.deployments.length },
            { label: "Workflow bindings", value: app.bindings.length },
            { label: "Data collections", value: app.collections.length },
            { label: "Last updated", value: app.updated },
          ].map((stat) => (
            <Card key={stat.label} className="p-4">
              <div className="text-xl font-semibold text-foreground">
                {stat.value}
              </div>
              <div className="text-xs text-muted-foreground">{stat.label}</div>
            </Card>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card className="flex flex-col gap-3 p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-foreground">Deployments</h2>
              <Button variant="outline" size="sm">
                <Upload className="mr-2 h-3.5 w-3.5" />
                Upload
              </Button>
            </div>
            {app.deployments.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No deployments yet. Upload a prebuilt bundle to publish this
                app.
              </p>
            ) : (
              <div className="flex flex-col divide-y divide-border">
                {app.deployments.map((deployment) => (
                  <div
                    key={deployment.id}
                    className="flex items-center justify-between gap-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-foreground">
                        {deployment.version}
                      </div>
                      <div className="truncate font-mono text-xs text-muted-foreground">
                        {deployment.digest} · {deployment.size} ·{" "}
                        {deployment.files} files
                      </div>
                      <div className="font-mono text-xs text-muted-foreground/70">
                        {deployment.created}
                      </div>
                    </div>
                    {deployment.active ? (
                      <IntentBadge intent="success" dot>
                        active
                      </IntentBadge>
                    ) : (
                      <Button variant="ghost" size="sm">
                        Roll back
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <div className="flex flex-col gap-4">
            <Card className="flex flex-col gap-3 p-4">
              <h2 className="font-semibold text-foreground">
                Workflow bindings
              </h2>
              {app.bindings.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No bindings yet. Bind a workflow so the app can call it as a
                  backend.
                </p>
              ) : (
                <div className="flex flex-col divide-y divide-border">
                  {app.bindings.map((binding) => (
                    <div
                      key={binding.name}
                      className="flex items-center justify-between gap-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="font-medium text-foreground">
                          {binding.name}
                        </div>
                        <div className="truncate font-mono text-xs text-muted-foreground">
                          {binding.workflow} · v{binding.version} ·{" "}
                          {binding.rate}
                        </div>
                      </div>
                      <IntentBadge
                        intent={
                          binding.access === "anonymous" ? "warning" : "neutral"
                        }
                      >
                        {binding.access}
                      </IntentBadge>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card className="flex flex-col gap-3 p-4">
              <h2 className="font-semibold text-foreground">
                Data collections
              </h2>
              {app.collections.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No collections configured.
                </p>
              ) : (
                <div className="flex flex-col divide-y divide-border">
                  {app.collections.map((collection) => (
                    <div
                      key={collection.name}
                      className="flex items-center justify-between gap-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="font-medium text-foreground">
                          {collection.name}
                        </div>
                        <div className="truncate font-mono text-xs text-muted-foreground">
                          read: {collection.read} · write: {collection.write}
                        </div>
                      </div>
                      <IntentBadge intent="neutral">
                        {collection.access}
                      </IntentBadge>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </div>
      </div>
    </main>
  );
}
