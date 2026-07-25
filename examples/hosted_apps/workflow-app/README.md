# Workflow-backed Hosted App example

This dependency-free example demonstrates the Hosted Apps browser contract:

```text
browser -> logical `greet` binding -> Orcheo workflow -> projected response
```

The browser never receives a workspace ID, workflow ID, service token, or runnable
configuration. It submits JSON to a same-origin logical binding and polls the opaque
run handle returned by the Hosted Apps runtime.

Local and single-node deployments execute accepted Hosted App runs in the backend
and settle the opaque handle for the browser. Distributed production deployments
still require the durable worker/outbox dispatch path.

## 1. Upload the workflow

From the repository root:

```bash
orcheo workflow upload examples/hosted_apps/workflow-app/workflow.py
```

Keep the workflow ID and version ID from the response. You can retrieve them again
with:

```bash
orcheo workflow show hosted-app-greeting
```

The workflow uses only Orcheo imports and a `CodeNode`, so it can be ingested in
both unrestricted and restricted definition modes.

## 2. Build the static bundle

Create the ZIP with `index.html` at its root:

```bash
cd examples/hosted_apps/workflow-app
zip -r ../workflow-app.zip index.html styles.css app.js
```

Do not include `workflow.py`, the binding template, or this README in the browser
bundle.

## 3. Create and bind the app

In Orcheo Studio:

1. Open **Apps**, create a public app, and choose an available alias.
2. Upload `examples/hosted_apps/workflow-app.zip`.
3. Add a workflow binding with the logical name `greet`.
4. Select the uploaded **Hosted App Greeting** workflow and its current version.
5. Copy the policy from
   [`binding.example.json`](./binding.example.json), replacing the two ID
   placeholders if you use the API instead of Studio.
6. Publish the validated deployment and acknowledge the current permission revision.

The important binding settings are:

- **Access:** `anonymous`
- **Input schema:** one required string field named `name`, up to 80 characters
- **Output projection:** `final_state`
- **Visitor output:** enabled
- **Sanitized errors:** enabled

`final_state` is the worker's public output envelope. The app reads only
`final_state.structured_response.greeting` from that projected value.

## 4. Verify the published app

Open the URL returned by the publish action, enter a name, and select **Run
workflow**. The UI should progress through the accepted state and finish with
`Hello, <name>!`.

The client creates a fresh `Idempotency-Key` for every form submission, calls only
`/__orcheo/workflows/greet/runs`, polls `/__orcheo/runs/{handle}`, times out after
60 seconds, and presents sanitized failures without exposing internal run details.

## Local checks

Run the focused regression tests:

```bash
uv run pytest tests/hosted_apps/test_example_workflow_app.py -q
```
