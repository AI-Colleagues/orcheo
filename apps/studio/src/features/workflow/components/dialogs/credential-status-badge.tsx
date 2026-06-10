import React from "react";
import { Badge } from "@/design-system/ui/badge";
import { AlertTriangle, CheckCircle2, Circle } from "lucide-react";
import type { CredentialVaultHealthStatus } from "@features/workflow/types/credential-vault";

interface CredentialStatusBadgeProps {
  status?: CredentialVaultHealthStatus;
}

export function CredentialStatusBadge({ status }: CredentialStatusBadgeProps) {
  const normalizedStatus = status ?? "unknown";

  switch (normalizedStatus) {
    case "healthy":
      return (
        <Badge
          variant="outline"
          className="flex items-center gap-1 bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800"
        >
          <CheckCircle2 className="h-3 w-3" />
          Healthy
        </Badge>
      );
    case "unhealthy":
      return (
        <Badge
          variant="outline"
          className="flex items-center gap-1 bg-red-100 text-red-800 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800"
        >
          <AlertTriangle className="h-3 w-3" />
          Unhealthy
        </Badge>
      );
    default:
      return (
        <Badge
          variant="outline"
          className="flex items-center gap-1 bg-muted text-muted-foreground border-border"
        >
          <Circle className="h-3 w-3" />
          Unknown
        </Badge>
      );
  }
}
