# Web and Cloudflare

## 1. Purpose

This subsystem serves the public static dashboard, bounded public APIs, and
private Admin/Assistant surfaces from Cloudflare. It mirrors local dashboard
projections but does not own local forecasting evidence.

## 2. Execution boundary

Prerendered pages and immutable assets are `STATIC`. API and selected dynamic
paths enter one Cloudflare `WORKER`. Each API route is a request handler loaded
by the minimal router. D1 is a store boundary. Vectorize is a retained derived
Assistant index. No Durable Object exists in current `main`.

| Dimension | Current state |
|---|---|
| Ownership | Static build owns assets; selected route owns each request; D1 owns public mirror and retained private state by contract. |
| Boundary | `STATIC`, `WORKER`, `REQUEST HANDLER`, `D1 STORE`, `Vectorize STORE`. |
| Critical Path | Static shell plus bounded status snapshot. |
| Bounded Work | Route-specific bytes/pages/D1 operations and lazy resources. |
| Incremental | Indexed D1 ledgers, snapshot IDs, cursors, migrations and build manifest. |
| Failure Isolation | Web failure does not stop Windows evidence; optional API failure does not erase other snapshots. |

## 3. Owner

`web/worker/index.ts` owns Worker dispatch and static fallback.
`web/worker/api-router.ts` owns minimal API routing. Each module under
`web/app/api/` or `web/app/admin/api/` owns its resource contract. Dashboard
Sync remains the writer owner for local-derived public projections.

## 4. Inputs and outputs

Inputs are prerendered assets, build deployment metadata, D1 rows, Access/admin
identity, authenticated ingest, and retained Assistant/Vectorize data. Outputs
are HTML/assets, status and lazy resource JSON, admin sessions, operator retry
commands, retained Assistant reads, and fail-closed paused Assistant responses.

## 5. Durable state

D1 holds dashboard snapshots, news detail/index/evidence, market and learning
history, bounded materialized overviews, operator retry requests/events, news
questions, and retained Assistant conversations/messages/jobs. Vectorize holds
derived Assistant memory vectors. Wrangler migrations are schema authority;
runtime capability checks do not migrate.

## 6. Current data flow

```text
build -> prerendered public routes + immutable assets -> ASSETS binding
request /api/* -> minimal Worker router -> selected route -> D1
authenticated local sync -> ingest routes -> D1 public mirror
admin request -> Access/session boundary -> private route -> D1
build -> expand and validate bounded architecture/manifest.json v2 -> lazy React Flow/Dagre Explorer chunk with one camera-intent owner
```

The public shell is not rendered through React SSR on every request. Snapshot
JSON that D1 has already validated is returned without an unnecessary
parse/serialize cycle on the fast path.

## 7. Critical path

Public first paint uses static HTML/assets and the bounded status resource.
Audit, learning, market, news archive, and Assistant resources are lazy or
paged. Private model-consuming routes must authenticate before parsing, D1
mutation, or provider work.

## 8. Bounded-work mechanisms

The Worker router loads only the selected route. Public snapshots have route
payload and D1-operation limits. History and news resources use indexed pages
and bounded overviews. Assistant contracts define finite turns, tool rounds,
events, leases, pages, and capacity, although execution is currently paused.
Static build snapshots are finite and carry explicit missing/unavailable
provenance.

The private `/admin/architecture` route consumes the bounded architecture
manifest at build time. Its read-only React Flow renderer uses Dagre once per
selected bounded view to position all of that view's explicit nodes and edges.
It has no API, D1 table, Worker handler, GitHub runtime request, Markdown
parser, Windows process, or background owner. The manifest, graph libraries,
and scoped graph CSS are referenced only by the lazy Explorer view, so they do
not join the public Live initial dependency path. Source links bind to the
immutable build SHA.

Explore is the beginner-first default: one System Overview leads through
node-owned subsystem actions and breadcrumbs, while one Advanced menu contains
advanced topology and campaign views. Reference mode provides direct access to
the same manifest and complete current-view relationships. View navigation and
progressive-disclosure modes are manifest metadata, not component-owned copies.

