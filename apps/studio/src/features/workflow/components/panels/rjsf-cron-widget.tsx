/* eslint-disable react-refresh/only-export-components */
/**
 * Visual cron-expression builder widget.
 *
 * Renders the classic "Every <freq> on <days> at <hh>:<mm>" row instead of a
 * raw string input, but still stores a standard 5-field cron expression
 * (`minute hour day-of-month month day-of-week`) as the field value so the
 * backend scheduler keeps receiving a plain string.
 *
 * It understands day-of-week ranges (e.g. `1-5`) on input. Expressions that the
 * builder cannot represent losslessly (lists/ranges in minute or hour, specific
 * months, combined day-of-month + day-of-week, named tokens, ...) fall back to a
 * raw text input so editing never silently rewrites them.
 */

import React from "react";
import { ChevronDown } from "lucide-react";
import { WidgetProps } from "@rjsf/utils";
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
import { Input } from "@/design-system/ui/input";

type Frequency = "minute" | "hour" | "day" | "week" | "month";

interface CronParts {
  /** Step interval (in minutes) used by the "minute" frequency, e.g. star-slash-30. */
  interval: number;
  /** Step interval (in hours) used by the "hour" frequency, e.g. star-slash-2. */
  hourInterval: number;
  minute: number;
  hour: number;
  dayOfMonth: number;
  daysOfWeek: number[];
}

const DAY_LABELS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

const FREQUENCY_OPTIONS: Array<{ value: Frequency; label: string }> = [
  { value: "minute", label: "minutes" },
  { value: "hour", label: "hours" },
  { value: "day", label: "day" },
  { value: "week", label: "week" },
  { value: "month", label: "month" },
];

// Common step intervals offered in the "every N minutes/hours" dropdowns. The
// current value is merged in (see `intervalOptions`) so hand-edited steps such
// as `*/7` still render a matching option.
const MINUTE_INTERVAL_PRESETS = [1, 2, 3, 5, 10, 15, 20, 30];
const HOUR_INTERVAL_PRESETS = [1, 2, 3, 4, 6, 8, 12];

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

/** Extract the step N from a `*\/N` field (clamped to `max`), or undefined if it isn't a step. */
const parseStep = (field: string | undefined, max: number): number | undefined => {
  const match = /^\*\/(\d+)$/.exec(field ?? "");
  return match ? clampInt(match[1], max, 1) || 1 : undefined;
};

/** Parse the day-of-week field, supporting comma lists and ranges (e.g. "1,6", "1-5"). */
const parseDaysOfWeek = (field: string | undefined): number[] => {
  if (!field || field === "*") return [];
  const days = field
    .split(",")
    .flatMap((token) => {
      const m = /^(\d)-(\d)$/.exec(token);
      if (!m) return [clampInt(token, 7, -1)];
      const from = clampInt(m[1], 7, -1);
      const to = clampInt(m[2], 7, -1);
      return from >= 0 && to >= from ? range(to - from + 1, from) : [-1];
    })
    .map((d) => (d === 7 ? 0 : d)) // normalise Sunday (cron allows 0 or 7)
    .filter((d) => d >= 0 && d <= 6);
  return Array.from(new Set(days));
};

