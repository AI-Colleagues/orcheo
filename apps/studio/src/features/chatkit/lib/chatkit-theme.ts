import type { UseChatKitOptions } from "@openai/chatkit-react";
import type { ColorScheme } from "@/hooks/use-color-scheme";

export const buildChatTheme = (
  scheme: ColorScheme,
): NonNullable<UseChatKitOptions["theme"]> => ({
  colorScheme: scheme,
  color: {
    grayscale: {
      hue: 220,
      tint: 6,
      shade: scheme === "dark" ? -1 : -4,
    },
    accent: {
      primary: scheme === "dark" ? "#f1f5f9" : "#0f172a",
      level: 1,
    },
  },
  radius: "round",
});
