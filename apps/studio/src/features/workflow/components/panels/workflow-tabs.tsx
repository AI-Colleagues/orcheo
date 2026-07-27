import { Tabs, TabsList, TabsTrigger } from "@/design-system/ui/tabs";

interface WorkflowTabsProps {
  activeTab: string;
  onTabChange: (value: string) => void;
  currentWorkflow?: {
    name: string;
    onNameChange?: (name: string) => void;
  };
}

export default function WorkflowTabs({
  activeTab,
  onTabChange,
  currentWorkflow,
}: WorkflowTabsProps) {
  return (
    <div className="flex items-center gap-3 border-b border-border px-3">
      {currentWorkflow && (
        <span className="min-w-0 truncate text-sm font-medium text-foreground">
          {currentWorkflow.name}
        </span>
      )}
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-fit">
        <TabsList className="h-9">
          <TabsTrigger value="workflow" className="gap-1.5 text-sm px-3 py-1.5">
            Workflow
          </TabsTrigger>
          <TabsTrigger value="trace" className="gap-1.5 text-sm px-3 py-1.5">
            Trace
          </TabsTrigger>
          <TabsTrigger value="settings" className="gap-1.5 text-sm px-3 py-1.5">
            Settings
          </TabsTrigger>
        </TabsList>
      </Tabs>
    </div>
  );
}