const parseCron = (value: string | undefined): { freq: Frequency; parts: CronParts } => {
  const [minute, hour, dom, , dow] = (value ?? "").trim().split(/\s+/);
  const minuteStep = parseStep(minute, 59);
  const hourStep = parseStep(hour, 23);
  const parts: CronParts = {
    interval: minuteStep ?? 1,
    hourInterval: hourStep ?? 1,
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
  // `M */2 * * *` (step) or `M * * * *` (every hour) → hour interval.
  else if (hourStep !== undefined || hour === "*" || hour === undefined) freq = "hour";

  return { freq, parts };
};

const buildCron = (freq: Frequency, parts: CronParts): string => {
  const { interval, hourInterval, minute, hour, dayOfMonth, daysOfWeek } = parts;
  switch (freq) {
    case "minute":
      // `*/1` is just "every minute"; emit the canonical wildcard form.
      return interval === 1 ? "* * * * *" : `*/${interval} * * * *`;
    case "hour":
      // `*/1` is just "every hour"; emit the canonical wildcard form.
      return hourInterval === 1
        ? `${minute} * * * *`
        : `${minute} */${hourInterval} * * *`;
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

/** Collapse `*\/1` to `*` so an explicit unit step compares equal to the wildcard. */
const collapseUnitStep = (field: string) => (field === "*/1" ? "*" : field);

/**
 * Normalise a cron string to the canonical 5-field form the builder emits:
 * drop redundant `*\/1` steps and expand day-of-week ranges to sorted comma
 * lists. Returns `null` for anything malformed or unrepresentable (wrong field
 * count, named tokens), so callers can fall back to raw editing.
 */
const canonicalCron = (value: string | undefined): string | null => {
  const raw = (value ?? "").trim();
  if (!raw) return "";
  const fields = raw.split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dom, month, dow] = fields.map(collapseUnitStep);
  // Named tokens (MON, JAN) aren't supported by the visual builder.
  if (/[a-z]/i.test(fields.join(""))) return null;
  const days = parseDaysOfWeek(dow);
  const dowField =
    dow === "*" ? "*" : days.length ? [...days].sort((a, b) => a - b).join(",") : dow;
  return [minute, hour, dom, month, dowField].join(" ");
};

/** True when `value` can be represented losslessly by the visual builder. */
const roundTrips = (value: string | undefined): boolean => {
  const canonical = canonicalCron(value);
  if (canonical === null) return false;
  if (canonical === "") return true;
  const { freq, parts } = parseCron(canonical);
  return buildCron(freq, parts) === canonical;
};

/** Interval presets plus the current value, sorted and de-duplicated. */
const intervalOptions = (current: number, presets: number[]): number[] =>
  Array.from(new Set([...presets, current])).sort((a, b) => a - b);

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

  const representable = React.useMemo(() => roundTrips(value), [value]);
  const { freq, parts } = React.useMemo(
    () => parseCron(canonicalCron(value) ?? value),
    [value],
  );

  const update = (nextFreq: Frequency, nextParts: CronParts) => {
    onChange(buildCron(nextFreq, nextParts));
  };

  // Expressions the builder can't represent losslessly are edited as a raw cron
  // string so tweaking one control never silently discards parts it can't model.
  if (!representable) {
    return (
      <div className="flex flex-col gap-1">
        <Input
          id={id}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          disabled={isDisabled}
          spellCheck={false}
          className="font-mono"
        />
        <span className="text-xs text-muted-foreground">
          This schedule is too advanced for the visual builder — editing the raw
          cron expression.
        </span>
      </div>
    );
  }

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
    // Keep at least one day selected in "week" mode. An empty list serializes
    // to a "day" cron, which would round-trip as a different frequency and
    // bounce the user out to the raw-expression editor.
    if (daysOfWeek.length === 0) {
      return;
    }
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

      {(freq === "minute" || freq === "hour") && (
        <Select
          value={String(freq === "minute" ? parts.interval : parts.hourInterval)}
          onValueChange={(v) =>
            freq === "minute"
              ? update("minute", { ...parts, interval: Number(v) })
              : update("hour", { ...parts, hourInterval: Number(v) })
          }
          disabled={isDisabled}
        >
          <SelectTrigger className="w-auto min-w-[4rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-60">
            {(freq === "minute"
              ? intervalOptions(parts.interval, MINUTE_INTERVAL_PRESETS)
              : intervalOptions(parts.hourInterval, HOUR_INTERVAL_PRESETS)
            ).map((n) => (
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

type CronWidgetMap = { cron: typeof CronWidget };

export const cronWidgets: CronWidgetMap = { cron: CronWidget };

export { CronWidget, parseCron, buildCron, canonicalCron, roundTrips };
