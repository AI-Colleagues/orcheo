# Studio Feature Modules

Each row corresponds to one source script (test files excluded).

| Module path (relative to `features/`)                                                | Purpose                                                                                                      |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **account**                                                                          |                                                                                                              |
| `account/components/settings/agent-settings-tab.tsx`                                 | Settings tab for managing external agent integrations (Claude Code, Codex, Gemini) and their login sessions. |
| `account/components/settings/appearance-settings-tab.tsx`                            | Settings tab for theme selection (light / dark / system).                                                    |
| `account/components/settings/theme-settings.tsx`                                     | Button group UI for picking light, dark, or system theme.                                                    |
| `account/components/use-theme-preferences.ts`                                        | Hook that reads and persists user theme, accent-colour, reduced-motion, and high-contrast preferences.       |
| `account/pages/profile/profile-general-tab.tsx`                                      | Profile tab displaying user avatar, name, email, role, and join date.                                        |
| `account/pages/profile/types.ts`                                                     | `ProfileUser` interface (name, email, avatar, role, optional joinDate).                                      |
| `account/pages/profile.tsx`                                                          | Profile page that composes the general-info tab and any additional settings tabs.                            |
| `account/pages/settings.tsx`                                                         | Settings page with tabs for agent integrations and appearance preferences.                                   |
| `account/pages/workspace-members.tsx`                                                | Tab content for listing and managing workspace members with role-based access control.                       |
| `account/pages/service-tokens.tsx`                                                   | Tab content for creating, rotating, and revoking workspace-scoped API keys used by the Orcheo SDK.           |
| `account/pages/workspace-management.tsx`                                             | Workspace Management page with tabs for members, external agents, and API keys.                              |
| `account/components/external-agents-section.tsx`                                     | Section for connecting and managing per-workspace external agent CLIs (Claude Code, Codex, Gemini).          |
| **auth**                                                                             |                                                                                                              |
| `auth/components/auto-login.tsx`                                                     | Initiates an OIDC login redirect automatically, forwarding org / invitation / hint query params.             |
| `auth/components/require-auth.tsx`                                                   | Route guard that verifies auth state and refreshes tokens before rendering protected children.               |
| `auth/lib/auth-session.ts`                                                           | Manages JWT tokens, user profile, and session state in `localStorage`; supports SSO.                         |
| `auth/lib/oidc-client.ts`                                                            | OIDC/OAuth2 client handling PKCE flow, token exchange, and session management.                               |
| `auth/pages/login.tsx`                                                               | Login page that parses invite-context params and mounts `AutoLogin`.                                         |
| `auth/pages/oauth-callback.tsx`                                                      | OAuth callback handler that completes OIDC login, stores tokens, and redirects to the intended URL.          |
| **chatkit**                                                                          |                                                                                                              |
| `chatkit/components/studio-chat-bubble.tsx`                                          | Floating chat-bubble component for the workflow canvas, positioned above the React Flow minimap.             |
| `chatkit/components/chatkit-surface.tsx`                                             | Wrapper around the ChatKit library that handles custom action dispatching and error reporting.               |
| `chatkit/components/public-chat-config.ts`                                           | Builds ChatKit start-screen prompt and model-option configuration from workflow settings.                    |
| `chatkit/components/public-chat-error-boundary.tsx`                                  | Error boundary that catches and gracefully displays errors inside the public chat widget.                    |
| `chatkit/components/public-chat-widget.tsx`                                          | Public-facing ChatKit widget that wires up theme, auth, and workflow configuration.                          |
| `chatkit/lib/chatkit-client.ts`                                                      | HTTP client for ChatKit API requests with error handling and domain-key management.                          |
| `chatkit/lib/chatkit-theme.ts`                                                       | Builds a ChatKit UI theme from the current light/dark colour-scheme preference.                              |
| `chatkit/lib/telemetry.ts`                                                           | Dispatches telemetry events for ChatKit interactions (open, close, success, failure).                        |
| `chatkit/lib/workflow-session.ts`                                                    | Manages ChatKit workflow client-secrets with automatic refresh and retry logic.                              |
| `chatkit/pages/public-chat.tsx`                                                      | Public shareable chat page for a workflow, with theme switching and workflow metadata display.               |
| **shared**                                                                           |                                                                                                              |
| `shared/components/chat-interface-options.ts`                                        | Builds ChatKit initialisation options (API endpoint, auth, model config).                                    |
| `shared/components/chat-interface.types.ts`                                          | `ChatInterface` props and `ChatParticipant` types.                                                           |
| `shared/components/top-navigation/account-menu.tsx`                                  | Dropdown menu for account settings, credentials, profile, workspace members, and logout.                     |
| `shared/components/top-navigation/active-workspace-indicator.tsx`                    | Displays the active workspace name and lets the user switch between workspaces.                              |
| `shared/components/top-navigation/studio-brand.tsx`                                   | Brand logo linking to the gallery, with a beta status badge.                                                 |
| `shared/components/top-navigation/top-navigation-types.ts`                           | `TopNavigation` props interface (credentials, credential handlers).                                          |
| `shared/components/top-navigation.tsx`                                               | Top navigation bar: brand, workspace selector, version status, and account menu.                             |
| `shared/components/top-navigation/version-status.tsx`                                | Checks for and displays available canvas version updates, with result caching.                               |
| `shared/components/workspace-bootstrap-gate.tsx`                                     | Ensures the user has at least one workspace, creating a default one if necessary, before rendering children. |
| **vibe**                                                                             |                                                                                                              |
| `vibe/components/vibe-authenticated-layout.tsx`                                      | Main authenticated layout for Vibe: resizable sidebar with workspace bootstrap gate.                         |
| `vibe/components/vibe-sidebar.tsx`                                                   | Sidebar component for the Vibe agent-chat interface, including provider selection and chat surface.          |
| `vibe/constants.ts`                                                                  | Constants for Vibe sidebar dimensions, agent tag, workflow name, and participant metadata.                   |
| `vibe/context/vibe-context.ts`                                                       | React context definition for Vibe state (open status, providers, workflow ID, context string).               |
| `vibe/context/vibe-provider.tsx`                                                     | Context provider managing Vibe state: agent list, workflow provisioning, and page context.                   |
| `vibe/hooks/use-vibe-agents.ts`                                                      | Fetches and caches external agent provider status with polling and transient-error handling.                 |
| `vibe/hooks/use-vibe-chat.ts`                                                        | Manages the Vibe chat session lifecycle including client-secret refresh and retry logic.                     |
| `vibe/hooks/use-vibe-context-string.ts`                                              | Builds the context string that describes the user's current page location for the Vibe AI.                   |
| `vibe/hooks/use-vibe-workflow.ts`                                                    | Manages Vibe agent workflow provisioning, template synchronisation, and workspace storage.                   |
| `vibe/lib/vibe-models.ts`                                                            | Builds ChatKit model options for the Vibe composer based on available external agent providers.              |
| **workflow / dialogs**                                                               |                                                                                                              |
| `workflow/components/dialogs/add-credential-dialog.tsx`                              | Dialog for creating a new credential (name, provider, access level, secret).                                 |
| `workflow/components/dialogs/confirm-delete-workflow-dialog.tsx`                     | Confirmation dialog for permanently deleting a workflow.                                                     |
| `workflow/components/dialogs/credential-access-badge.tsx`                            | Badge showing a credential's access level (scoped vs. shared).                                               |
| `workflow/components/dialogs/credential-status-badge.tsx`                            | Badge indicating a credential's health status (healthy / unhealthy / unknown).                               |
| `workflow/components/dialogs/credentials-table.tsx`                                  | Table listing credentials with status, access level, and edit/delete/reveal-secret actions.                  |
| `workflow/components/dialogs/credentials-vault.tsx`                                  | Full credentials-vault UI: search, add, edit, and delete credentials.                                        |
| `workflow/components/dialogs/edit-credential-dialog.tsx`                             | Dialog for editing an existing credential's name, provider, access level, and secrets.                       |
| `workflow/components/dialogs/update-workflow-dialog.tsx`                             | Dialog for uploading a new version of a workflow's script and configuration files.                           |
| `workflow/components/dialogs/upload-workflow-dialog.tsx`                             | Dialog for uploading a brand-new workflow with script and configuration files.                               |
| **workflow / forms & layouts**                                                       |                                                                                                              |
| `workflow/components/forms/schema-config-form.tsx`                                   | RJSF form wrapper for workflow runtime configuration, using custom widgets and templates.                    |
| `workflow/components/layouts/sidebar-layout.tsx`                                     | Resizable two-panel layout with collapsible sidebar.                                                         |
| `workflow/components/layouts/use-sidebar-resize.ts`                                  | Hook that handles sidebar drag-resize with min/max width constraints.                                        |
| `workflow/components/layouts/workflow-page-layout.tsx`                               | Generic page scaffold with optional header and main-content areas.                                           |
| **workflow / panels (RJSF & history)**                                               |                                                                                                              |
| `workflow/components/panels/rjsf-basic-widgets.tsx`                                  | RJSF widgets for basic input types: text, number, checkbox, and select.                                      |
| `workflow/components/panels/rjsf-input-widgets.tsx`                                  | Primitive RJSF input widgets: number, checkbox, and select.                                                  |
| `workflow/components/panels/rjsf-templates.tsx`                                      | RJSF field and array item templates controlling form layout and styling.                                     |
| `workflow/components/panels/rjsf-text-widgets.tsx`                                   | Text RJSF widgets (input, textarea) with schema drag-and-drop field insertion.                               |
| `workflow/components/panels/rjsf-theme.tsx`                                          | RJSF theme wiring together custom widgets, templates, and validator.                                         |
| `workflow/components/panels/schema-dnd.ts`                                           | Drag-and-drop utilities for inserting `{{field}}` references from schema into text inputs.                   |
| `workflow/components/panels/workflow-diff-dialog.tsx`                                | Dialog that displays a human-readable diff between two workflow versions.                                    |
| `workflow/components/panels/workflow-history-filters.tsx`                            | Filter bar for the workflow history panel (text search, version picker).                                     |
| `workflow/components/panels/workflow-history-footer.tsx`                             | Pagination footer for the workflow version history list.                                                     |
| `workflow/components/panels/workflow-history-header.tsx`                             | Header with "Restore" and "Compare" buttons for the workflow history panel.                                  |
| `workflow/components/panels/workflow-history-table.tsx`                              | Table of workflow versions with status badges, change summaries, and row selection.                          |
| `workflow/components/panels/workflow-history.tsx`                                    | Assembled workflow history panel: filters, table, diff dialog, and restore action.                           |
| `workflow/components/panels/workflow-tabs.tsx`                                       | Tab bar for switching between the workflow editor, trace, and settings views.                                |
| **workflow / trace components (agent-prism)**                                        |                                                                                                              |
| `workflow/components/trace/agent-prism/Avatar.tsx`                                   | Avatar component for trace span categories with configurable sizes.                                          |
| `workflow/components/trace/agent-prism/Badge.tsx`                                    | Badge component for trace span attributes with optional icon.                                                |
| `workflow/components/trace/agent-prism/BrandLogo.tsx`                                | Brand logo used inside the trace viewer.                                                                     |
| `workflow/components/trace/agent-prism/Button.tsx`                                   | Generic button used across trace viewer components.                                                          |
| `workflow/components/trace/agent-prism/CollapseAndExpandControls.tsx`                | Controls for collapsing / expanding all trace tree nodes at once.                                            |
| `workflow/components/trace/agent-prism/CollapsibleSection.tsx`                       | Reusable collapsible section wrapper for trace detail panels.                                                |
| `workflow/components/trace/agent-prism/CopyButton.tsx`                               | Button that copies text content to the clipboard.                                                            |
| `workflow/components/trace/agent-prism/IconButton.tsx`                               | Icon-only button primitive used across the trace viewer.                                                     |
| `workflow/components/trace/agent-prism/PriceBadge.tsx`                               | Badge showing the cost of a trace span.                                                                      |
| `workflow/components/trace/agent-prism/SearchInput.tsx`                              | Search input for filtering trace spans.                                                                      |
| `workflow/components/trace/agent-prism/SpanBadge.tsx`                                | Badge that labels a span type or category.                                                                   |
| `workflow/components/trace/agent-prism/SpanStatus.tsx`                               | Visual indicator for a span's execution status.                                                              |
| `workflow/components/trace/agent-prism/TabSelector.tsx`                              | Tab-selector control for the trace detail view panes.                                                        |
| `workflow/components/trace/agent-prism/Tabs.tsx`                                     | Tabs container for the trace detail view.                                                                    |
| `workflow/components/trace/agent-prism/TextInput.tsx`                                | Text input primitive used inside trace viewer forms.                                                         |
| `workflow/components/trace/agent-prism/TimestampBadge.tsx`                           | Badge displaying a span's start time or duration.                                                            |
| `workflow/components/trace/agent-prism/TokensBadge.tsx`                              | Badge showing LLM token counts for a span.                                                                   |
| `workflow/components/trace/agent-prism/TreeView.tsx`                                 | Recursive tree view component for rendering the span hierarchy.                                              |
| `workflow/components/trace/agent-prism/DetailsView/DetailsView.tsx`                  | Container for the trace span detail panel with tabbed sub-views.                                             |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewAttributesTab.tsx`     | Attributes tab showing key/value metadata for a span.                                                        |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewContentViewer.tsx`     | Generic content viewer for rendering span input/output data.                                                 |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewHeader.tsx`            | Header for the span detail panel showing span name and status.                                               |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewInputOutputTab.tsx`    | Input/output tab rendering a span's request and response data.                                               |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewJsonOutput.tsx`        | JSON renderer used to display span output in the detail panel.                                               |
| `workflow/components/trace/agent-prism/DetailsView/DetailsViewRawDataTab.tsx`        | Raw-data tab showing the full unprocessed span payload.                                                      |
| `workflow/components/trace/agent-prism/SpanCard/SpanCard.tsx`                        | Card component that renders a single trace span inside the tree view.                                        |
| `workflow/components/trace/agent-prism/SpanCard/SpanCardBadges.tsx`                  | Badge row on a span card (status, tokens, cost, duration).                                                   |
| `workflow/components/trace/agent-prism/SpanCard/SpanCardConnector.tsx`               | Vertical connector line linking parent and child span cards in the tree.                                     |
| `workflow/components/trace/agent-prism/SpanCard/SpanCardTimeline.tsx`                | Horizontal timeline bar visualising a span's position and duration.                                          |
| `workflow/components/trace/agent-prism/SpanCard/SpanCardToggle.tsx`                  | Expand/collapse toggle button on a span card.                                                                |
| `workflow/components/trace/agent-prism/TraceList/TraceList.tsx`                      | List of all traces for a workflow execution.                                                                 |
| `workflow/components/trace/agent-prism/TraceList/TraceListItem.tsx`                  | Individual item in the trace list.                                                                           |
| `workflow/components/trace/agent-prism/TraceList/TraceListItemHeader.tsx`            | Header row for a trace list item (name, status, timestamp).                                                  |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewer.tsx`                  | Top-level trace viewer component integrating tree, detail, and search.                                       |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewerDesktopLayout.tsx`     | Desktop two-column layout for the trace viewer.                                                              |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewerMobileLayout.tsx`      | Mobile single-column layout for the trace viewer.                                                            |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewerPlaceholder.tsx`       | Empty-state placeholder shown when no trace is selected.                                                     |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewerSearchAndControls.tsx` | Search bar and collapse/expand controls for the trace viewer.                                                |
| `workflow/components/trace/agent-prism/TraceViewer/TraceViewerTreeViewContainer.tsx` | Scrollable container wrapping the span tree view.                                                            |
| `workflow/components/trace/agent-prism/TraceViewer/useTraceSelection.ts`             | Hook managing which trace and span are currently selected in the viewer.                                     |
| `workflow/components/trace/agent-prism/index.ts`                                     | Public barrel export for the agent-prism trace viewer library.                                               |
| `workflow/components/trace/agent-prism/shared.ts`                                    | Shared utilities and constants used across agent-prism components.                                           |
| `workflow/components/trace/agent-prism/theme/index.ts`                               | Theme tokens and configuration for the agent-prism trace viewer.                                             |
| **workflow / data & templates**                                                      |                                                                                                              |
| `workflow/data/templates/candidate-badges.ts`                                        | Types and utilities for workflow template candidate definitions.                                             |
| `workflow/data/templates/index.ts`                                                   | Barrel export for the templates module.                                                                      |
| `workflow/data/templates/template-definition.ts`                                     | `WorkflowTemplate` type and related metadata types.                                                          |
| `workflow/data/templates/template-owner.ts`                                          | Default template-owner constant.                                                                             |
| `workflow/data/templates/vibe-agent.ts`                                              | Built-in Vibe agent workflow template configuration.                                                         |
| `workflow/data/workflow-data.ts`                                                     | Re-exports for workflow data and templates.                                                                  |
| `workflow/data/workflow-types.ts`                                                    | Types for workflow node structures, edges, and metadata.                                                     |
| **workflow / library**                                                               |                                                                                                              |
| `workflow/lib/mermaid-renderer.ts`                                                   | Renders workflow graphs as Mermaid diagrams with multi-level caching.                                        |
| `workflow/lib/workflow-diff.ts`                                                      | Computes a human-readable diff between two workflow versions.                                                |
| `workflow/lib/workflow-execution-builders.ts`                                        | Converts raw API execution responses into typed `WorkflowExecution` models.                                  |
| `workflow/lib/workflow-execution-formatters.ts`                                      | Formats execution status codes and timestamps for display.                                                   |
| `workflow/lib/workflow-execution-storage.ts`                                         | Fetches workflow execution history from the backend API.                                                     |
| `workflow/lib/workflow-execution.types.ts`                                           | Types for workflow executions and run-history records.                                                       |
| `workflow/lib/workflow-storage-api.ts`                                               | API client for workflow CRUD, publishing, and scheduling operations.                                         |
| `workflow/lib/workflow-storage-helpers.ts`                                           | Helper utilities for normalising and transforming workflow storage payloads.                                 |
| `workflow/lib/workflow-storage-versioning.ts`                                        | Manages workflow version history and template-synchronisation logic.                                         |
| `workflow/lib/workflow-storage.ts`                                                   | Main workflow storage façade: load, save, delete, and list workflows.                                        |
| `workflow/lib/workflow-storage.constants.ts`                                         | Constants shared across workflow storage modules.                                                            |
| `workflow/lib/workflow-storage.types.ts`                                             | Types for stored workflow records and API request/response shapes.                                           |
| **workflow / canvas page**                                                           |                                                                                                              |
| `workflow/pages/workflow-canvas/components/settings-tab-content.tsx`                 | Settings tab: workflow metadata display, version history, and listener controls.                             |
| `workflow/pages/workflow-canvas/components/trace-tab-content.tsx`                    | Trace tab: renders execution traces using the agent-prism trace viewer.                                      |
| `workflow/pages/workflow-canvas/components/workflow-canvas-layout.tsx`               | Top-level canvas layout composing the tab bar, navigation, and chat bubble.                                  |
| `workflow/pages/workflow-canvas/components/workflow-config-sheet.tsx`                | Slide-over sheet for editing workflow runtime parameters and tags.                                           |
| `workflow/pages/workflow-canvas/components/workflow-config-sheet.utils.ts`           | Schema inference and form-data conversion utilities for the config sheet.                                    |
| `workflow/pages/workflow-canvas/components/workflow-tab-content.tsx`                 | Workflow editor tab: React Flow canvas, publish/schedule controls, and Mermaid preview.                      |
| `workflow/pages/workflow-canvas/handlers/credentials.ts`                             | Event handlers for credential add, update, delete, and secret-reveal operations.                             |
| `workflow/pages/workflow-canvas/helpers/execution.ts`                                | Utility functions for normalising execution status values.                                                   |
| `workflow/pages/workflow-canvas/helpers/trace.ts`                                    | Transforms raw trace data and computes span display metadata.                                                |
| `workflow/pages/workflow-canvas/helpers/types.ts`                                    | Shared types for execution and trace data used across canvas helpers/hooks.                                  |
| `workflow/pages/workflow-canvas/hooks/controller/build-layout-props.ts`              | Assembles the full layout-props object from core, resource, and execution sub-hooks.                         |
| `workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-controller.ts`  | Main controller hook that composes all canvas sub-hooks into a single interface.                             |
| `workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-core.ts`        | Core hook managing metadata, execution, UI, WebSocket, and chat state.                                       |
| `workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-execution.ts`   | Handles workflow run triggering, status polling, and trace updates.                                          |
| `workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-lifecycle.ts`   | Wires loader and storage-listener hooks into the canvas lifecycle.                                           |
| `workflow/pages/workflow-canvas/hooks/controller/use-workflow-canvas-resources.ts`   | Composes credential management, listener, and auto-save hooks.                                               |
| `workflow/pages/workflow-canvas/hooks/execution-log-helpers.ts`                      | Formats raw execution log lines for display.                                                                 |
| `workflow/pages/workflow-canvas/hooks/execution-node-status.ts`                      | Derives per-node status indicators from the current execution record.                                        |
| `workflow/pages/workflow-canvas/hooks/execution-record-updater.ts`                   | Logic for merging incremental execution updates into the execution record.                                   |
| `workflow/pages/workflow-canvas/hooks/execution-record.ts`                           | Creates the initial execution record structure when a run starts.                                            |
| `workflow/pages/workflow-canvas/hooks/execution-runtime-updates.ts`                  | Applies real-time runtime updates to the canvas execution state.                                             |
| `workflow/pages/workflow-canvas/hooks/use-studio-ui-state.ts`                         | Manages active-tab and sidebar-visibility UI state for the canvas.                                           |
| `workflow/pages/workflow-canvas/hooks/use-execution-trace.ts`                        | Hook that fetches and stores the execution trace for the current run.                                        |
| `workflow/pages/workflow-canvas/hooks/use-execution-updates.ts`                      | Subscribes to WebSocket execution events and forwards them to state.                                         |
| `workflow/pages/workflow-canvas/hooks/use-pause-workflow.ts`                         | Hook exposing a pause action for an in-progress workflow execution.                                          |
| `workflow/pages/workflow-canvas/hooks/use-workflow-chat.ts`                          | Manages the workflow-specific ChatKit session lifecycle.                                                     |
| `workflow/pages/workflow-canvas/hooks/use-workflow-credentials.ts`                   | Hook for loading and mutating workflow-scoped credentials.                                                   |
| `workflow/pages/workflow-canvas/hooks/use-workflow-execution-state.ts`               | Central state container for the current workflow execution.                                                  |
| `workflow/pages/workflow-canvas/hooks/use-workflow-listeners.ts`                     | Manages workflow event listeners (webhooks / triggers).                                                      |
| `workflow/pages/workflow-canvas/hooks/use-workflow-loader.ts`                        | Loads workflow metadata, definition, and execution history on mount.                                         |
| `workflow/pages/workflow-canvas/hooks/use-workflow-metadata-state.ts`                | State and mutators for workflow name, description, tags, and version info.                                   |
| `workflow/pages/workflow-canvas/hooks/use-workflow-saver.ts`                         | Auto-saves workflow changes on a debounced schedule.                                                         |
| `workflow/pages/workflow-canvas/hooks/use-workflow-storage-listener.ts`              | Listens for external (cross-tab / server-push) workflow storage updates.                                     |
| `workflow/pages/workflow-canvas/hooks/workflow-runner-websocket.ts`                  | Creates and configures the WebSocket connection for live execution updates.                                  |
| `workflow/pages/workflow-canvas.tsx`                                                 | Top-level workflow canvas page that integrates the layout with the controller hook.                          |
| **workflow / gallery page**                                                          |                                                                                                              |
| `workflow/pages/workflow-gallery/types.ts`                                           | Types for gallery tab identifiers and gallery component state.                                               |
| `workflow/pages/workflow-gallery/use-workflow-gallery-actions.ts`                    | Gallery action handlers: import workflow, delete workflow, export workflow.                                  |
| `workflow/pages/workflow-gallery/use-workflow-gallery-state.ts`                      | Hook managing gallery state: workflow list, search query, and active tab.                                    |
| `workflow/pages/workflow-gallery/use-workflow-gallery.ts`                            | Composed hook combining gallery state and action hooks.                                                      |
| `workflow/pages/workflow-gallery/workflow-card-size.ts`                              | CSS utility class for the workflow card aspect-ratio.                                                        |
| `workflow/pages/workflow-gallery/workflow-card.tsx`                                  | Workflow card component with open, use-template, export, and delete actions.                                 |
| `workflow/pages/workflow-gallery/workflow-gallery-tabs.tsx`                          | Tab bar switching between user workflows and templates, with a search input.                                 |
| `workflow/pages/workflow-gallery.tsx`                                                | Main workflow gallery page.                                                                                  |
| **workflow / types**                                                                 |                                                                                                              |
| `workflow/types/credential-vault.ts`                                                 | Types for the credential vault: access-level enum, health-status enum, input/output shapes.                  |
