# Requirements Document

## METADATA
- **Authors:** Codex
- **Project/Feature Name:** Orcheo Hosted Apps
- **Type:** Product
- **Summary:** Let workspace users upload, publish, and operate prebuilt static web applications on unique Orcheo-managed subdomains, with controlled access to Orcheo authentication, workflows, and app-scoped persistent data.
- **Owner (if different than authors):** ShaojieJiang
- **Date Started:** 2026-07-23

## RELEVANT LINKS & STAKEHOLDERS

| Documents | Link | Owner | Name |
|-----------|------|-------|------|
| Design Doc | `./2_design.md` | ShaojieJiang | Orcheo Hosted Apps Design |
| Project Plan | `./3_plan.md` | ShaojieJiang | Orcheo Hosted Apps Plan |
| First-party Auth Initiative | `../first_party_auth/1_requirements.md` | ShaojieJiang | Passwordless email identity |
| Multi-workspace Initiative | `../multi_workspace/1_requirements.md` | ShaojieJiang | Workspace membership and roles |
| Caddy Ingress Initiative | `../caddy_self_hosted_ingress/1_requirements.md` | ShaojieJiang | Bundled public ingress |
| ChatKit Attachment Blobs | `../chatkit_attachment_blobs/2_design.md` | ShaojieJiang | Scoped blob storage precedent |
| Backend App Factory | `apps/backend/src/orcheo_backend/app/factory.py` | ShaojieJiang | API protection and CORS composition |
| First-party Identity Service | `apps/backend/src/orcheo_backend/app/identity/` | ShaojieJiang | Auth challenge, session, and token issuance |
| Workflow Publish API | `apps/backend/src/orcheo_backend/app/routers/workflows.py` | ShaojieJiang | Existing public/private workflow precedent |
| Stack Assets | `deploy/stack/` | ShaojieJiang | Compose and Caddy deployment topology |

## PROBLEM DEFINITION

### Objectives
Allow Orcheo users to turn prebuilt frontend bundles into hosted applications whose backends are composed from Orcheo workflows and managed platform data. Preserve Orcheo's workspace, identity, authorization, quota, and audit boundaries while isolating user-authored JavaScript from Studio and the general Orcheo API.

### Target users
- Workflow builders who want to distribute a purpose-built interface over one or more Orcheo workflows.
- Teams building internal tools that should inherit workspace login and membership.
- Product builders publishing small public applications without operating a separate frontend host or application database. In P0, their external visitors use only explicitly anonymous capabilities; authenticated app access remains limited to current members of the publisher workspace.
- Self-hosted operators who want the same capability through an optional stack profile.

