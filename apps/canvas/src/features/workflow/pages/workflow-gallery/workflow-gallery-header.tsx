import { ChangeEvent } from "react";
import { Input } from "@/design-system/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import { Search, ArrowUpDown, Clock } from "lucide-react";
import { WorkflowCreateFolderDialog } from "./workflow-create-dialogs";
import { WorkflowFilterPopover } from "./workflow-filter-popover";
import { type WorkflowGalleryFilters, type WorkflowGallerySort } from "./types";

interface WorkflowGalleryHeaderProps {
  searchQuery: string;
  onSearchQueryChange: (value: string) => void;
  sortBy: WorkflowGallerySort;
  onSortChange: (value: WorkflowGallerySort) => void;
  filters: WorkflowGalleryFilters;
  onFiltersChange: (filters: WorkflowGalleryFilters) => void;
  showFilterPopover: boolean;
  onFilterPopoverChange: (open: boolean) => void;
  showNewFolderDialog: boolean;
  onNewFolderDialogChange: (open: boolean) => void;
  newFolderName: string;
  onFolderNameChange: (value: string) => void;
  onCreateFolder: () => void;
  onApplyFilters: () => void;
}

export const WorkflowGalleryHeader = ({
  searchQuery,
  onSearchQueryChange,
  sortBy,
  onSortChange,
  filters,
  onFiltersChange,
  showFilterPopover,
  onFilterPopoverChange,
  showNewFolderDialog,
  onNewFolderDialogChange,
  newFolderName,
  onFolderNameChange,
  onCreateFolder,
  onApplyFilters,
}: WorkflowGalleryHeaderProps) => {
  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onSearchQueryChange(event.target.value);
  };

  return (
    <div className="flex flex-col gap-4 p-4 md:flex-row md:items-center">
      <div className="relative flex-1 md:order-1">
        <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search colleagues..."
          className="pl-10"
          value={searchQuery}
          onChange={handleSearchChange}
        />
      </div>

      <div className="flex items-center gap-2 md:order-2">
        <WorkflowCreateFolderDialog
          open={showNewFolderDialog}
          onOpenChange={onNewFolderDialogChange}
          folderName={newFolderName}
          onFolderNameChange={onFolderNameChange}
          onCreateFolder={onCreateFolder}
        />
      </div>

      <div className="flex items-center gap-2 md:order-3">
        <Select
          value={sortBy}
          onValueChange={(value) => onSortChange(value as WorkflowGallerySort)}
        >
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Sort by" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="updated">
              <div className="flex items-center">
                <ArrowUpDown className="mr-2 h-4 w-4" />
                Last Updated
              </div>
            </SelectItem>
            <SelectItem value="created">
              <div className="flex items-center">
                <Clock className="mr-2 h-4 w-4" />
                Creation Date
              </div>
            </SelectItem>
            <SelectItem value="name">
              <div className="flex items-center">
                <ArrowUpDown className="mr-2 h-4 w-4" />
                Name
              </div>
            </SelectItem>
          </SelectContent>
        </Select>

        <WorkflowFilterPopover
          filters={filters}
          onFiltersChange={onFiltersChange}
          open={showFilterPopover}
          onOpenChange={onFilterPopoverChange}
          onApplyFilters={onApplyFilters}
        />
      </div>
    </div>
  );
};
