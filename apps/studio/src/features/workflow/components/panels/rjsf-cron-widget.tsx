/* eslint-disable react-refresh/only-export-components */
/**
 * Visual cron-expression builder widget.
 *
 * Renders the classic "Every <freq> on <days> at <hh>:<mm>" row instead of a
 * raw string input, but still stores a standard 5-field cron expression
 * (`minute hour day-of-month month day-of-week`) as the field value so the
 * backend scheduler keeps receiving a plain string.
 */

import React from "react";
import { ChevronDown } from "lucide-react";
import { RegistryWidgetsType, WidgetProps } from "@rjsf/utils";
import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/design-system/ui/dropdown-menu";

type Frequency = "minute" | "hour" | "day" | "week" | "month";

interface CronParts {
  /** Step interval (in minutes) used by the "minute" frequency, e.g. star-slash-30. */
  interval: number;
  minute: number;
  hour: number;
  dayOfMonth: number;
  daysOfWeek: number[];
}

const DAY_LABELS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

const FREQUENCY_OPTIONS: Array<{ value: Frequency; label: string }> = [
  { value: "minute", label: "minutes" },
  { value: "hour", label: "hour" },
  { value: "day", label: "day" },
  { value: "week", label: "week" },
  { value: "month", label: "month" },
];

// Common minute intervals offered in the "every N minutes" dropdown. The
// current value is merged in (see `intervalOptions`) so hand-edited steps such
// as `*/7` still render a matching option.
const INTERVAL_PRESETS = [1, 2, 3, 5, 10, 15, 20, 30];

const range = (count: number, start = 0) =>
  Array.from({ length: count }, (_, i) => i + start);

const pad = (n: number) => String(n).padStart(2, "0");

const clampInt = (value: string, max: number, fallback: number) => {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n >= 0 && n <= max ? n : fallback;
};

/** Parse a single cron field's first numeric token, falling back to `fallback`. */
const parseField = (field: string | undefined, max: number, fallback: number) =>
  field && field !== "*" ? clampInt(field.split(/[,/-]/)[0], max, fallback) : fallback;

/** Extract the step N from a `*\/N` field, or undefined if it isn't a step. */
const parseStep = (field: string | undefined): number | undefined => {
  const match = /^\*\/(\d+)$/.exec(field ?? "");
  return match ? clampInt(match[1], 59, 1) || 1 : undefined;
};

/** Parse the day-of-week field, supporting comma lists (e.g. "1,6"). */
const parseDaysOfWeek = (field: string | undefined): number[] => {
  if (!field || field === "*") return [];
  return field
    .split(",")
    .map((token) => clampInt(token, 7, -1))
    .map((d) => (d === 7 ? 0 : d)) // normalise Sunday (cron allows 0 or 7)
    .filter((d) => d >= 0 && d <= 6);
};

const parseCron = (value: string | undefined): { freq: Frequency; parts: CronParts } => {
  const [minute, hour, dom, , dow] = (value ?? "").trim().split(/\s+/);
  const minuteStep = parseStep(minute);
  const parts: CronParts = {
    interval: minuteStep ?? 1,
    minute: parseField(minute, 59, 0),
    hour: parseField(hour, 23, 0),
    dayOfMonth: dom && dom !== "*" ? clampInt(dom, 31, 1) || 1 : 1,
    daysOfWeek: parseDaysOfWeek(dow),
  };

  let freq: Frequency = "day";
  if (dow && dow !== "*") freq = "week";
  else if (dom && dom !== "*") freq = "month";
  // `*/30 * * * *` (step) or `* * * * *` (every minute) → minute interval.
  else if (minuteStep !== undefined || minute === "*") freq = "minute";
  else if (hour === "*" || hour === undefined) freq = "hour";

  return { freq, parts };
};

const buildCron = (freq: Frequency, parts: CronParts): string => {
  const { interval, minute, hour, dayOfMonth, daysOfWeek } = parts;
  switch (freq) {
    case "minute":
      return `*/${interval} * * * *`;
    case "hour":
      return `${minute} * * * *`;
    case "day":
      return `${minute} ${hour} * * *`;
    case "week": {
      const dow = daysOfWeek.length ? [...daysOfWeek].sort((a, b) => a - b).join(",") : "*";
      return `${minute} ${hour} * * ${dow}`;
    }
    case "month":
      return `${minute} ${hour} ${dayOfMonth} * *`;
    default:
      return `${minute} ${hour} * * *`;
  }
};

/** Interval presets plus the current value, sorted and de-duplicated. */
const intervalOptions = (current: number): number[] =>
  Array.from(new Set([...INTERVAL_PRESETS, current])).sort((a, b) => a - b);