### User Stories
| As a... | I want to... | So that... | Priority | Acceptance Criteria |
|---------|--------------|------------|----------|---------------------|
| Workspace editor | create an app and reserve a unique alias | I have a stable destination for future deployments | P0 | A valid globally unique alias is reserved atomically and an unpublished app record is created in the selected workspace |
| Workspace editor | upload a prebuilt static bundle | I can deploy an application without giving Orcheo source code or running a build on Orcheo infrastructure | P0 | A ZIP containing `index.html` is validated, stored immutably, and appears as a deployment candidate; no uploaded code is executed server-side |
| Workspace admin | bind logical app operations to workspace workflows | the frontend can use workflows as its backend without receiving broad workspace credentials | P0 | Each binding identifies one workflow and an explicit access policy; the app can invoke only named bindings |
| Workspace admin | publish a validated deployment | visitors receive the selected version at the app alias | P0 | Publishing atomically creates and selects an immutable release containing the deployment and approved capability snapshot, and `https://<alias>.<apps-base-domain>` serves it |
| Workspace admin | make an app public or private | I can choose anonymous distribution or workspace-member-only access | P0 | Public assets are anonymously readable; private assets require a valid Orcheo identity with current workspace membership |
| Workspace-member app visitor | sign in with Orcheo when required | I can use a private app without creating a second account | P0 | Login uses the first-party identity service, requires current membership in the publisher workspace, and returns to the app without exposing a Studio token to app JavaScript |
| App author | call an approved workflow from browser code | the application can perform backend work safely | P0 | A same-origin runtime API invokes the configured binding and returns an app-scoped run handle; arbitrary workflow IDs and workspace headers are rejected |
| App author | persist app records | I do not need to expose raw database credentials to browser code | P0 | A same-origin document API stores records scoped by workspace, app, collection, and optional end-user subject |
| Workspace admin | unpublish or roll back immediately | I can recover from a bad or abusive deployment | P0 | New page loads stop resolving to the revoked deployment and an older validated deployment can be promoted atomically |
| Operator | configure the hosted-app domain and storage backend | I can enable the capability in cloud or self-hosted environments | P0 | App hosting is disabled safely when configuration is absent; the optional profile validates wildcard DNS/TLS and bundle-storage prerequisites |
| Platform operator | suspend an alias, app, workspace, or publisher | I can respond to phishing, malware, legal, or quota incidents | P0 | A platform-scoped moderation principal, distinct from any workspace role, can apply a durable block that overrides publisher state within the suspension SLO and is recorded atomically in an audit event |
| App author | inspect deployment and invocation health | I can diagnose upload, publish, storage, and workflow failures | P1 | Studio shows deployment status and aggregate app runtime metrics without exposing sensitive workflow traces to visitors |

### Context, Problems, Opportunities
Orcheo already supplies workflows, workspace tenancy, first-party identity, vault-backed credentials, background execution, public/private workflow semantics, and a bundled ingress tier. Users who want a custom product interface must nevertheless operate another hosting platform, connect a second authentication layer, and carefully expose Orcheo APIs to browser code. That friction prevents workflows from becoming complete distributable applications.

Prebuilt static bundles are a tractable first step because HTML, CSS, and JavaScript execute in the visitor's browser. Orcheo does not need to run package installation, build scripts, application servers, or arbitrary user containers. The primary platform work is therefore control-plane lifecycle, safe asset delivery, app-scoped authorization, workflow binding, persistent app data, and abuse controls.

User-authored JavaScript must be treated as untrusted relative to Studio, Orcheo identity credentials, and unrelated workspace resources. A hosted app is allowed to exercise its explicitly granted capabilities and can disclose any data those capabilities return. Consequently, the security boundary is least-privilege app grants and an isolated app runtime, not an assumption that the uploaded frontend is benign.

### Product goals and Non-goals
**Goals**
- Add an Apps area in Studio for app creation, upload, configuration, deployment history, publishing, rollback, and suspension status.
- Make each published app reachable at `<alias>.<configured-apps-base-domain>`, initially `<alias>.beta.orcheo.cloud` for the hosted beta.
- Preserve Orcheo identity and workspace membership for private apps and authenticated app features.
- Let apps invoke explicitly bound workflows without exposing general user, service, vault, or workspace credentials.
- Provide a small app-scoped persistent data API rather than direct database access.
- Treat deployments as immutable artifacts with atomic promotion and rollback.
- Treat a published release as an immutable pairing of one deployment, one approved capability revision, and one immutable workflow-execution revision per binding.
- Separate the Orcheo control plane from the untrusted app-delivery data plane while shipping both as one Orcheo product and stack.
- Support rapid operator suspension, auditability, quotas, and abuse response.

**Non-goals**
- Running `npm`, Vite, Next.js, or any other user-controlled build process on Orcheo infrastructure.
- Hosting SSR applications, server functions, long-running user processes, containers, or arbitrary backend code.
- Giving browser applications direct SQL, Postgres, Redis, vault, filesystem, or object-store credentials.
- Allowing an app to call arbitrary Orcheo endpoints or select arbitrary workflows and workspaces.
- Automatically making a bound workflow public when an app is published.
- Custom domains, deployment previews, branch deployments, marketplace discovery, monetization, and app templates in the P0 release.
- Treating the bundled app gateway as a global CDN, WAF, or complete DDoS mitigation layer.
- Depending on Public Suffix List acceptance for the initial security model.
- External-customer login or user-scoped data for people who are not current members of the publisher workspace. A broader end-user identity model is P1.
- Service workers, offline-first application behavior, or installable PWAs. P0 must prevent publisher code from registering a service worker over the app origin.

