import React from "react";
import { Check, Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/design-system/ui/button";
import { cn } from "@/lib/utils";

import { useThemePreferences } from "./use-theme-preferences";

interface ThemeSettingsProps {
  onThemeChange?: (theme: "light" | "dark" | "system") => void;
  className?: string;
}

export default function ThemeSettings({
  onThemeChange,
  className,
}: ThemeSettingsProps) {
  const { setTheme, theme } = useThemePreferences({
    onThemeChange,
  });

  return (
    <div className={cn("grid grid-cols-1 gap-2 sm:grid-cols-3", className)}>
      <ThemeCard
        label="Light"
        icon={<Sun className="h-6 w-6" />}
        isActive={theme === "light"}
        onClick={() => setTheme("light")}
      />
      <ThemeCard
        label="Dark"
        icon={<Moon className="h-6 w-6" />}
        isActive={theme === "dark"}
        onClick={() => setTheme("dark")}
      />
      <ThemeCard
        label="System"
        icon={<Monitor className="h-6 w-6" />}
        isActive={theme === "system"}
        onClick={() => setTheme("system")}
      />
    </div>
  );
}

interface ThemeCardProps {
  label: string;
  icon: React.ReactNode;
  isActive: boolean;
  onClick: () => void;
}

const ThemeCard: React.FC<ThemeCardProps> = ({
  icon,
  isActive,
  label,
  onClick,
}) => (
  <Button
    variant={isActive ? "default" : "outline"}
    className="relative flex h-24 flex-col items-center justify-center gap-2"
    onClick={onClick}
  >
    {icon}
    <span>{label}</span>
    {isActive && (
      <Check className="absolute top-2 right-2 h-4 w-4 text-primary-foreground" />
    )}
  </Button>
);
