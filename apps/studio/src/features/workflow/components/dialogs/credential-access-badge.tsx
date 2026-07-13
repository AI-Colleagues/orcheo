import React from "react";
import { Badge } from "@/design-system/ui/badge";
import { Lock, Users } from "lucide-react";

interface CredentialAccessBadgeProps {
  access: string;
}

export function CredentialAccessBadge({ access }: CredentialAccessBadgeProps) {
  switch (access) {
    case "scoped":
      return (
        <Badge
          variant="outline"
          className="border-info/30 bg-info-muted text-info-muted-foreground"
        >
          <Lock className="h-3 w-3 mr-1" />
          Scoped
        </Badge>
      );
    case "shared":
      return (
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/10 text-primary-active"
        >
          <Users className="h-3 w-3 mr-1" />
          Shared
        </Badge>
      );
    default:
      return null;
  }
}
