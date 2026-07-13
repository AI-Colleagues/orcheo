import React from "react";

import { Alert, AlertDescription, AlertTitle } from "@/design-system/ui/alert";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Separator } from "@/design-system/ui/separator";
import type {
  WorkflowListenerHealth,
  WorkflowListenerMetricsResponse,
} from "@features/workflow/lib/workflow-storage.types";
import { AlertTriangle, RefreshCw, ShieldOff, Zap } from "lucide-react";
import WorkflowHistory from "@features/workflow/components/panels/workflow-history";

export interface SettingsTabContentProps {
  workflowId: string | null;
  workflowName: string;
  workflowDescription: string;
  workflowTags: string[];
  missingCredentials?: string[];
  onWorkflowNameChange: (value: string) => void;
  onWorkflowDescriptionChange: (value: string) => void;
  onTagsChange: (value: string) => void;
  onSaveWorkflowDetails: () => Promise<void>;
  isSavingWorkflowDetails: boolean;
  workflowVersions: Array<{ version: string; createdAt: string }>;
  onRestoreVersion: (version: { version: string; createdAt: string }) => void;
  listeners: WorkflowListenerHealth[];
  listenerMetrics: WorkflowListenerMetricsResponse | null;
  isListenersLoading: boolean;
  isListenersRefreshing: boolean;
  activeListenerSubscriptionId: string | null;
  onRefreshListeners: () => Promise<void>;
  onPauseListener: (subscriptionId: string) => Promise<void>;
  onResumeListener: (subscriptionId: string) => Promise<void>;
}

const listenerStatusBadge = (status: WorkflowListenerHealth["status"]) => {
  switch (status) {
    case "active":
      return { label: "Active", variant: "default" as const };
    case "blocked":
      return { label: "Blocked", variant: "outline" as const };
    case "paused":
      return { label: "Paused", variant: "secondary" as const };
    case "error":
      return { label: "Error", variant: "destructive" as const };
    case "disabled":
      return { label: "Disabled", variant: "outline" as const };
  }
};

const runtimeStatusBadge = (
  status: WorkflowListenerHealth["runtime_status"],
) => {
  switch (status) {
    case "healthy":
      return {
        label: "Healthy",
        className: "bg-success text-success-foreground",
      };
    case "starting":
      return { label: "Starting", className: "bg-info text-info-foreground" };
    case "backoff":
      return {
        label: "Backoff",
        className: "bg-warning text-warning-foreground",
      };
    case "error":
      return {
        label: "Runtime Error",
        className: "bg-destructive text-destructive-foreground",
      };
    case "stopped":
      return {
        label: "Stopped",
        className: "bg-muted-foreground text-background",
      };
    default:
      return { label: "Unknown", className: "bg-muted text-muted-foreground" };
  }
};

const formatDateTime = (value?: string | null) => {
  if (!value) {
    return "Never";
  }

  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  return timestamp.toLocaleString();
};

const platformLabel = (platform: WorkflowListenerHealth["platform"]) => {
  switch (platform) {
    case "telegram":
      return "Telegram";
    case "discord":
      return "Discord";
    case "qq":
      return "QQ";
  }
};

