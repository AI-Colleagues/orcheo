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
          className="bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800"
        >
          <Lock className="h-3 w-3 mr-1" />
          Scoped
        </Badge>
      );
    case "shared":
      return (
        <Badge
          variant="outline"
          className="bg-purple-100 text-purple-800 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800"
        >
          <Users className="h-3 w-3 mr-1" />
          Shared
        </Badge>
      );
    default:
      return null;
  }
}