## PRODUCT DEFINITION

### Requirements

#### P0 — App control plane and Studio
- Add a dedicated Studio Apps page that lists apps in the selected workspace and shows name, alias, visibility, publication state, active release/deployment, and health.
- Support creating, viewing, editing, archiving, and restoring an app.
- Workspace role behavior:
  - viewers may inspect app metadata and deployment status;
  - editors may create draft apps, update descriptive metadata, and upload deployments;
  - admins and owners may change aliases, configure bindings and data permissions, publish, unpublish, roll back, suspend workspace-owned apps, and archive apps.
- App lifecycle uses orthogonal publication (`draft`, `published`, `unpublished`), archive, and suspension state. API responses expose a derived display state such as `suspended` or `archived`, and a documented transition table defines restore and reinstatement behavior without losing the prior publication state.
- Draft bindings, collection grants, visibility, external origins, and related permissions belong to a mutable capability revision. A published app continues using its last approved release snapshot until an admin publishes a new revision; a permission reduction or revocation may take effect immediately.
- Every control-plane mutation must derive its actor from authentication and atomically create an audit event, either in the same transaction or through a transactional outbox. Audit failure may not silently leave a sensitive mutation committed without a durable event.
- An operator-level suspension must take precedence over workspace-admin publication state.

#### P0 — Alias and domain lifecycle
- The apps base domain is configured through `ORCHEO_APPS_BASE_DOMAIN`; the hosted beta value is `beta.orcheo.cloud`.
- Aliases must:
  - be normalized to lowercase;
  - contain 3–48 ASCII characters;
  - start and end with an alphanumeric character;
  - contain only `a-z`, `0-9`, and internal hyphens;
  - be globally unique within one Orcheo deployment;
  - exclude a configurable reserved-name list including `api`, `auth`, `admin`, `studio`, `www`, `mail`, `support`, and infrastructure names.
- Alias reservation and changes must be transactional and enforced by a case-insensitive unique database constraint.
- Released aliases must enter a configurable tombstone period before reuse to reduce impersonation and takeover risk.
- Alias change, release, archive, and restore semantics must specify whether the alias remains reserved. Any alias reassignment must not inherit sessions, storage, cached release metadata, or browser execution state from its prior owner.
- P0 supports one platform alias per app and no custom domains.

#### P0 — Bundle upload and deployment lifecycle
- Accept a prebuilt ZIP archive containing a root `index.html` plus static assets.
- Never install dependencies, evaluate uploaded JavaScript, or execute bundle-provided commands during validation or serving.
- Validate compressed size, expanded size, file count, path depth, filename encoding, duplicate paths, absolute paths, parent traversal, symlinks, hard links, nested archives, and per-file size.
- Reject server executables and unsupported special files. Preserve supported web assets with server-derived MIME types.
- Reject bundle paths under the reserved `__orcheo/` namespace. Parse HTML during validation, reject unsupported executable constructs, and record SHA-256 hashes for supported inline scripts so the gateway can emit a strict per-deployment CSP without requiring `script-src 'unsafe-inline'`.
- Generate a SHA-256 digest for the accepted deployment and store extracted files under an immutable deployment prefix.
- Record validation failures in the deployment record without making partial artifacts publishable.
- Support multiple immutable deployments per app, one atomic `active_release_id` whose release references exactly one ready deployment and approved capability snapshot, unpublish, and reviewed rollback to any still-valid deployment.
- Model staged uploads explicitly with short expiry, expected size/checksum, private object version, single-use completion, and cleanup state. Presigned uploads must enforce provider-supported size/checksum conditions and server-side verification before validation.
- Object-store promotion is logically atomic: files are written idempotently to a unique immutable prefix, the authoritative manifest is completed last, and only then may one database transaction mark the deployment ready. The design must not depend on an atomic S3 prefix rename.
- Bundle uploads must use a provider-neutral store. Production hosting uses S3-compatible object storage; a filesystem backend may be supported only for local development and explicitly documented single-node installs.
- Do not store raw bundle bytes in workflow state, audit payloads, or ordinary app metadata rows.

