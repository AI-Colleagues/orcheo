import { Tabs, TabsList, TabsTrigger } from "@/design-system/ui/tabs";

interface WorkflowTabsProps {
  activeTab: string;
  onTabChange: (value: string) => void;
}

export default function WorkflowTabs({
  activeTab,
  onTabChange,
}: WorkflowTabsProps) {
  return (
    <div className="border-b border-border">
      <Tabs value={activeTab} onValueChange={onTabChange} className="w-full">
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
