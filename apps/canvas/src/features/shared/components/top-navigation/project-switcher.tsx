import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/design-system/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";
import { ChevronDown, Folder, Plus } from "lucide-react";
import {
  getWorkflowRouteRef,
  listWorkflows,
  type StoredWorkflow,
  WORKFLOW_STORAGE_EVENT,
} from "@features/workflow/lib/workflow-storage";

export default function ProjectSwitcher() {
  const [workflows, setWorkflows] = useState<StoredWorkflow[]>([]);

  useEffect(() => {
    let isMounted = true;

    const loadWorkflows = async (forceRefresh = false) => {
      try {
        const items = await listWorkflows({ forceRefresh });
        if (isMounted) {
          setWorkflows(items);
        }
      } catch (error) {
        console.error("Failed to load workflows for project switcher", error);
      }
    };

    void loadWorkflows();

    const targetWindow = typeof window !== "undefined" ? window : undefined;
    if (!targetWindow) {
      return () => {
        isMounted = false;
      };
    }

    const onWorkflowStorageUpdate = () => {
      void loadWorkflows(true);
    };

    targetWindow.addEventListener(
      WORKFLOW_STORAGE_EVENT,
      onWorkflowStorageUpdate,
    );

    return () => {
      isMounted = false;
      targetWindow.removeEventListener(
        WORKFLOW_STORAGE_EVENT,
        onWorkflowStorageUpdate,
      );
    };
  }, []);

  const recentWorkflows = useMemo(
    () =>
      [...workflows]
        .sort(
          (a, b) =>
            new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
        )
        .slice(0, 5),
    [workflows],
  );

  return (
    <div className="flex items-center gap-4 lg:gap-6">
      <Link
        to="/"
        className="flex items-center gap-2 whitespace-nowrap font-semibold"
      >
        <img src="/favicon.ico" alt="Orcheo Logo" className="h-6 w-6" />
        <span>Orcheo</span>
      </Link>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" className="flex items-center gap-1">
            <Folder className="mr-1 h-4 w-4" />
            <span className="hidden sm:inline">My Projects</span>
            <span className="sm:hidden">Projects</span>
            <ChevronDown className="ml-1 h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          <DropdownMenuLabel>Recent Colleagues</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {recentWorkflows.length > 0 ? (
            recentWorkflows.map((workflow) => (
              <DropdownMenuItem key={workflow.id}>
                <Link
                  to={`/workflow-canvas/${getWorkflowRouteRef(workflow)}`}
                  className="flex w-full items-center"
                >
                  {workflow.name}
                </Link>
              </DropdownMenuItem>
            ))
          ) : (
            <DropdownMenuItem disabled>No colleagues yet</DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem>
            <Link to="/" className="flex w-full items-center">
              View all colleagues
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem>
            <Link to="/workflow-canvas" className="flex w-full items-center">
              <Plus className="mr-2 h-4 w-4" />
              Create New Project
            </Link>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