#### P0 — Hosting and runtime delivery
- Add a dedicated app-gateway service or process to the Orcheo stack.
- Wildcard ingress must route `*.<apps-base-domain>` to the app gateway, while the existing Studio/API hostname keeps its current routing.
- The gateway must resolve the exact request host to an active app and deployment; client-supplied app or workspace identifiers are not authoritative.
- Public apps serve their active static assets anonymously. Private apps require a current app session before serving `index.html` or bundle assets, excluding only platform-owned authentication callback paths.
- SPA navigation must fall back to `index.html` only when the requested path is not a real asset and is not under the reserved `/__orcheo/` runtime namespace.
- Only server-verifiably content-addressed URLs containing a deployment or content digest may use long-lived `immutable` caching. Stable alias paths, all HTML, app state, and alias-to-release resolution must revalidate so publish, unpublish, and rollback take effect promptly. Private assets must not enter shared caches and use `no-store` or a short explicitly bounded browser lifetime.
- The gateway must set platform security headers that an uploaded bundle cannot remove, including protections for framing, MIME sniffing, referrer leakage, and dangerous object embedding.
- A strict deployment-specific Content Security Policy is applied. It may include validator-derived hashes for inline scripts. `worker-src 'none'`, `object-src 'none'`, `base-uri 'none'`, and `frame-ancestors 'none'` are mandatory and non-relaxable. Any publisher-configurable external origins must be declarative, validated, limited to named directives, and unable to weaken mandatory platform directives.
- Private assets and reserved runtime responses use `Cross-Origin-Resource-Policy: same-origin`; authentication, run, and app-data responses use `Cache-Control: private, no-store` and appropriate `Vary` headers.

#### P0 — Authentication and private access
- Reuse Orcheo's first-party passwordless identity and workspace membership; do not create an app-specific user database or second login system.
- In P0, `authenticated` always means a current member of the app's owning workspace. Public apps may offer optional login only to those members; authentication for external customers is deferred.
- Do not transfer or expose Studio access or refresh tokens to uploaded JavaScript.
- Private app login must use a central redirect and single-use authorization-code exchange. The app gateway stores the resulting app session in a host-only, `HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-` prefixed cookie.
- Gateway login transactions must keep the PKCE verifier, state hash, exact app
  host, safe relative return path, and expiry server-side. The browser holds only
  an opaque random transaction secret in a host-only `__Host-`, `HttpOnly`,
  `Secure`, `SameSite=Lax` cookie. The authorization request and callback
  contracts must define exact parameters, exact callback equality, PKCE S256, and
  state validation.
- App sessions must be restricted to one app, workspace, exact canonical alias host, user, and expiration time, with documented absolute and idle defaults. They must be revocable when the alias changes, the app is unpublished, suspended, archived, the membership is removed, the workspace is inactive, or the hosted-app runtime is globally disabled.
- Public apps may request optional login for authenticated-only bindings without changing the app's asset visibility.
- State-changing runtime requests, including anonymous mutations, must validate the exact app origin, strict media type, Fetch Metadata where available, and session-bound CSRF protection independently of SameSite cookie behavior.
- No authentication cookie may be set for `.orcheo.cloud`, `.beta.orcheo.cloud`, or another parent domain shared by multiple apps.

