import { Button } from "@/design-system/ui/button";
import ChatInterface from "@features/shared/components/chat-interface";
import type { ChatInterfaceProps } from "@features/shared/components/chat-interface.types";
import { MessageSquare } from "lucide-react";

type SupportChatCalloutProps = Pick<
  ChatInterfaceProps,
  "ai" | "user" | "initialMessages"
>;

export function SupportChatCallout({
  ai,
  user,
  initialMessages,
}: SupportChatCalloutProps) {
  return (
    <section className="rounded-lg border bg-card text-card-foreground shadow-sm">
      <div className="flex flex-col items-center gap-4 p-6 md:flex-row">
        <div className="flex-1">
          <h3 className="text-2xl font-bold">Need immediate help?</h3>
          <p className="text-muted-foreground">
            Chat with our AI assistant for instant answers
          </p>
        </div>
        <ChatInterface
          title="Orcheo Support"
          initialMessages={initialMessages}
          user={user}
          ai={ai}
          triggerButton={
            <Button size="lg" className="w-full md:w-auto">
              <MessageSquare className="mr-2 h-4 w-4" />
              Chat with Support
            </Button>
          }
        />
      </div>
    </section>
  );
}
