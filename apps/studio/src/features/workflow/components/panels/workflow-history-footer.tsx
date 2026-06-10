import React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/design-system/ui/button";

interface WorkflowHistoryFooterProps {
  totalVersions: number;
}

export default function WorkflowHistoryFooter({
  totalVersions,
}: WorkflowHistoryFooterProps) {
  return (
    <div className="flex items-center justify-between p-4 border-t border-border">
      <div className="text-sm text-muted-foreground">
        {totalVersions} versions
      </div>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon" className="h-8 w-8">
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button variant="outline" size="icon" className="h-8 w-8">
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