#### P0 — Workflow bindings and invocation
- An app binds a logical name to exactly one workflow in the same workspace. Uploaded JavaScript calls the logical name, never a workflow UUID.
- Binding configuration must include:
  - logical name and optional description;
  - workflow id and immutable executable revision or digest covering the graph and runnable configuration;
  - access mode: anonymous or authenticated;
  - input schema and maximum payload size;
  - output projection/schema;
  - per-IP, per-session, and per-app rate limits;
  - concurrency and timeout limits;
  - whether app visitors may read final output and sanitized errors.
- Publishing must fail if a referenced workflow/version is missing, archived, invalid, or belongs to another workspace.
- The implementation must define these terms against Orcheo's actual workflow model: an archived parent workflow is unavailable, and a bound executable revision must exist, compile successfully, and match its approved digest. A mutable workflow-version UUID alone is not a sufficient pin.
- App publication and workflow public status are independent. A private workflow may be invoked only through an active app binding without becoming globally public.
- Invocation must resolve the app, active release/deployment, immutable binding snapshot, workspace, and visitor session on the server. Client-supplied workspace headers, workflow ids, actor ids, or service credentials are ignored or rejected.
- Runtime responses expose opaque app-run handles and the binding's projected result. They must not expose general workflow run history, runnable configuration, vault references, node traces, or unrelated workspace data.
- Authenticated app-run handles must remain bound to the originating app session/user. Anonymous handles are short-lived high-entropy bearer capabilities and may expose only results approved for anonymous disclosure.
- Run acceptance must be idempotent and durable: the internal workflow run, public handle, quota reservation, idempotency record, and dispatch outbox are committed atomically before a worker is notified.
- Anonymous workflow use must be an explicit binding grant and must consume workspace governance limits. Suspending or unpublishing the app revokes new invocations immediately.

#### P0 — App data API
- Provide same-origin JSON document storage under the reserved `/__orcheo/data/` namespace.
- Data is always scoped server-side by `workspace_id` and `app_id`; collections may additionally be scoped by the authenticated app visitor.
- App configuration declares allowed collections and access mode:
  - `shared`: all authorized visitors of the app share records;
  - `user`: records are additionally scoped to the authenticated user and unavailable anonymously.
- Each collection declares `read_access` and `write_access` as `anonymous` or `authenticated`; `user` collections require authenticated access for both. Anonymous writes must be an explicit admin grant and receive stricter rate and storage limits.
- Provide create, get, update with optimistic concurrency, list with cursor pagination, and delete operations.
- Enforce collection-name validation, document size, query, row-count, and total-storage quotas.
- Collection definitions use stable identifiers. Delete/recreate behavior must prevent records from silently reappearing under a new permission definition that reuses the same collection name.
- The client may never supply or override authoritative workspace, app, owner-subject, or storage-backend fields.
- Raw SQL, joins against Orcheo internal tables, arbitrary indexes, schema DDL, and database credentials are out of scope.

#### P0 — Operations, security, and abuse controls
- App creation, upload reservations, uploaded and extracted bytes, retained deployments, app-data rows/bytes, workflow invocations, concurrent runs, and active sessions must be quota-controlled with distributed atomic reserve/commit/release semantics. Limits must define retryability and fail-open/fail-closed behavior when Redis, workers, or storage are unavailable.
- Per-IP enforcement must derive the client address only from a configured trusted ingress/CDN chain. Browser forwarding headers are stripped, and the gateway passes a normalized, service-authenticated client-IP assertion to the backend.
- Add platform-operator controls, protected by explicit global moderation scopes rather than workspace roles, to reserve aliases, block or reinstate an app/alias/workspace/publisher, inspect ownership, and record a reason without exposing private app contents broadly.
- Add a global runtime generation/kill switch checked by control plane, gateway, and runtime authorization. Disabling Hosted Apps stops new mutations, delivery resolution, login, and runtime access, invalidates caches, and revokes outstanding codes/sessions within the suspension SLO.
- Publish and alias mutation must be blocked for suspended or inactive workspaces.
- Log and measure app resolution failures, deployment validation failures, asset response classes, auth outcomes, workflow invocation counts/latency/failures, quota rejections, and app-data usage.
- Do not log bearer tokens, authorization codes, session cookies, bundle contents, app documents, workflow inputs, or workflow outputs by default.
- Provide a documented abuse-report and takedown procedure before enabling open self-service public publishing.
- The hosted production deployment should prefer a separate registrable user-content domain. If `beta.orcheo.cloud` is used, the cookie and CSRF constraints above are release blockers.
- Define retention and deletion behavior for staged uploads, failed and ready deployments, release snapshots, aliases/tombstones, sessions/codes, run handles, collection definitions and records, workspace deletion, user membership removal, backups, legal holds, and operator takedown. Quota reclamation and object-store deletion use auditable reconciliation jobs.

