import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FieldProps } from "@rjsf/utils";

import { JsonObjectField } from "./json-object-field";

afterEach(() => {
  cleanup();
});

const createProps = (
  overrides: Partial<FieldProps<Record<string, unknown>>> = {},
): FieldProps<Record<string, unknown>> =>
  ({
    schema: {
      type: "object",
      title: "Fields",
      description: "Editable JSON object",
    },
    uiSchema: {},
    idSchema: { $id: "root_configurable_fields" },
    formData: {
      title: { type: "string" },
      body: { type: "string" },
    },
    errorSchema: {},
    onChange: vi.fn(),
    onBlur: vi.fn(),
    onFocus: vi.fn(),
    formContext: {},
    autofocus: false,
    disabled: false,
    hideError: false,
    readonly: false,
    required: false,
    name: "fields",
    registry: {} as FieldProps<Record<string, unknown>>["registry"],
    rawErrors: [],
    ...overrides,
  }) as FieldProps<Record<string, unknown>>;

describe("JsonObjectField", () => {
  it("renders the object as formatted JSON and parses edits back into an object", async () => {
    const onChange = vi.fn();

    render(<JsonObjectField {...createProps({ onChange })} />);

    const editor = screen.getByRole("textbox", { name: /fields/i });
    expect(editor).toHaveValue(
      '{\n  "title": {\n    "type": "string"\n  },\n  "body": {\n    "type": "string"\n  }\n}',
    );

    fireEvent.change(editor, {
      target: {
        value: '{"text":{"type":"string"}}',
      },
    });

    expect(onChange).toHaveBeenLastCalledWith({
      text: { type: "string" },
    });
  });

  it("shows the object description in a tooltip on hover", async () => {
    const user = userEvent.setup();

    render(<JsonObjectField {...createProps()} />);

    const helpButton = screen.getByRole("button", {
      name: "Fields help",
    });
    await user.hover(helpButton);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Editable JSON object",
    );
  });

  it("shows an error for invalid JSON", async () => {
    render(<JsonObjectField {...createProps()} />);

    const editor = screen.getByRole("textbox", { name: /fields/i });
    fireEvent.change(editor, {
      target: {
        value: "{not-json",
      },
    });

    expect(screen.getByText("Value must be valid JSON.")).toBeInTheDocument();
  });
});
