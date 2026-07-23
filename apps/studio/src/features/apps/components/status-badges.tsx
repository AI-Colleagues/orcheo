import type { ReactNode } from "react";
import { Badge } from "@/design-system/ui/badge";
import { cn } from "@/lib/utils";
import type { AppHealth, AppState, AppVisibility } from "../data/sample-apps";

type Intent = "neutral" | "success" | "warning" | "destructive" | "info";

const INTENT_CLASSNAME: Record<Intent, string> = {
  neutral: "border-border bg-muted text-muted-foreground",
  success: "border-success/40 bg-success-muted text-success-muted-foreground",
  warning: "border-warning/40 bg-warning-muted text-warning-muted-foreground",
  destructive:
    "border-destructive/40 bg-destructive-muted text-destructive-muted-foreground",
  info: "border-info/40 bg-info-muted text-info-muted-foreground",
};

const INTENT_DOT_CLASSNAME: Record<Intent, string> = {
  neutral: "bg-muted-foreground",
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  info: "bg-info",
};

interface IntentBadgeProps {
  intent: Intent;
  dot?: boolean;
  children: ReactNode;
  className?: string;
}

export function IntentBadge({
  intent,
  dot,
  children,
  className,
}: IntentBadgeProps) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 text-[11px] font-medium capitalize shadow-none",
        INTENT_CLASSNAME[intent],
        className,
      )}
    >
      {dot && (
        <span
          className={cn("h-1.5 w-1.5 rounded-full", INTENT_DOT_CLASSNAME[intent])}
        />
      )}
      {children}
    </Badge>
  );
}

const STATE_INTENT: Record<AppState, Intent> = {
  draft: "neutral",
  published: "success",
  unpublished: "warning",
  suspended: "destructive",
  archived: "neutral",
};

const HEALTH_INTENT: Record<AppHealth, Intent> = {
  healthy: "success",
  unknown: "neutral",
  error: "destructive",
};

export function AppStateBadge({ state }: { state: AppState }) {
  return (
    <IntentBadge intent={STATE_INTENT[state]} dot>
      {state}
    </IntentBadge>
  );
}

export function AppHealthBadge({ health }: { health: AppHealth }) {
  const label = health === "unknown" ? "unknown" : health;
  return (
    <IntentBadge intent={HEALTH_INTENT[health]} dot>
      {label === "error" ? "error" : label}
    </IntentBadge>
  );
}

export function AppVisibilityBadge({
  visibility,
}: {
  visibility: AppVisibility;
}) {
  return (
    <IntentBadge intent={visibility === "public" ? "info" : "neutral"}>
      {visibility}
    </IntentBadge>
  );
}