#### P1 — Follow-up capabilities
- Deployment preview URLs with authenticated, non-indexed access.
- Custom domains using validated ownership and on-demand TLS.
- CDN integration and purge/invalidation APIs.
- A browser SDK package wrapping auth, workflow invocation, run polling/streaming, and app data.
- External end-user identity for authenticated public applications, with an explicit audience/consent model distinct from publisher-workspace membership.
- Workspace-admin collection inspection and export with privacy controls, audit, retention enforcement, and safeguards against exposing user-scoped data broadly.
- Server-sent events or WebSocket streaming for app-run progress.
- Explicit rolling workflow bindings that follow a workspace-approved latest version.
- Visitor-facing consent when a release expands user-visible access beyond the already-required P0 immutable release capability snapshot.
- Public Suffix List application after the service is established and meets the list's acceptance criteria.
- App analytics, budgets, billing attribution, and downloadable audit reports.

### Designs (if applicable)
See `./2_design.md`. The primary Studio surfaces are Apps list, create/edit, deployment upload/history, workflow bindings, data permissions, publish review, and app health.

### Other Teams Impacted
- **Backend/API:** new app control-plane resources, runtime authorization, app data, and internal gateway contracts.
- **Studio:** new Apps navigation and lifecycle screens.
- **Identity:** single-use app authorization codes and revocable app sessions.
- **Workflow runtime/worker:** app-originated execution context, projected output, and governance accounting.
- **Deployment/infra:** wildcard DNS/TLS, app gateway, bundle storage, runtime cache/session store, and optional self-host profile.
- **Security/operations:** phishing response, suspension, reserved aliases, audit, and monitoring.
- **SDK/docs:** P0 bundle/runtime contracts, examples, configuration, and operator guides; the convenience browser JavaScript SDK remains P1.

## TECHNICAL CONSIDERATIONS

### Architecture Overview
Orcheo's existing backend remains the control plane and policy authority. It owns app metadata, aliases, deployments, workflow bindings, data permissions, authorization codes, audit, and quotas. A dedicated app gateway is the untrusted-content data plane: it resolves app hosts, serves immutable bundle assets, maintains host-scoped sessions, and proxies the reserved same-origin runtime API to backend endpoints that re-evaluate app grants.

The app gateway is a separate deployable component in the same repository and stack, not a separate product or identity system. Workflow execution remains in the existing backend/worker path. Bundle bytes live in object storage, while app metadata, grants, sessions, and document records use workspace-scoped platform stores.

