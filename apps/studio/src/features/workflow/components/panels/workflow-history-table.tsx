import React from "react";
import { GitCommit } from "lucide-react";

import { Badge } from "@/design-system/ui/badge";
import { ScrollArea } from "@/design-system/ui/scroll-area";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/design-system/ui/table";
import { cn } from "@/lib/utils";
import type { WorkflowVersionRecord } from "@features/workflow/lib/workflow-storage";

interface WorkflowHistoryTableProps {
  filteredVersions: WorkflowVersionRecord[];
  selectedVersionId: string | null;
  currentVersion?: string;
  searchQuery: string;
  onSelectVersion: (versionId: string) => void;
}

const statusBadgeClass =
  "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";

const summaryBadgeClasses: Record<"added" | "removed" | "modified", string> = {
  added: statusBadgeClass,
  removed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  modified: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
};

const SummaryBadge = ({
  type,
  value,
}: {
  type: keyof typeof summaryBadgeClasses;
  value: number;
}) => {
  if (value <= 0) {
    return null;
  }

  const prefix = type === "added" ? "+" : type === "removed" ? "-" : "~";

  return (
    <Badge className={summaryBadgeClasses[type]}>{`${prefix}${value}`}</Badge>
  );
};

export default function WorkflowHistoryTable({
  filteredVersions,
  selectedVersionId,
  currentVersion,
  searchQuery,
  onSelectVersion,
}: WorkflowHistoryTableProps) {
  const renderStatusBadge = (version: WorkflowVersionRecord) => {
    if (version.version !== currentVersion) {
      return null;
    }

    return <Badge className={statusBadgeClass}>Current</Badge>;
  };

  return (
    <ScrollArea className="flex-1 h-[400px]">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[100px]">Version</TableHead>
            <TableHead>Message</TableHead>
            <TableHead>Author</TableHead>
            <TableHead>Date</TableHead>
            <TableHead className="text-right">Changes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredVersions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center py-8">
                <div className="text-muted-foreground">
                  No versions found
                  {searchQuery && (
                    <p className="text-sm">Try adjusting your search query</p>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ) : (
            filteredVersions.map((version) => {
              const isSelected = selectedVersionId === version.id;
              return (
                <TableRow
                  key={version.id}
                  className={cn("cursor-pointer", isSelected && "bg-muted")}
                  onClick={() => onSelectVersion(version.id)}
                >
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      <GitCommit className="h-4 w-4 text-muted-foreground" />

                      {version.version}
                      {renderStatusBadge(version)}
                    </div>
                  </TableCell>
                  <TableCell>{version.message}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="h-6 w-6 rounded-full overflow-hidden bg-muted">
                        <img
                          src={version.author.avatar}
                          alt={version.author.name}
                          className="h-full w-full object-cover"
                        />
                      </div>
                      {version.author.name}
                    </div>
                  </TableCell>
                  <TableCell>
                    {new Date(version.timestamp).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <SummaryBadge
                        type="added"
                        value={version.summary.added}
                      />
                      <SummaryBadge
                        type="removed"
                        value={version.summary.removed}
                      />
                      <SummaryBadge
                        type="modified"
                        value={version.summary.modified}
                      />
                    </div>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
    </ScrollArea>
  );
}