The graph calculates layout, lanes, per-edge anchors, and routes from the
complete current view. Disclosure then filters rendered edges and their exact
ports; disclosure does not rerun Dagre, trigger Fit, or change zoom. The package
view therefore starts with nine nodes and no edges,
reveals incident dependencies for the selected package, and exposes the full
`DEPENDENCY` graph only through Show All or Reference. Its text list is derived
from the same manifest edges.

The graph renders manifest lanes as pointer-transparent labelled regions.
Mobile uses a bounded lane-first top-to-bottom layout, geometry-derived canvas
height, a node readability zoom floor, and horizontal canvas panning; its
inspector is a viewport bottom sheet. Edge labels remain in the accessible text
equivalent when background or optional visual labels are interaction-only.

## 9. Incremental mechanisms

D1 histories use stable keys, cursors, and materialized overviews. Snapshot
resources replace by received revision. Migrations are numbered and additive.
Build artifacts bind the branch SHA and manifest. Retained Assistant state uses
message/event sequences, leases, summaries, and index generations.

## 10. Failure behavior

D1 capability absence returns an explicit missing-capability error. Invalid or
oversized ingest fails the owned resource without clearing unrelated last-good
snapshots. Preview rejects writes before mutation. Assistant admission is
`PAUSED` and fails closed. A Cloudflare outage does not stop quote collection,
decisions, outcomes, annotation, or training on Windows.

## 11. Restart/recovery behavior

Workers are request-driven and recover from D1/metadata rather than process
memory. D1 migrations and source snapshots are durable. Static assets are
immutable build products. Public mirrors can be repopulated from local
authority, but must never be used to reconstruct missing local forecast
evidence.

## 12. Entry points

- `web/worker/index.ts`
- `web/worker/api-router.ts`
- `web/wrangler.jsonc`
- `web/app/layout.tsx` and route `page.tsx` files
- `web/app/api/*/route.ts` and `web/app/admin/api/*/route.ts`
- `web/build/publish-prerendered-assets.mjs`
- `web/build/architecture-manifest.ts`
- `architecture/manifest.json`

## 13. Core modules

- `web/db/schema.ts`: D1 table ownership map.
- `web/app/api/_shared/dashboard-status.ts`: public status reads.
- `web/app/api/_shared/dashboard-snapshot.ts`: bounded snapshot access.
- `web/app/api/_shared/news-evidence-store.ts`: news evidence pages.
- `web/app/api/_shared/operator-retry.ts`: remote command lifecycle.
- `web/app/_components/DashboardApp.tsx`: application shell composition.
- `web/app/_views/*`: feature views.
- `web/app/_lib/dashboard-resource.ts`: client resource loading/caching.
- `web/app/_lib/admin-auth-session.ts`: admin session boundary.
- `web/app/_lib/architecture-explorer.ts`: fail-closed manifest v2 parsing,
  Dagre view transformation, path/failure selection, search, and exact-SHA links.
- `web/app/_views/ArchitectureExplorerView.module.css`: private graph,
  inspector, accessibility, and responsive presentation ownership.

## 14. Relevant tests

`web/tests/rendered-html.test.mjs`, `dashboard-resource.test.mjs`,
`d1-capabilities.test.mjs`, `news-evidence-store.test.mjs`,
`operator-retry*.test.mjs`, `assistant-*.test.mjs`,
`responsive-scroll.test.mjs`, and `worker-cpu-headroom.test.mjs` cover static
identity, routing, bounds, auth, retained Assistant contracts, and rendering.

## 15. Authoritative contracts/specs

- [Hosting Boundaries](../contracts/HOSTING_BOUNDARIES.md)
- [Preview Isolation](../contracts/PREVIEW_ISOLATION.md)
- [Preview Behavior](../specs/PREVIEW_BEHAVIOR.md)
- [Assistant Security](../contracts/ASSISTANT_SECURITY.md)
- [Cloudflare Hosting](CLOUDFLARE_HOSTING.md)

## 16. Known current gaps

Feature ownership exists but large shared files remain: `web/app/globals.css`,
`web/app/_views/AuditView.tsx`, and
`web/app/audit/LearningGraphModal.tsx`. Route structure must remain compatible
with Vinext/Next conventions; a visually tidy folder tree is not sufficient
reason to move routes. Production-shaped Preview behavior and the isolated live
broadcast transport are current, but neither is forecasting authority and
broadcast remains independently configured and failure-isolated.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
