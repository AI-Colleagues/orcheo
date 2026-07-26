# Workflow-backed Hosted App

This example publishes a static browser app with two logical workflow bindings:
`greet` and `farewell`.

Upload `workflow.py` and `farewell_workflow.py` as workflows named
`hosted-app-greeting` and `hosted-app-farewell`, each at version 1. Then upload
the sibling `workflow-app.zip` bundle through Studio and publish the validated
deployment.

The browser sends only the logical binding name and an idempotency key. It polls
the opaque run handle returned by the Hosted Apps runtime and never receives a
workflow, workspace, deployment, or release identifier.
