export const SCHEMA_FIELD_DRAG_TYPE = "application/x.orcheo.schema-field";

export type SchemaFieldDragPayload = {
  path: string;
  name?: string;
  type?: string;
  description?: string;
};

export function writeSchemaFieldDragData(
  dataTransfer: DataTransfer,
  field: SchemaFieldDragPayload,
): void {
  dataTransfer.effectAllowed = "copy";
  dataTransfer.setData(SCHEMA_FIELD_DRAG_TYPE, JSON.stringify(field));
  dataTransfer.setData("text/plain", field.path);
}

export function hasSchemaFieldData(dataTransfer: DataTransfer | null): boolean {
  if (!dataTransfer) {
    return false;
  }

  const types = Array.from(dataTransfer.types ?? []);
  if (types.includes(SCHEMA_FIELD_DRAG_TYPE)) {
    return true;
  }
  if (types.includes("text/plain")) {
    const preview = dataTransfer.getData("text/plain");
    return preview.trim().length > 0;
  }
  return false;
}

export function readSchemaFieldDragData(
  dataTransfer: DataTransfer | null,
): SchemaFieldDragPayload | null {
  if (!dataTransfer) {
    return null;
  }

  const rawPayload = dataTransfer.getData(SCHEMA_FIELD_DRAG_TYPE);
  if (rawPayload) {
    try {
      const parsed = JSON.parse(rawPayload) as SchemaFieldDragPayload;
      if (parsed && typeof parsed.path === "string" && parsed.path.length > 0) {
        return parsed;
      }
    } catch (error) {
      console.warn("Failed to parse schema field payload", error);
    }
  }

  const fallbackPath = dataTransfer.getData("text/plain");
  if (fallbackPath && fallbackPath.trim().length > 0) {
    return { path: fallbackPath.trim() };
  }

  return null;
}

export function insertSchemaFieldReference(
  target: HTMLInputElement | HTMLTextAreaElement,
  path: string,
): { value: string; selectionStart: number } {
  const moustache = `{{ ${path} }}`;
  const currentValue = target.value ?? "";
  const selectionStart = target.selectionStart ?? currentValue.length;
  const selectionEnd = target.selectionEnd ?? selectionStart;

  const value =
    currentValue.slice(0, selectionStart) +
    moustache +
    currentValue.slice(selectionEnd);

  const caretPosition = selectionStart + moustache.length;

  return { value, selectionStart: caretPosition };
}
