import { useEffect, useMemo, useState } from "react";
import type { FieldProps } from "@rjsf/utils";

import { Label } from "@/design-system/ui/label";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/design-system/ui/tooltip";
import { Textarea } from "@/design-system/ui/textarea";
import { HelpCircle } from "lucide-react";

const formatJson = (value: unknown): string => {
  if (value === undefined || value === null) {
    return "{}";
  }
  try {
    return JSON.stringify(value, null, 2) ?? "{}";
  } catch {
    return "{}";
  }
};

const parseJsonObject = (
  value: string,
): { value: Record<string, unknown> | undefined; error: string | null } => {
  if (!value.trim()) {
    return { value: {}, error: null };
  }

  try {
    const parsed = JSON.parse(value) as unknown;
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return {
        value: undefined,
        error: "Value must be a JSON object.",
      };
    }
    return { value: parsed as Record<string, unknown>, error: null };
  } catch {
    return { value: undefined, error: "Value must be valid JSON." };
  }
};

function JsonObjectField({
  idSchema,
  name,
  schema,
  formData,
  required,
  disabled,
  readonly,
  autofocus,
  onChange,
  onBlur,
  onFocus,
  rawErrors,
}: FieldProps<Record<string, unknown>>) {
  const [text, setText] = useState(() => formatJson(formData));
  const [localError, setLocalError] = useState<string | null>(null);

  const label = schema.title ?? name;
  const description = schema.description;
  const errors = useMemo(
    () => [...(rawErrors ?? []), ...(localError ? [localError] : [])],
    [localError, rawErrors],
  );

  useEffect(() => {
    setText(formatJson(formData));
    setLocalError(null);
  }, [formData]);

  const handleChange = (nextValue: string) => {
    setText(nextValue);
    const parsed = parseJsonObject(nextValue);
    setLocalError(parsed.error);
    if (parsed.value !== undefined) {
      onChange(parsed.value);
    }
  };

  return (
    <div className="grid gap-2 mb-4">
      <div className="flex items-center gap-1.5">
        <Label htmlFor={idSchema.$id}>
          {label}
          {required && <span className="ml-1 text-destructive">*</span>}
        </Label>
        {description && (
          <TooltipProvider delayDuration={300}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={`${label} help`}
                  className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <HelpCircle className="h-3.5 w-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" className="max-w-[300px]">
                <p className="text-xs">{description}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      <Textarea
        id={idSchema.$id}
        value={text}
        onChange={(event) => handleChange(event.target.value)}
        onBlur={() => onBlur(idSchema.$id, formData)}
        onFocus={() => onFocus(idSchema.$id, formData)}
        autoFocus={autofocus}
        disabled={disabled}
        readOnly={readonly}
        spellCheck={false}
        className="min-h-40 font-mono text-xs"
        aria-invalid={errors.length > 0}
      />
      {errors.length > 0 && (
        <div className="space-y-1 text-xs text-destructive">
          {errors.map((error) => (
            <p key={error}>{error}</p>
          ))}
        </div>
      )}
    </div>
  );
}

export { JsonObjectField };
