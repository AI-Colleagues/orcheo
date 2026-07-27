import { useEffect, useState, type ChangeEvent } from "react";
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
import { getWorkspaceAppsPath } from "@/lib/workspace-routing";
import { getHostedAppAddress } from "../data/sample-apps";
import {
  canPublishApp,
  getPublishBlockedReason,
  toggleAppPublish,
  uploadAppBundle,
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
  const { app, loading, error } = useApp(appId, workspaceSlug);
  const { setPageContext } = usePageContext();
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [publishing, setPublishing] = useState(false);

  useEffect(() => {
    setPageContext({ page: "other" });
  }, [setPageContext]);

  if (loading || !app) {
    return (
      <main className="flex h-full min-h-0 items-center justify-center p-8">
        <p
          className={
            error ? "text-sm text-destructive" : "text-sm text-muted-foreground"
          }
        >
          {error ?? (loading ? "Loading app…" : "App not found.")}
        </p>
      </main>
    );
  }

  const published = app.state === "published";
  const readyDeployment = app.deployments.find(
    (deployment) => deployment.status === "ready",
  );
  const manifestBindings = readyDeployment?.manifestBindings;
  const reviewBindings = manifestBindings ?? app.bindings;
  const manifestManaged =
    manifestBindings !== null && manifestBindings !== undefined;
  const publishAllowed = canPublishApp(app);
  const publishBlockedReason = getPublishBlockedReason(app);
  const handlePublish = async () => {
    setActionError(null);
    setPublishing(true);
    try {
      await toggleAppPublish(app);
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Unable to update publication.",
      );
    } finally {
      setPublishing(false);
    }
  };
  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const bundle = input.files?.[0];
    if (!bundle) return;
    setActionError(null);
    setUploading(true);
    try {
      await uploadAppBundle(app.id, bundle);
      input.value = "";
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Unable to upload deployment.",
      );
    } finally {
      setUploading(false);
    }
  };

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
              {published ? (
                <a
                  href={app.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex min-w-0 items-center gap-1.5 underline-offset-4 hover:text-foreground hover:underline"
                >
                  <span className="truncate">
                    {getHostedAppAddress(app.url)}
                  </span>
                  <ExternalLink className="h-3 w-3 shrink-0 opacity-60" />
                </a>
              ) : (
                <span className="truncate">{getHostedAppAddress(app.url)}</span>
              )}
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
              disabled={publishing}
              onClick={() => void handlePublish()}
            >
              <Pause className="mr-2 h-4 w-4" />
              {publishing ? "Updating…" : "Unpublish"}
            </Button>
          ) : (
            <Button
              disabled={publishing || !publishAllowed}
              title={publishBlockedReason ?? undefined}
              onClick={() => void handlePublish()}
            >
              <Rocket className="mr-2 h-4 w-4" />
              {publishing ? "Publishing…" : "Publish"}
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

        {actionError ? (
          <p role="alert" className="text-sm text-destructive">
            {actionError}
          </p>
        ) : null}

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {[
            { label: "Deployments", value: app.deployments.length },
            { label: "Workflow bindings", value: reviewBindings.length },
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
              <input
                aria-label="Upload deployment ZIP"
                type="file"
                accept=".zip,application/zip"
                disabled={uploading}
                onChange={(event) => void handleUpload(event)}
                className="max-w-56 text-xs text-muted-foreground file:mr-2 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-foreground hover:file:bg-accent"
              />
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
                      {deployment.manifestBindings ? (
                        <div className="text-xs text-muted-foreground">
                          {deployment.manifestBindings.length} declared workflow{" "}
                          {deployment.manifestBindings.length === 1
                            ? "binding"
                            : "bindings"}
                        </div>
                      ) : null}
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
              {manifestManaged ? (
                <p className="text-xs text-muted-foreground">
                  Declared by the newest ready deployment. Publish resolves and
                  pins these workflow versions in the release.
                </p>
              ) : null}
              {reviewBindings.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {manifestManaged
                    ? "This deployment declares no workflow bindings."
                    : "No bindings yet. Bind a workflow so the app can call it as a backend."}
                </p>
              ) : (
                <div className="flex flex-col divide-y divide-border">
                  {reviewBindings.map((binding) => (
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
                        {binding.digest ? (
                          <div className="truncate font-mono text-[11px] text-muted-foreground/70">
                            executable sha256:{binding.digest.slice(0, 16)}
                          </div>
                        ) : null}
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

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card className="flex flex-col gap-3 p-4">
            <h2 className="font-semibold text-foreground">
              Draft versus live access
            </h2>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Draft revision</span>
              <span className="font-mono">{app.permissionRevision}</span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Published revision</span>
              <span className="font-mono">
                {app.publishedPermissionRevision ?? "not published"}
              </span>
            </div>
            {app.publishedPermissionRevision !== null &&
            app.publishedPermissionRevision !== undefined &&
            app.publishedPermissionRevision !== app.permissionRevision ? (
              <IntentBadge intent="warning">
                review required before publish
              </IntentBadge>
            ) : (
              <IntentBadge intent="success">access is in sync</IntentBadge>
            )}
          </Card>

          <Card className="flex flex-col gap-3 p-4">
            <h2 className="font-semibold text-foreground">Recent activity</h2>
            {app.audit?.length ? (
              <div className="flex flex-col divide-y divide-border">
                {app.audit
                  .slice(-8)
                  .reverse()
                  .map((event) => (
                    <div key={event.id} className="py-2 text-sm">
                      <div className="font-medium text-foreground">
                        {event.action}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {event.actor} · {event.created}
                      </div>
                    </div>
                  ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No activity has been recorded for this app.
              </p>
            )}
          </Card>
        </div>
      </div>
    </main>
  );
}
