import type { ReactNode } from "react";

import type { NodeIconKey } from "@features/workflow/lib/node-icons";

export interface SidebarNodeData {
  label: string;
  type: string;
  description: string;
  iconKey: NodeIconKey;
  backendType?: string;
  [key: string]: unknown;
}

export interface SidebarNode {
  id: string;
  name: string;
  description: string;
  iconKey: NodeIconKey;
  icon?: ReactNode;
  type: string;
  backendType?: string;
  data: SidebarNodeData;
}
