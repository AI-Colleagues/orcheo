import React from "react";

import { getNodeIcon } from "@features/workflow/lib/node-icons";

import type { SidebarNode } from "./sidebar-node-item.types";

interface NodeItemProps {
  node: SidebarNode;
  onSelect?: (node: SidebarNode) => void;
}

export const SidebarNodeItem = ({ node, onSelect }: NodeItemProps) => {
  const icon = node.icon ?? getNodeIcon(node.iconKey);

  const handleClick = () => {
    onSelect?.(node);
  };

  const handleDragStart: React.DragEventHandler<HTMLDivElement> = (event) => {
    const serializableNode = { ...node, icon: undefined };
    event.dataTransfer.setData(
      "application/reactflow",
      JSON.stringify(serializableNode),
    );
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      className="flex items-start gap-3 p-2 rounded-md hover:bg-accent cursor-pointer"
      onClick={handleClick}
      draggable
      onDragStart={handleDragStart}
    >
      <div className="mt-0.5">{icon}</div>
      <div>
        <div className="font-medium text-sm">{node.name}</div>
        <div className="text-xs text-muted-foreground">{node.description}</div>
      </div>
    </div>
  );
};
