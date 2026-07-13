import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import ThemeSettings from "@features/account/components/theme-settings";

const AppearanceSettingsTab = () => (
  <Card className="border-border/70 bg-muted/40 shadow-none">
    <CardHeader>
      <CardTitle>Theme</CardTitle>
      <CardDescription>
        Choose how the application should look in light, dark, or system mode.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <ThemeSettings />
    </CardContent>
  </Card>
);

export default AppearanceSettingsTab;