function ListenerSummary({
  metrics,
}: {
  metrics: WorkflowListenerMetricsResponse | null;
}) {
  const items = [
    {
      label: "Total",
      value: metrics?.total_subscriptions ?? 0,
    },
    {
      label: "Healthy",
      value: metrics?.healthy_runtimes ?? 0,
    },
    {
      label: "Paused",
      value: metrics?.paused_subscriptions ?? 0,
    },
    {
      label: "Blocked",
      value: metrics?.blocked_subscriptions ?? 0,
    },
    {
      label: "Alerts",
      value: metrics?.alerts.length ?? 0,
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {items.map((item) => (
        <div
          key={item.label}
          className="rounded-lg border bg-muted/30 px-4 py-3"
        >
          <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
            {item.label}
          </div>
          <div className="mt-2 text-2xl font-semibold">{item.value}</div>
        </div>
      ))}
    </div>
  );
}

export function SettingsTabContent({
  workflowId,
  workflowName,
  workflowDescription,
  workflowTags,
  missingCredentials = [],
  onWorkflowNameChange,
  onWorkflowDescriptionChange,
  onTagsChange,
  onSaveWorkflowDetails,
  isSavingWorkflowDetails,
  workflowVersions,
  onRestoreVersion,
  listeners,
  listenerMetrics,
  isListenersLoading,
  isListenersRefreshing,
  activeListenerSubscriptionId,
  onRefreshListeners,
  onPauseListener,
  onResumeListener,
}: SettingsTabContentProps) {
  const hasMissingCredentials = missingCredentials.length > 0;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-bold">Workflow Settings</h2>
            <p className="text-sm text-muted-foreground">
              Edit the workflow name, description, and tags locally, then save
              them explicitly.
            </p>
          </div>
          <Button
            onClick={() => void onSaveWorkflowDetails()}
            disabled={!workflowId || isSavingWorkflowDetails}
          >
            {isSavingWorkflowDetails ? "Saving..." : "Save details"}
          </Button>
        </div>
        {hasMissingCredentials ? (
          <Alert variant="destructive" className="mb-4">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Missing credentials</AlertTitle>
            <AlertDescription>
              This workflow references credentials that are not in the vault.
              Add them before running:
              <ul className="mt-1 list-disc pl-5">
                {missingCredentials.map((name) => (
                  <li key={name} className="font-mono">
                    {name}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="space-y-4">
          <div className="grid gap-2">
            <label className="text-sm font-medium">Workflow Name</label>
            <input
              type="text"
              className="rounded-md border border-border bg-background px-3 py-2"
              value={workflowName}
              onChange={(event) => onWorkflowNameChange(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <label className="text-sm font-medium">Description</label>
            <textarea
              className="rounded-md border border-border bg-background px-3 py-2"
              rows={3}
              value={workflowDescription}
              onChange={(event) =>
                onWorkflowDescriptionChange(event.target.value)
              }
            />
          </div>
          <div className="grid gap-2">
            <label className="text-sm font-medium">Tags</label>
            <input
              type="text"
              className="rounded-md border border-border bg-background px-3 py-2"
              value={workflowTags.join(", ")}
              onChange={(event) => onTagsChange(event.target.value)}
            />

            <p className="text-xs text-muted-foreground">
              Separate tags with commas
            </p>
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-bold">Listener Control</h2>
            <p className="text-sm text-muted-foreground">
              Monitor and pause or resume private bot listeners for this
              workflow.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => void onRefreshListeners()}
            disabled={
              !workflowId || isListenersLoading || isListenersRefreshing
            }
          >
            <RefreshCw
              className={isListenersRefreshing ? "animate-spin" : undefined}
            />
            Refresh
          </Button>
        </div>

        {!workflowId ? (
          <Alert>
            <ShieldOff className="h-4 w-4" />
            <AlertTitle>Save the workflow first</AlertTitle>
            <AlertDescription>
              Listener subscriptions are created from persisted workflow
              versions. Save this workflow to inspect or control them.
            </AlertDescription>
          </Alert>
        ) : isListenersLoading ? (
          <Card className="border-border/70 bg-muted/40 shadow-none">
            <CardContent className="pt-6 text-sm text-muted-foreground">
              Loading listener health and control state...
            </CardContent>
          </Card>
        ) : listeners.length === 0 ? (
          <Card className="border-border/70 bg-muted/40 shadow-none">
            <CardHeader>
              <CardTitle>No listeners detected</CardTitle>
              <CardDescription>
                The latest workflow version does not expose any listener
                subscriptions.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <div className="space-y-4">
            <ListenerSummary metrics={listenerMetrics} />

            {listenerMetrics && listenerMetrics.alerts.length > 0 && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>Listener alerts</AlertTitle>
                <AlertDescription>
                  <div className="space-y-1">
                    {listenerMetrics.alerts.map((alert) => (
                      <p key={`${alert.subscription_id}-${alert.kind}`}>
                        {platformLabel(alert.platform)}: {alert.detail}
                      </p>
                    ))}
                  </div>
                </AlertDescription>
              </Alert>
            )}

            <div className="grid gap-4">
              {listeners.map((listener) => {
                const status = listenerStatusBadge(listener.status);
                const runtime = runtimeStatusBadge(listener.runtime_status);
                const isActionPending =
                  activeListenerSubscriptionId === listener.subscription_id;
                const canPause = listener.status === "active";
                const canResume =
                  listener.status === "paused" || listener.status === "blocked";

                return (
                  <Card
                    key={listener.subscription_id}
                    className="border-border/70 bg-muted/40 shadow-none"
                  >
                    <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <CardTitle>{listener.node_name}</CardTitle>
                          <Badge variant="outline">
                            {platformLabel(listener.platform)}
                          </Badge>
                          <Badge variant={status.variant}>{status.label}</Badge>
                          <Badge className={runtime.className}>
                            {runtime.label}
                          </Badge>
                        </div>
                        <CardDescription>
                          Bot identity: {listener.bot_identity_key}
                        </CardDescription>
                      </div>

                      {(canPause || canResume) && (
                        <Button
                          variant={canPause ? "outline" : "default"}
                          disabled={isActionPending}
                          onClick={() =>
                            void (canPause
                              ? onPauseListener(listener.subscription_id)
                              : onResumeListener(listener.subscription_id))
                          }
                        >
                          {isActionPending
                            ? canPause
                              ? "Pausing..."
                              : "Resuming..."
                            : canPause
                              ? "Pause"
                              : "Resume"}
                        </Button>
                      )}
                    </CardHeader>
                    <CardContent className="grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
                      <div>
                        <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                          Runtime
                        </div>
                        <div className="mt-1 font-medium">
                          {listener.assigned_runtime ?? "Unassigned"}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                          Last poll
                        </div>
                        <div className="mt-1 font-medium">
                          {formatDateTime(listener.last_polled_at)}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                          Last event
                        </div>
                        <div className="mt-1 font-medium">
                          {formatDateTime(listener.last_event_at)}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                          Failures
                        </div>
                        <div className="mt-1 font-medium">
                          {listener.consecutive_failures}
                        </div>
                      </div>

                      {(listener.runtime_detail || listener.last_error) && (
                        <div className="sm:col-span-2 xl:col-span-4">
                          <div className="rounded-lg border border-warning/40 bg-warning-muted/70 px-3 py-2 text-warning-muted-foreground">
                            <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em]">
                              <Zap className="h-3.5 w-3.5" />
                              Runtime detail
                            </div>
                            <p>
                              {listener.last_error ??
                                listener.runtime_detail ??
                                "No detail available."}
                            </p>
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <Separator />

      <WorkflowHistory
        versions={workflowVersions}
        currentVersion={workflowVersions.at(-1)?.version}
        onRestoreVersion={onRestoreVersion}
      />
    </div>
  );
}
