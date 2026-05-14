import React, { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Maximize2,
} from "lucide-react";
import { NodeProps, Handle, Position } from "@xyflow/react";
import { cn } from "@/lib/utils";
import { Button } from "@/design-system/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import NodeLabel from "@features/workflow/components/nodes/node-label";
import { WORKFLOW_NODE_CARD_SIZE_CLASSNAME } from "./node-card-size";

export type GroupNodeData = {
  label: string;
  description?: string;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
  onOpenInNewTab?: () => void;
  nodeCount?: number;
  color?: string;
  onLabelChange?: (id: string, newLabel: string) => void;
  [key: string]: unknown;
};

const GroupNode = ({ data, selected, id }: NodeProps) => {
  const nodeData = data as GroupNodeData;
  const {
    label,
    description,
    isCollapsed = false,
    onToggleCollapse,
    onOpenInNewTab,
    nodeCount = 0,
    color = "blue",
    onLabelChange,
  } = nodeData;

  const [isHovered, setIsHovered] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  const colorStyles = {
    blue: "bg-blue-50 border-blue-200 dark:bg-blue-950/30 dark:border-blue-800/50",
    purple:
      "bg-purple-50 border-purple-200 dark:bg-purple-950/30 dark:border-purple-800/50",
    amber:
      "bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-800/50",
    green:
      "bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800/50",
    indigo:
      "bg-indigo-50 border-indigo-200 dark:bg-indigo-950/30 dark:border-indigo-800/50",
  } as const;

  const colorStyle =
    color in colorStyles
      ? colorStyles[color as keyof typeof colorStyles]
      : colorStyles.blue;

  const handleDoubleClick = () => {
    setIsExpanded(!isExpanded);
  };

  return (
    <div className="flex flex-col items-center">
      <div
        className={cn(
          "group relative rounded-xl border-2 shadow-sm transition-all duration-200",
          colorStyle,
          selected && "ring-2 ring-primary ring-offset-2",
          isExpanded
            ? "min-w-[250px] min-h-[150px]"
            : WORKFLOW_NODE_CARD_SIZE_CLASSNAME,
        )}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onDoubleClick={handleDoubleClick}
      >
        {/* Input handle */}
        <Handle
          type="target"
          position={Position.Left}
          className="!h-3 !w-3 !bg-primary !border-2 !border-background"
        />

        {/* Output handle */}
        <Handle
          type="source"
          position={Position.Right}
          className="!h-3 !w-3 !bg-primary !border-2 !border-background"
        />

        {isExpanded ? (
          <div className="p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-5 w-5 p-0"
                  onClick={onToggleCollapse}
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-4 w-4" />
                  ) : (
                    <ChevronDown className="h-4 w-4" />
                  )}
                </Button>
                <div className="font-medium truncate">{label}</div>
              </div>
              <div className="flex items-center gap-1">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={onOpenInNewTab}
                      >
                        <ExternalLink className="h-3 w-3" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>Open in new tab</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>

                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={() => setIsExpanded(false)}
                      >
                        <Maximize2 className="h-3 w-3" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>Collapse group</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>
            {description && (
              <p className="text-xs text-muted-foreground mt-1">
                {description}
              </p>
            )}
            {!isCollapsed && (
              <div className="mt-2 p-2 border border-dashed border-border/50 rounded bg-background/50 min-h-[80px] flex items-center justify-center">
                <span className="text-xs text-muted-foreground">
                  {nodeCount > 0
                    ? `Contains ${nodeCount} nodes`
                    : "Drop nodes here to group them"}
                </span>
              </div>
            )}
          </div>
        ) : (
          <div className="h-full w-full flex flex-col items-center justify-center relative">
            {/* Group icon */}
            <div className="flex items-center justify-center">
              <div className="text-xs font-medium">
                {nodeCount > 0 ? nodeCount : "G"}
              </div>
            </div>

            {/* Tooltip for group name */}
            {isHovered && (
              <div className="absolute -bottom-8 left-1/2 transform -translate-x-1/2 bg-background border border-border rounded-md shadow-md p-1 text-xs">
                {label}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Node label component - only show when collapsed */}
      {!isExpanded && (
        <NodeLabel id={id} label={label} onLabelChange={onLabelChange} />
      )}
    </div>
  );
};

export default GroupNode;
