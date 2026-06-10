/* eslint-disable react-refresh/only-export-components */
/**
 * Text-based widgets (input + textarea) with schema drag-and-drop support.
 */

import React from "react";
import { RegistryWidgetsType, WidgetProps, getUiOptions } from "@rjsf/utils";
import { Input } from "@/design-system/ui/input";
import { Textarea } from "@/design-system/ui/textarea";
import {
  hasSchemaFieldData,
  insertSchemaFieldReference,
  readSchemaFieldDragData,
} from "./schema-dnd";

const useSchemaFieldDnd = <
  Element extends HTMLInputElement | HTMLTextAreaElement,
>(
  disabled: boolean | undefined,
  readonly: boolean | undefined,
  onChange: (value: string) => void,
) => {
  const handleDragOver = React.useCallback(
    (event: React.DragEvent<Element>) => {
      if (disabled || readonly) {
        return;
      }
      if (hasSchemaFieldData(event.dataTransfer)) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }
    },
    [disabled, readonly],
  );

  const handleDrop = React.useCallback(
    (event: React.DragEvent<Element>) => {
      if (disabled || readonly) {
        return;
      }

      const payload = readSchemaFieldDragData(event.dataTransfer);
      if (!payload?.path) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      const target = event.target as Element;
      const { value: nextValue, selectionStart } = insertSchemaFieldReference(
        target,
        payload.path,
      );

      target.value = nextValue;
      target.focus();
      const restoreSelection = () =>
        target.setSelectionRange(selectionStart, selectionStart);
      if (typeof window !== "undefined" && window.requestAnimationFrame) {
        window.requestAnimationFrame(restoreSelection);
      } else {
        restoreSelection();
      }

      onChange(nextValue);
    },
    [disabled, onChange, readonly],
  );

  return { handleDragOver, handleDrop };
};

/**
 * Custom Text Input Widget
 */
function TextWidget(props: WidgetProps) {
  const { id, value, onChange, required, disabled, readonly, placeholder } =
    props;
  const { handleDragOver, handleDrop } = useSchemaFieldDnd<HTMLInputElement>(
    disabled,
    readonly,
    onChange,
  );

  return (
    <Input
      id={id}
      type="text"
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      required={required}
      disabled={disabled}
      readOnly={readonly}
      placeholder={placeholder}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    />
  );
}

/**
 * Custom Textarea Widget
 */
function TextareaWidget(props: WidgetProps) {
  const { id, value, onChange, required, disabled, readonly, placeholder } =
    props;
  const uiOptions = getUiOptions(props.uiSchema || {});
  const rows = (uiOptions.rows as number) || 3;
  const { handleDragOver, handleDrop } = useSchemaFieldDnd<HTMLTextAreaElement>(
    disabled,
    readonly,
    onChange,
  );

  return (
    <Textarea
      id={id}
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
      required={required}
      disabled={disabled}
      readOnly={readonly}
      placeholder={placeholder}
      rows={rows}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    />
  );
}

type TextWidgetMap = Pick<RegistryWidgetsType, "TextWidget" | "TextareaWidget">;

export const textWidgets: TextWidgetMap = {
  TextWidget,
  TextareaWidget,
};

export { TextWidget, TextareaWidget };
