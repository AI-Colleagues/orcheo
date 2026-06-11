import { type ReactNode, useState } from "react";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { Button } from "@/design-system/ui/button";

interface TeamSectionProps {
  name: string;
  count: number;
  defaultOpen?: boolean;
  onRemove?: () => void;
  children: ReactNode;
}

/**
 * A vertically-stacked, individually collapsible section grouping the AI
 * colleagues that belong to a single team.
 */
export const TeamSection = ({
  name,
  count,
  defaultOpen = true,
  onRemove,
  children,
}: TeamSectionProps) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className="border-b border-border/60 pb-4 last:border-b-0">
      <div className="group flex items-center">
        <button
          type="button"
          aria-expanded={isOpen}
          onClick={() => setIsOpen((open) => !open)}
          className="flex flex-1 items-center gap-2 py-3 text-left"
        >
          {isOpen ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="text-sm font-semibold">{name}</span>
          <span className="text-xs text-muted-foreground">{count}</span>
        </button>
        {onRemove ? (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 opacity-0 group-hover:opacity-100"
            onClick={onRemove}
            aria-label={`Remove team ${name}`}
          >
            <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
          </Button>
        ) : null}
      </div>
      {isOpen ? children : null}
    </section>
  );
};
