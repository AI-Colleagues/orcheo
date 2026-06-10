/* eslint-disable react-refresh/only-export-components */
/**
 * Primitive input widgets (number, checkbox, select).
 */

import React from "react";
import { RegistryWidgetsType, WidgetProps } from "@rjsf/utils";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { Switch } from "@/design-system/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/design-system/ui/select";

function NumberWidget(props: WidgetProps) {
  const {
    id,
    value,
    onChange,
    required,
    disabled,
    readonly,
    placeholder,
    schema,
  } = props;

  return (
    <Input
      id={id}
      type="number"
      value={value ?? ""}
      onChange={(e) => {
        const val = e.target.value;
        onChange(val === "" ? undefined : Number(val));
      }}
      required={required}
      disabled={disabled}
      readOnly={readonly}
      placeholder={placeholder}
      min={schema.minimum}
      max={schema.maximum}
      step={schema.multipleOf || (schema.type === "integer" ? 1 : "any")}
    />
  );
}

function CheckboxWidget(props: WidgetProps) {
  const { id, value, onChange, label, disabled, readonly } = props;

  return (
    <div className="flex items-center space-x-2">
      <Switch
        id={id}
        checked={Boolean(value)}
        onCheckedChange={onChange}
        disabled={disabled || readonly}
      />
      <Label htmlFor={id}>{label}</Label>
    </div>
  );
}

function SelectWidget(props: WidgetProps) {
  const { id, value, onChange, options, disabled, readonly, placeholder } =
    props;
  const { enumOptions } = options;

  return (
    <Select
      value={value ? String(value) : undefined}
      onValueChange={onChange}
      disabled={disabled || readonly}
    >
      <SelectTrigger id={id}>
        <SelectValue placeholder={placeholder || "Select an option"} />
      </SelectTrigger>
      <SelectContent>
        {(enumOptions as Array<{ value: string; label: string }>)?.map(
          (option) => (
            <SelectItem key={option.value} value={String(option.value)}>
              {option.label}
            </SelectItem>
          ),
        )}
      </SelectContent>
    </Select>
  );
}

type PrimitiveWidgetMap = Pick<
  RegistryWidgetsType,
  "NumberWidget" | "CheckboxWidget" | "SelectWidget"
>;

export const primitiveWidgets: PrimitiveWidgetMap = {
  NumberWidget,
  CheckboxWidget,
  SelectWidget,
};

export { NumberWidget, CheckboxWidget, SelectWidget };