### Technical Requirements
- Add a provider-neutral app repository and bundle-store contract with Postgres metadata persistence and S3-compatible production artifacts.
- Add immutable published-release snapshots, explicit staged-upload persistence, moderation blocks, runtime idempotency/outbox records, and database-enforced composite tenant ownership.
- Extend the first-party identity service with an OAuth-like, single-use authorization-code flow for app sessions without turning each app into an independent IdP client implementation.
- Add an internal authenticated contract between app gateway and backend. The gateway must strip client attempts to supply trusted internal headers.
- Add service-token scopes or equivalent internal service identity restricted to app runtime operations.
- Mount the internal gateway router outside client-selected workspace resolution, exclude it from public OpenAPI and Studio SPA fallback, and accept only the dedicated gateway scope.
- Keep the existing explicit Studio API CORS allow-list. App JavaScript uses same-origin runtime paths rather than receiving wildcard access to the general `/api` surface.
- Add wildcard DNS/TLS stack configuration. A platform-owned wildcard certificate is preferred for the configured base domain; on-demand TLS is deferred to custom domains.
- Ensure app-data and deployment schemas are workspace-scoped and have explicit cleanup/retention behavior.
- Add governance limits for hosted-app count, deployment bytes, app-data rows/bytes, invocation rate, and active sessions.
- Provide a dedicated deployment-validation worker consumer and queue with bounded resources so archive processing cannot starve workflow execution.

### AI/ML Considerations (if applicable)
Not applicable. Apps may bind workflows containing AI nodes, but no new model-training or model-selection behavior is introduced by this initiative.

## MARKET DEFINITION (for products or large features)

### Total Addressable Market
The initial addressable audience is Orcheo workflow builders and teams that need internal tools, customer-facing workflow applications, demos, portals, and task-specific interfaces. Applications requiring server-side rendering, custom server processes, direct database protocols, regulated hosting controls not yet offered by Orcheo, or arbitrary container execution are outside the initial addressable market.

### Launch Exceptions

| Market | Status | Considerations & Summary |
|--------|--------|--------------------------|
| Anonymous public publishing | Limited beta | Requires abuse reporting, operator suspension, conservative quotas, and verified publisher identity |
| Self-hosted public apps | Opt-in | Requires operator-managed wildcard DNS/TLS and compatible bundle storage |
| Regulated workloads | No launch commitment | Requires a separate compliance and data-residency assessment |

## LAUNCH/ROLLOUT PLAN

### Success metrics
| KPIs | Target & Rationale |
|------|--------------------|
| [Primary] Successful publish rate | At least 95% over a rolling 7-day beta window of eligible upload attempts that pass bundle validation and publish review, excluding user cancellation |
| [Primary] First successful app | Median under 10 minutes from the first app-created event to the first successful canonical-URL navigation, measured for new beta apps |
| [Primary] Runtime reliability | At least 99.9% over a rolling 7-day window of eligible requests to published aliases return the expected 2xx/304 static response, excluding publisher errors and suspended/unpublished apps |
| [Secondary] Workflow-backed adoption | At least 50% of beta apps invoke one or more workflow bindings |
| [Secondary] Rollback recovery | A workspace admin can restore a prior deployment in under 2 minutes |
| [Guardrail] Cross-scope access | Zero confirmed cross-app, cross-workspace, or cross-user data/access incidents |
| [Guardrail] Credential exposure | Zero Studio access/refresh tokens or raw vault credentials exposed to app JavaScript |
| [Guardrail] Abuse response | Gateway resolution, new navigations, login, and runtime access stop within 60 seconds of suspension; previously downloaded public bytes are outside the recall guarantee |
| [Guardrail] Core API isolation | Under the documented beta load profile, app traffic increases Studio/API p95 latency by no more than 5% and does not increase its error rate by more than 0.1 percentage points |

### Rollout Strategy
- Ship behind a global hosted-apps feature flag and disabled-by-default stack profile.
- Complete a baseline threat model, choose and document the beta/production registrable-domain boundary, and establish publisher-verification and takedown ownership before enabling any non-staff publisher.
- Begin with staff-owned apps and a separate staging apps domain.
- Progress to selected verified workspaces with conservative app, storage, and invocation quotas.
- Enable public publishing only after suspension, audit, abuse reporting, and rate limits are proven.
- Keep custom domains and server-side execution disabled throughout the initial rollout.

