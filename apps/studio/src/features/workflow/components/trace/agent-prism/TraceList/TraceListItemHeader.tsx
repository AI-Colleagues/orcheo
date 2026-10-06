import type { TraceRecord } from "@evilmartians/agent-prism-types";

import { LoaderCircle } from "lucide-react";

import type { AvatarProps } from "../Avatar";
import type { TraceLoadStatus } from "../TraceViewer/TraceViewer";

import { Avatar } from "../Avatar";
import { Badge } from "../Badge";

interface TraceListItemHeaderProps {
  trace: TraceRecord & { loadStatus?: TraceLoadStatus };
  avatar?: AvatarProps;
}

const SpansBadge = ({
  trace,
}: {
  trace: TraceRecord & { loadStatus?: TraceLoadStatus };
}) => {
  if (trace.spansCount === 0 && trace.loadStatus === "loading") {
    return (
      <Badge
        size="4"
        label="Loading"
        iconStart={<LoaderCircle className="size-3 animate-spin" />}
      />
    );
  }
  if (trace.spansCount === 0 && trace.loadStatus === "idle") {
    // Not fetched yet: the span count is unknown, not zero.
    return null;
  }
  if (trace.spansCount === 0 && trace.loadStatus === "error") {
    return <Badge size="4" label="Failed to load" />;
  }
  return (
    <Badge
      size="4"
      label={trace.spansCount === 1 ? "1 span" : `${trace.spansCount} spans`}
    />
  );
};

export const TraceListItemHeader = ({
  trace,
  avatar,
}: TraceListItemHeaderProps) => {
  return (
    <header className="flex w-full min-w-0 flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-1.5 overflow-hidden">
        {avatar && <Avatar size="4" {...avatar} />}

        <h3 className="text-agentprism-muted-foreground max-w-full truncate text-sm">
          {trace.name}
        </h3>
      </div>

      <div className="flex items-center gap-2">
        <SpansBadge trace={trace} />
      </div>
    </header>
  );
};
