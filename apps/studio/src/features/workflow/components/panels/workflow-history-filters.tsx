import React from "react";
import { Search } from "lucide-react";

import { Input } from "@/design-system/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import type { WorkflowVersionRecord } from "@features/workflow/lib/workflow-storage";

interface WorkflowHistoryFiltersProps {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  compareVersionId: string | null;
  onSelectCompareVersion: (value: string | null) => void;
  versions: WorkflowVersionRecord[];
  selectedVersionId: string | null;
}

export default function WorkflowHistoryFilters({
  searchQuery,
  onSearchChange,
  compareVersionId,
  onSelectCompareVersion,
  versions,
  selectedVersionId,
}: WorkflowHistoryFiltersProps) {
  return (
    <div className="flex items-center gap-2 p-4 border-b border-border">
      <div className="relative flex-1">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />

        <Input
          placeholder="Search versions..."
          className="pl-8"
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </div>
      <Select
        value={compareVersionId ?? ""}
        onValueChange={(value) => onSelectCompareVersion(value || null)}
      >
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="Compare with..." />
        </SelectTrigger>
        <SelectContent>
          {versions
            .filter((version) => version.id !== selectedVersionId)
            .map((version) => (
              <SelectItem key={version.id} value={version.id}>
                {version.version}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
    </div>
  );
}