### Estimated Launch Phases
| Phase | Target | Description |
|-------|--------|-------------|
| **Phase 1** | Internal dogfood | Public static bundles, immutable deployments, alias routing, manual workflow bindings, and operator suspension |
| **Phase 2** | Selected workspaces | Private apps, central login, app sessions, workflow runtime API, and app data API |
| **Phase 3** | Hosted beta | Verified publishers may create public apps under the configured beta domain with quotas and abuse controls |
| **Phase 4** | Self-hosted opt-in | Document and ship the app-gateway profile with wildcard DNS/TLS and storage prerequisites |

## HYPOTHESIS & RISKS

**Hypothesis:** Workflow builders will ship useful applications faster when Orcheo provides the frontend hosting, identity, workflow-binding, and data primitives needed to turn a workflow into a distributable product.

**Risks and mitigations**
- **Phishing and malicious content:** Public static hosting can be abused even when no server code runs. Require verified publishers for beta, reserve sensitive aliases, apply quotas, provide reporting and immediate operator suspension, and keep authentication on the recognizable central Orcheo origin.
- **Sibling-subdomain security:** `*.beta.orcheo.cloud` shares a registrable domain with trusted services. Use no parent-domain cookies, use host-only `__Host-` app cookies, keep Studio tokens origin-local, enforce Origin/CSRF checks, and prefer a separate user-content registrable domain for production.
- **Persistent browser control:** A service worker could retain control of an alias, intercept reserved runtime paths, and outlive suspension or alias reassignment. P0 forbids service workers with a mandatory CSP and verifies the prohibition in real browsers.
- **Stale cached releases:** Long-lived caching on stable alias paths would mix deployment versions and undermine rollback. Only content-addressed release URLs are immutable; stable and private paths revalidate or do not store.
- **Workflow credential abuse and cost amplification:** A public app could drive workflows that use workspace credentials or paid APIs. Require explicit anonymous grants, binding-specific schemas and projections, quotas, concurrency limits, and revocation that does not depend on changing workflow public state.
- **Mutable workflow configuration:** Existing workflow-version runnable configuration can change in place. Hosted-app bindings therefore approve an immutable executable digest/snapshot; the active release keeps using its copied snapshot, while dependent draft review becomes stale when the source execution definition changes.
- **Publisher code can misuse allowed data:** A hosted frontend can exfiltrate any data returned by its approved capabilities. Make capabilities narrow, show them during publish review, require renewed review when permissions expand, and never expose broad API tokens.
- **Bundle and asset traffic affects core Orcheo:** Serving assets through the main API would couple untrusted load to control-plane reliability. Use a dedicated gateway and object storage with independent limits and scaling.
- **Object-storage and database growth:** Immutable deployments and app records can grow without bound. Enforce quotas, retention, archival cleanup, storage metrics, and documented backup/lifecycle policies.
- **Feature scope expansion:** Build pipelines, SSR, custom domains, and arbitrary server code would change the trust model substantially. Keep explicit non-goals and require separate initiatives for those capabilities.

## APPENDIX

### Key decisions
- Orcheo Hosted Apps is one Orcheo product and control plane with a separately deployable app-delivery data plane.
- P0 accepts only prebuilt static bundles.
- Apps use same-origin runtime endpoints and never receive a general Orcheo token.
- P0 authenticated visitors are current publisher-workspace members; external-customer identity is deferred.
- Published releases atomically pair a deployment with an approved capability revision and immutable workflow-execution revisions.
- Service workers are unsupported and blocked by a non-relaxable `worker-src 'none'` policy.
- Only deployment/content-addressed asset URLs receive long-lived immutable caching.
- Workflow bindings are app-scoped and independent of workflow `is_public`.
- Persistent app data is exposed through a scoped document API, not direct database credentials.
- Hosted beta URLs use `<alias>.beta.orcheo.cloud`, but a separate registrable user-content domain remains the preferred production isolation boundary.
