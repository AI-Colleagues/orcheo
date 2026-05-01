import { useState } from "react";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Label } from "@/design-system/ui/label";
import { Separator } from "@/design-system/ui/separator";
import { Switch } from "@/design-system/ui/switch";

type VisualizationSettingsState = {
  autoSave: boolean;
  showNodeLabels: boolean;
  confirmBeforeDelete: boolean;
  showMinimap: boolean;
};

const ApplicationSettingsTab = () => {
  const [appSettings, setAppSettings] = useState<VisualizationSettingsState>({
    autoSave: true,
    showNodeLabels: true,
    confirmBeforeDelete: true,
    showMinimap: false,
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Workflow Visualization Settings</CardTitle>
          <CardDescription>
            Configure how workflow visualizations are displayed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between space-x-2">
            <Label htmlFor="autosave" className="flex flex-col space-y-1">
              <span>Auto-save Workflows</span>
              <span className="font-normal text-xs text-muted-foreground">
                Automatically save changes as you work
              </span>
            </Label>
            <Switch
              id="autosave"
              checked={appSettings.autoSave}
              onCheckedChange={(checked) =>
                setAppSettings((prev) => ({ ...prev, autoSave: checked }))
              }
            />
          </div>
          <Separator />

          <div className="flex items-center justify-between space-x-2">
            <Label htmlFor="nodelabels" className="flex flex-col space-y-1">
              <span>Show Node Labels</span>
              <span className="font-normal text-xs text-muted-foreground">
                Display labels on workflow nodes
              </span>
            </Label>
            <Switch
              id="nodelabels"
              checked={appSettings.showNodeLabels}
              onCheckedChange={(checked) =>
                setAppSettings((prev) => ({
                  ...prev,
                  showNodeLabels: checked,
                }))
              }
            />
          </div>
          <Separator />

          <div className="flex items-center justify-between space-x-2">
            <Label htmlFor="confirmdelete" className="flex flex-col space-y-1">
              <span>Confirm Before Delete</span>
              <span className="font-normal text-xs text-muted-foreground">
                Show confirmation dialog before deleting nodes
              </span>
            </Label>
            <Switch
              id="confirmdelete"
              checked={appSettings.confirmBeforeDelete}
              onCheckedChange={(checked) =>
                setAppSettings((prev) => ({
                  ...prev,
                  confirmBeforeDelete: checked,
                }))
              }
            />
          </div>
          <Separator />

          <div className="flex items-center justify-between space-x-2">
            <Label htmlFor="minimap" className="flex flex-col space-y-1">
              <span>Show Minimap</span>
              <span className="font-normal text-xs text-muted-foreground">
                Display minimap navigation in workflow visualization
              </span>
            </Label>
            <Switch
              id="minimap"
              checked={appSettings.showMinimap}
              onCheckedChange={(checked) =>
                setAppSettings((prev) => ({ ...prev, showMinimap: checked }))
              }
            />
          </div>
        </CardContent>
        <CardFooter>
          <Button>Save Visualization Settings</Button>
        </CardFooter>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Data Storage</CardTitle>
          <CardDescription>
            Manage your data storage preferences.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <h3 className="font-medium">Storage Usage</h3>
            <div className="h-4 w-full rounded-full bg-secondary">
              <div
                className="h-4 rounded-full bg-primary"
                style={{ width: "35%" }}
              ></div>
            </div>
            <p className="text-xs text-muted-foreground">
              3.5 GB used of 10 GB (35%)
            </p>
          </div>
          <div className="pt-2">
            <Button variant="outline">Manage Storage</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default ApplicationSettingsTab;
