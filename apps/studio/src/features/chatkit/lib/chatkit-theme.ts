import type { UseChatKitOptions } from "@openai/chatkit-react";
import type { ColorScheme } from "@/hooks/use-color-scheme";

export const buildChatTheme = (
  scheme: ColorScheme,
): NonNullable<UseChatKitOptions["theme"]> => ({
  colorScheme: scheme,
  color: {
    // Warm ink grayscale + Orcheo orange accent (orcheo-design-system tokens).
    grayscale: {
      hue: 42,
      tint: 6,
      shade: scheme === "dark" ? -1 : -4,
    },
    accent: {
      primary: "#f87825",
      level: 1,
    },
  },
  radius: "round",
});
