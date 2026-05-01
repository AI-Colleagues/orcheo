# Workflow Config Annotations

Orcheo workflow configs are regular JSON payloads, but the Canvas config sheet can
render richer widgets when you annotate fields with JSON Schema metadata. The
upload path resolves those annotations into two separate pieces of data:

- the raw runnable config stored on the workflow version
- the schema metadata stored for Canvas to render the right widget

## File Layout

A typed workflow config usually lives next to the workflow script:

```text
workflows/python_agent_model_selector/
  workflow.py
  config.json
```

The workflow script uses frontmatter to point at the runnable config:

```python
# /// orcheo
# name = "Simple Agent"
# config = "./config.json"
# entrypoint = "orcheo_workflow"
# ///
```

`config.json` stores the typed defaults and schema annotations. The uploader
splits that file into raw runtime values and schema metadata automatically.

## Single-Select Example

Use a string field with an `enum` and `default` to render a single-select widget:

```json
{
  "configurable": {
    "ai_model": "openai:gpt-4.1-mini"
  }
}
```

```json
{
  "configurable": {
    "ai_model": {
      "type": "string",
      "enum": [
        "openai:gpt-4.1-mini",
        "openai:gpt-5.4-mini"
      ],
      "title": "Model",
      "default": "openai:gpt-4.1-mini"
    }
  }
}
```

In Canvas, that `enum` becomes a single-select field. The same config file is
resolved into:

- `runnable_config.configurable.ai_model = "openai:gpt-4.1-mini"`
- `metadata.configurable_schema.ai_model = { ...schema... }`

When you save from Canvas, only the raw runtime config is stored back to the
workflow version.

## Supported Type Annotations

Canvas already maps the common schema shapes to RJSF widgets:

| Declared schema | Widget |
| --- | --- |
| `enum` | Single-select |
| `type: "array"` with `items.enum` | Multi-select |
| `type: "integer"` or `type: "number"` | Number input |
| `type: "boolean"` | Checkbox |
| `type: "string"` with no `enum` | Text input |

## Workflow Authoring Pattern

When you build a workflow for shared use:

1. Keep runtime values in `config.json`.
2. Put field constraints and defaults in `config.json`.
3. Reference `config.json` from workflow frontmatter.
4. Use `{{config.configurable.<name>}}` in the workflow code.

That pattern lets the same workflow remain runnable from the CLI while giving the
Canvas editor enough information to render the right controls for each field.
