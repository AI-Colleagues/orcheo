import type { TraceRecord } from "@evilmartians/agent-prism-types";

import cn from "classnames";
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";

import type { BadgeProps } from "../Badge";

import { Badge } from "../Badge";
import { Button } from "../Button";
import { IconButton } from "../IconButton";
import { TraceListItem } from "./TraceListItem";

const INITIAL_VISIBLE_TRACE_COUNT = 20;

type TraceRecordWithBadges = TraceRecord & {
  badges?: Array<BadgeProps>;
};

type TraceListProps = {
  traces: TraceRecordWithBadges[];
  expanded: boolean;
  onExpandStateChange: (expanded: boolean) => void;
  className?: string;
  onTraceSelect?: (trace: TraceRecord) => void;
  selectedTrace?: TraceRecord;
};

export const TraceList = ({
  traces,
  expanded,
  onExpandStateChange,
  className,
  onTraceSelect,
  selectedTrace,
}: TraceListProps) => {
  const [visibleTraceCount, setVisibleTraceCount] = useState(
    INITIAL_VISIBLE_TRACE_COUNT,
  );
  const traceIdsSignature = traces.map((trace) => trace.id).join("|");

  useEffect(() => {
    setVisibleTraceCount(Math.min(INITIAL_VISIBLE_TRACE_COUNT, traces.length));
  }, [traceIdsSignature, traces.length]);

  const visibleTraces = traces.slice(0, visibleTraceCount);
  const hasMoreTraces = traces.length > visibleTraceCount;

  return (
    <div
      className={cn(
        "flex min-w-0 flex-col",
        expanded ? "h-full w-full gap-3" : "h-auto w-fit gap-1",
        className,
      )}
    >
      <header className="flex min-h-6 shrink-0 items-center justify-between gap-2">
        <div
          className={cn(
            "flex items-center gap-2",
            expanded ? "opacity-100" : "hidden opacity-0",
          )}
        >
          <h2 className="text-agentprism-muted-foreground">Traces</h2>

          <Badge
            size="5"
            aria-label={`Total number of traces: ${traces.length}`}
            label={traces.length}
          />
        </div>

        <IconButton
          aria-label={expanded ? "Collapse Trace List" : "Expand Trace List"}
          onClick={() => onExpandStateChange(!expanded)}
        >
          <ArrowLeft className={cn("size-3", expanded ? "" : "rotate-180")} />
        </IconButton>
      </header>

      {expanded && (
        <div className="border-agentprism-border flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border">
          <ul className="flex-1 overflow-y-auto">
            {visibleTraces.map((trace) => (
              <li
                className="border-agentprism-border w-full list-none border-b [&:not(:last-child)]:border-b"
                key={trace.id}
              >
                <TraceListItem
                  showDescription={false}
                  trace={trace}
                  onClick={() => onTraceSelect?.(trace)}
                  isSelected={selectedTrace?.id === trace.id}
                  badges={trace.badges}
                />
              </li>
            ))}
          </ul>

          {hasMoreTraces && (
            <div className="border-agentprism-border shrink-0 border-t p-2">
              <Button
                fullWidth
                size="8"
                variant="ghost"
                onClick={() => {
                  setVisibleTraceCount((current) =>
                    Math.min(
                      current + INITIAL_VISIBLE_TRACE_COUNT,
                      traces.length,
                    ),
                  );
                }}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
