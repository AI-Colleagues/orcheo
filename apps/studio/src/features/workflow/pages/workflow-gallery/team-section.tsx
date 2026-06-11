import { type ReactNode, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface TeamSectionProps {
  name: string;
  count: number;
  defaultOpen?: boolean;
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
  children,
}: TeamSectionProps) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className="border-b border-border/60 pb-4 last:border-b-0">
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        className="flex w-full items-center gap-2 py-3 text-left"
      >
        {isOpen ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold">{name}</span>
        <span className="text-xs text-muted-foreground">{count}</span>
      </button>
      {isOpen ? children : null}
    </section>
  );
};
