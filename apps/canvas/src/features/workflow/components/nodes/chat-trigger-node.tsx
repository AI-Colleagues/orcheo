import React from "react";
import { Button } from "@/design-system/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { NodeProps, Handle, Position } from "@xyflow/react";
import { cn } from "@/lib/utils";
import { MessageSquare } from "lucide-react";
import NodeLabel from "@features/workflow/components/nodes/node-label";
import { WORKFLOW_NODE_CARD_SIZE_CLASSNAME } from "./node-card-size";

export type ChatTriggerNodeData = {
  label: string;
  description?: string;
  status?: "idle" | "running" | "success" | "error";
  onOpenChat?: () => void;
  onLabelChange?: (id: string, newLabel: string) => void;
  [key: string]: unknown;
};

const ChatTriggerNode = ({ data, selected, id }: NodeProps) => {
  const nodeData = data as ChatTriggerNodeData;
  const {
    label,
    description,
    status = "idle",
    onOpenChat,
    onLabelChange,
  } = nodeData;

  const statusColors = {
    idle: "bg-muted",
    running: "bg-blue-500",
    success: "bg-green-500",
    error: "bg-red-500",
  } as const;

  return (
    <div className="flex flex-col items-center">
      <div
        className={cn(
          "relative group rounded-lg border p-3 shadow-sm bg-background",
          WORKFLOW_NODE_CARD_SIZE_CLASSNAME,
          selected
            ? "border-primary ring-2 ring-primary ring-opacity-20"
            : "border-border",
        )}
      >
        {/* Status indicator */}
        <div
          className={cn(
            "absolute top-1 right-1 w-2 h-2 rounded-full",
            statusColors[status],
          )}
        />

        {/* Output handle on right side */}
        <Handle
          type="source"
          position={Position.Right}
          id="out"
          className="!h-3 !w-3 !bg-primary !border-2 !border-background"
        />

        <div className="flex flex-col items-center gap-2">
          <div className="rounded-full bg-primary/10 p-2">
            <MessageSquare className="h-5 w-5 text-primary" />
          </div>

          <div className="text-center">
            {description && (
              <p className="text-xs text-muted-foreground truncate max-w-[150px]">
                {description}
              </p>
            )}
          </div>

          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="secondary"
                  size="sm"
                  className="mt-1 w-full"
                  onClick={onOpenChat}
                >
                  <MessageSquare className="h-3.5 w-3.5 mr-1" />
                  Test Chat
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Open chat interface to test this trigger</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>

      {/* Node label component */}
      <NodeLabel id={id} label={label} onLabelChange={onLabelChange} />
    </div>
  );
};

export default ChatTriggerNode;
