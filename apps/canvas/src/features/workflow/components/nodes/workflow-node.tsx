import React, { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import { WorkflowNodeControls } from "./workflow-node-controls";
import {
  deriveInputHandles,
  deriveOutputHandles,
} from "./workflow-node-handle-config";
import { WorkflowNodeHandles } from "./workflow-node-handles";
import { WORKFLOW_NODE_CARD_SIZE_CLASSNAME } from "./node-card-size";
import { getNodeColor, getStatusIcon } from "./workflow-node-style";
import type { WorkflowNodeProps } from "./workflow-node.types";

const WorkflowNode = ({ id, data, selected }: WorkflowNodeProps) => {
  const nodeData = data;
  const [controlsVisible, setControlsVisible] = useState(false);
  const controlsRef = useRef<HTMLDivElement>(null);
  const nodeRef = useRef<HTMLDivElement>(null);

  const {
    label,
    icon,
    status = "idle" as const,
    type,
    isDisabled,
    isSearchMatch = false,
    isSearchActive = false,
  } = nodeData;

  // Handle clicks outside the controls to hide them
  useEffect(() => {
    const targetDocument =
      typeof document !== "undefined" ? document : undefined;
    if (!targetDocument) {
      return;
    }

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (
        controlsRef.current &&
        !controlsRef.current.contains(target) &&
        nodeRef.current &&
        !nodeRef.current.contains(target)
      ) {
        setControlsVisible(false);
      }
    };

    targetDocument.addEventListener("mousedown", handleClickOutside);
    return () => {
      targetDocument.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const nodeColor = getNodeColor(type);
  const statusIcon = getStatusIcon(status);

  const handleMouseEnter = () => {
    setControlsVisible(true);
  };

  const handleMouseLeave = () => {
    setControlsVisible(false);
  };

  const inputHandles = deriveInputHandles(nodeData);
  const outputHandles = deriveOutputHandles(nodeData);

  return (
    <div
      ref={nodeRef}
      data-search-match={isSearchMatch ? "true" : undefined}
      data-search-active={isSearchActive ? "true" : undefined}
      className={cn(
        "group relative border shadow-sm transition-all duration-200",
        nodeColor,
        selected && "ring-2 ring-primary ring-offset-2",
        isSearchMatch &&
          !isSearchActive &&
          "ring-2 ring-sky-400/70 ring-offset-2",
        isSearchActive && "ring-4 ring-sky-500 ring-offset-2",
        isDisabled && "opacity-60",
        WORKFLOW_NODE_CARD_SIZE_CLASSNAME,
        "rounded-xl cursor-pointer",
      )}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      tabIndex={0}
      role="button"
      aria-selected={Boolean(selected)}
    >
      {/* Simple text label */}
      <div className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-xs text-center whitespace-nowrap pointer-events-none">
        <span
          className={cn(
            "px-2 py-0.5 rounded-full transition-colors",
            isSearchActive
              ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
              : isSearchMatch
                ? "bg-sky-500/5 text-sky-600 dark:text-sky-200"
                : undefined,
          )}
        >
          {label}
        </span>
      </div>

      {/* Input handle */}
      <WorkflowNodeHandles handles={inputHandles} type="target" />

      {/* Output handle */}
      <WorkflowNodeHandles handles={outputHandles} type="source" />

      {/* Node content */}
      <div className="h-full w-full flex items-center justify-center relative pointer-events-none">
        {/* Status indicator in corner */}
        <div className="absolute top-1 right-1 pointer-events-auto">
          {statusIcon}
        </div>

        {/* Main icon */}
        <div className="flex items-center justify-center pointer-events-auto">
          {icon ? (
            <div className="scale-125">{icon}</div>
          ) : (
            <div className="text-xs font-medium text-center">
              {label.substring(0, 2)}
            </div>
          )}
        </div>
      </div>

      {/* Hover actions */}
      <WorkflowNodeControls
        ref={controlsRef}
        visible={controlsVisible}
        isDisabled={isDisabled}
        nodeId={id}
        onDelete={(nodeId) => {
          nodeData.onDelete?.(nodeId);
        }}
      />
    </div>
  );
};

export default WorkflowNode;
