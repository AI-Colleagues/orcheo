import { useMemo, useRef } from "react";
import {
  ChatKit,
  useChatKit,
  type UseChatKitOptions,
  type UseChatKitReturn,
} from "@openai/chatkit-react";
import { cn } from "@/lib/utils";
import { toast } from "@/hooks/use-toast";

interface ChatKitSurfaceProps {
  options: UseChatKitOptions;
  className?: string;
}

export function ChatKitSurface({ options, className }: ChatKitSurfaceProps) {
  const sendActionRef = useRef<UseChatKitReturn["sendCustomAction"] | null>(
    null,
  );
  const optionsWithWidgetActions = useMemo<UseChatKitOptions>(() => {
    const originalOnAction = options.widgets?.onAction;
    return {
      ...options,
      widgets: {
        ...options.widgets,
        onAction: async (action, widgetItem) => {
          try {
            if (originalOnAction) {
              await originalOnAction(action, widgetItem);
            }
            const sendAction = sendActionRef.current;
            if (!sendAction) {
              throw new Error(
                "Chat session is not ready to send widget actions",
              );
            }
            await sendAction(action, widgetItem.id);
          } catch (error) {
            console.error("Failed to dispatch widget action", error);
            toast({
              title: "Widget action failed",
              description:
                "We could not send that widget action. Please try again.",
              variant: "destructive",
            });
          }
        },
      },
    };
  }, [options]);

  const { control, sendCustomAction } = useChatKit(optionsWithWidgetActions);
  sendActionRef.current = sendCustomAction;

  return (
    <div className={cn("flex h-full w-full flex-col", className)}>
      <ChatKit control={control} className="flex h-full w-full flex-col" />
    </div>
  );
}
