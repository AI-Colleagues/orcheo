import { useEffect, useState } from "react";

import { Badge } from "@/design-system/ui/badge";
import { getActiveWorkspace, type ActiveWorkspaceResponse } from "@/lib/api";

export default function ActiveWorkspaceIndicator() {
  const [workspace, setWorkspace] = useState<ActiveWorkspaceResponse | null>(
    null,
  );

  useEffect(() => {
    let active = true;

    void getActiveWorkspace()
      .then((payload) => {
        if (active) {
          setWorkspace(payload);
        }
      })
      .catch(() => {
        // Leave the header uncluttered when the backend cannot resolve workspace state.
      });

    return () => {
      active = false;
    };
  }, []);

  if (!workspace) {
    return null;
  }

  return (
    <Badge
      variant="outline"
      className="hidden items-center gap-1 border-dashed bg-background/80 text-foreground sm:inline-flex"
      data-testid="active-workspace-indicator"
    >
      <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        Workspace
      </span>
      <span className="max-w-[10rem] truncate font-medium">
        {workspace.slug}
      </span>
    </Badge>
  );
}
