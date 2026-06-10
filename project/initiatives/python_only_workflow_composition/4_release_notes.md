# Release Notes

## Python-Only Workflow Composition

- **Author:** ShaojieJiang
- **Date:** 2026-03-07
- **Status:** Draft

## Summary

Orcheo now supports Python LangGraph script ingestion as the only workflow composition path for creating executable workflow versions.

## Breaking Changes

1. `POST /api/workflows/{workflow_ref}/versions` was removed.
2. Runtime graph build/execution now supports only graph payloads with `format=langgraph-script`.
3. CLI workflow upload supports `.py` files only.
4. CLI workflow download supports Python output only.
5. `orcheo_sdk.mcp_server` and the `orcheo-mcp` entrypoint were removed from active runtime/tooling.

## New/Updated APIs

1. `POST /api/workflows/{workflow_ref}/versions/ingest` remains the only version creation path.
2. `PUT /api/workflows/{workflow_ref}/versions/{version_number}/runnable-config` updates version runnable config without creating a version.

## Migration Guidance

1. For any legacy JSON-composed workflow versions, re-ingest from Python LangGraph source:
   `orcheo workflow upload path/to/workflow.py --id <workflow_ref>`
2. For config-only updates on existing versions, use:
   `orcheo workflow save-config <workflow_ref> --config '{"tags":["prod"]}'`
3. For Studio config saves, ensure the workflow already has at least one Python-ingested version. Studio will not create versions from graph JSON.

## Legacy Archive

Removed MCP SDK server implementation/tests/docs were moved under `legacy/` for reference-only access.
