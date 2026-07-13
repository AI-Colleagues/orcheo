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
          className="flex items-center gap-1 border-success/30 bg-success-muted text-success-muted-foreground"
        >
          <CheckCircle2 className="h-3 w-3" />
          Healthy
        </Badge>
      );
    case "unhealthy":
      return (
        <Badge
          variant="outline"
          className="flex items-center gap-1 border-destructive/30 bg-destructive-muted text-destructive-muted-foreground"
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
