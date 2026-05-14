import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";

export function ProfileApiKeysTab() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>API Keys</CardTitle>
        <CardDescription>
          Manage your API keys for programmatic access to Orcheo.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <div className="font-medium">Production Key</div>
            <div className="text-sm text-muted-foreground">
              Created on Jan 12, 2023 • Last used 2 days ago
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm">
              View
            </Button>
            <Button variant="destructive" size="sm">
              Revoke
            </Button>
          </div>
        </div>
        <div className="flex items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <div className="font-medium">Development Key</div>
            <div className="text-sm text-muted-foreground">
              Created on Mar 5, 2023 • Last used 5 hours ago
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm">
              View
            </Button>
            <Button variant="destructive" size="sm">
              Revoke
            </Button>
          </div>
        </div>
      </CardContent>
      <CardFooter>
        <Button>Generate New API Key</Button>
      </CardFooter>
    </Card>
  );
}