interface UnitSelectProps {
  id?: string;
  value: number;
  max: number;
  start?: number;
  format?: (n: number) => string;
  disabled?: boolean;
  onChange: (value: number) => void;
}

function UnitSelect({
  id,
  value,
  max,
  start = 0,
  format = String,
  disabled,
  onChange,
}: UnitSelectProps) {
  return (
    <Select
      value={String(value)}
      onValueChange={(v) => onChange(Number(v))}
      disabled={disabled}
    >
      <SelectTrigger id={id} className="w-auto min-w-[4rem]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="max-h-60">
        {range(max - start + 1, start).map((n) => (
          <SelectItem key={n} value={String(n)}>
            {format(n)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function CronWidget(props: WidgetProps) {
  const { id, value, onChange, disabled, readonly } = props;
  const isDisabled = disabled || readonly;

  const { freq, parts } = React.useMemo(() => parseCron(value), [value]);

  const update = (nextFreq: Frequency, nextParts: CronParts) => {
    onChange(buildCron(nextFreq, nextParts));
  };

  const setFreq = (nextFreq: Frequency) => {
    // Default to a sensible day when switching to "week" with nothing selected.
    const next =
      nextFreq === "week" && parts.daysOfWeek.length === 0
        ? { ...parts, daysOfWeek: [1] }
        : parts;
    update(nextFreq, next);
  };

  const toggleDay = (day: number, checked: boolean) => {
    const daysOfWeek = checked
      ? [...parts.daysOfWeek, day]
      : parts.daysOfWeek.filter((d) => d !== day);
    update("week", { ...parts, daysOfWeek });
  };

  const selectedDaysLabel =
    parts.daysOfWeek.length === 0
      ? "Select days"
      : [...parts.daysOfWeek]
          .sort((a, b) => a - b)
          .map((d) => DAY_LABELS[d])
          .join(",");

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" id={id}>
      <span className="text-muted-foreground">Every</span>

      {freq === "minute" && (
        <Select
          value={String(parts.interval)}
          onValueChange={(v) =>
            update("minute", { ...parts, interval: Number(v) })
          }
          disabled={isDisabled}
        >
          <SelectTrigger className="w-auto min-w-[4rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-60">
            {intervalOptions(parts.interval).map((n) => (
              <SelectItem key={n} value={String(n)}>
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      <Select
        value={freq}
        onValueChange={(v) => setFreq(v as Frequency)}
        disabled={isDisabled}
      >
        <SelectTrigger className="w-auto min-w-[5rem]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {FREQUENCY_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {freq === "week" && (
        <>
          <span className="text-muted-foreground">on</span>
          <DropdownMenu>
            <DropdownMenuTrigger
              disabled={isDisabled}
              className={cn(
                "flex h-9 min-w-[6rem] items-center justify-between gap-2 whitespace-nowrap rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <span className={cn(parts.daysOfWeek.length === 0 && "text-muted-foreground")}>
                {selectedDaysLabel}
              </span>
              <ChevronDown className="h-4 w-4 opacity-50" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {DAY_LABELS.map((label, day) => (
                <DropdownMenuCheckboxItem
                  key={label}
                  checked={parts.daysOfWeek.includes(day)}
                  onCheckedChange={(checked) => toggleDay(day, checked)}
                  onSelect={(e) => e.preventDefault()}
                >
                  {label}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      )}

      {freq === "month" && (
        <>
          <span className="text-muted-foreground">on day</span>
          <UnitSelect
            value={parts.dayOfMonth}
            max={31}
            start={1}
            disabled={isDisabled}
            onChange={(dayOfMonth) => update("month", { ...parts, dayOfMonth })}
          />
        </>
      )}

      {(freq === "day" || freq === "week" || freq === "month") && (
        <>
          <span className="text-muted-foreground">at</span>
          <UnitSelect
            value={parts.hour}
            max={23}
            format={pad}
            disabled={isDisabled}
            onChange={(hour) => update(freq, { ...parts, hour })}
          />
          <span className="text-muted-foreground">:</span>
          <UnitSelect
            value={parts.minute}
            max={59}
            format={pad}
            disabled={isDisabled}
            onChange={(minute) => update(freq, { ...parts, minute })}
          />
        </>
      )}

      {freq === "hour" && (
        <>
          <span className="text-muted-foreground">at minute</span>
          <UnitSelect
            value={parts.minute}
            max={59}
            format={pad}
            disabled={isDisabled}
            onChange={(minute) => update("hour", { ...parts, minute })}
          />
        </>
      )}
    </div>
  );
}

type CronWidgetMap = Pick<RegistryWidgetsType, never> & { cron: typeof CronWidget };

export const cronWidgets: CronWidgetMap = { cron: CronWidget };

export { CronWidget, parseCron, buildCron };
