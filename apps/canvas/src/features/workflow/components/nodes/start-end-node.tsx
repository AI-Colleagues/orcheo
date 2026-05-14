import { cn } from "@/lib/utils";
import { Play, Square } from "lucide-react";
import { NodeProps, Handle, Position } from "@xyflow/react";
import { WORKFLOW_NODE_CARD_SIZE_CLASSNAME } from "./node-card-size";

export type StartEndNodeData = {
  label: string;
  type: "start" | "end";
  description?: string;
  onLabelChange?: (id: string, newLabel: string) => void;
  [key: string]: unknown;
};

const StartEndNode = ({ data, selected }: NodeProps) => {
  const nodeData = data as StartEndNodeData;
  const { label, type } = nodeData;

  const nodeColors = {
    start:
      "bg-emerald-50 border-emerald-300 dark:bg-emerald-950/30 dark:border-emerald-800/50",
    end: "bg-rose-50 border-rose-300 dark:bg-rose-950/30 dark:border-rose-800/50",
  } as const;

  return (
    <div
      className={cn(
        "group relative rounded-xl border-2 shadow-sm transition-all duration-200 flex items-center justify-center",
        WORKFLOW_NODE_CARD_SIZE_CLASSNAME,
        nodeColors[type],
        selected && "ring-2 ring-primary ring-offset-2",
      )}
    >
      {/* Simple text label */}
      <div className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-xs text-center whitespace-nowrap">
        <span className="px-2 py-0.5 rounded-full transition-colors">
          {label}
        </span>
      </div>

      {/* Only show input handle on end node */}
      {type === "end" && (
        <Handle
          type="target"
          position={Position.Left}
          className="!h-2 !w-2 !bg-primary !border-2 !border-background"
        />
      )}

      {/* Only show output handle on start node */}
      {type === "start" && (
        <Handle
          type="source"
          position={Position.Right}
          className="!h-2 !w-2 !bg-primary !border-2 !border-background"
        />
      )}

      {/* Node icon */}
      <div className="flex items-center justify-center">
        {type === "start" ? (
          <Play className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <Square className="h-5 w-5 text-rose-600 dark:text-rose-400" />
        )}
      </div>
    </div>
  );
};

export default StartEndNode;
