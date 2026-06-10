import React from "react";
import { History, RotateCcw } from "lucide-react";

import { Button } from "@/design-system/ui/button";

interface WorkflowHistoryHeaderProps {
  onRestoreVersion: () => void;
  onCompareVersions: () => void;
  canRestore: boolean;
  canCompare: boolean;
}

export default function WorkflowHistoryHeader({
  onRestoreVersion,
  onCompareVersions,
  canRestore,
  canCompare,
}: WorkflowHistoryHeaderProps) {
  return (
    <div className="flex items-center justify-between p-4 border-b border-border">
      <div className="flex items-center gap-2">
        <History className="h-5 w-5" />

        <h3 className="font-medium">Version History</h3>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onRestoreVersion}
          disabled={!canRestore}
        >
          <RotateCcw className="h-4 w-4 mr-2" />
          Restore
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onCompareVersions}
          disabled={!canCompare}
        >
          Compare
        </Button>
      </div>
    </div>
  );
}
