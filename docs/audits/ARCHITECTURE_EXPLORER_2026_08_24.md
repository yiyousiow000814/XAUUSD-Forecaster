# Private Architecture Explorer Audit — 2026-08-24

## Generated evidence extension — 2026-08-26

Draft PR #328 extends the accepted #304 graph, routing, camera, disclosure, and
mobile interaction boundaries without redesigning them. It consumes compiler
artifacts at exact Git SHA `91030a458f5ae1bfc9da59854afb6d910d35b977`
and adds only private lazy evidence surfaces:

- node and edge evidence status on the existing graph;
- exact repository-relative source spans with extractor and certainty;
- generated package/module/symbol code drill-down;
- separate Observed Imports, Allowed Policy, and Violations views;
- contract test execution and runtime-trace evidence;
- a test-effectiveness panel that reports 16 verified critical contracts while
  retaining three surviving designated mutations as explicit gaps.

The exact immutable Preview is version
`2dd07cc1-44b4-49e6-af7d-44c43f3403c2` at
`https://2dd07cc1-aurum-signal-room-preview.yiyousiow1234.workers.dev/admin/architecture`.
Desktop 1440x900 and phone 390x844 / 360x800 passed. The phones retained the
168px node, 17px primary text, 13px CSS lane-heading, 44px target, canvas-contained
horizontal pan, and page-overflow contracts. Closing either sheet retained the
path and viewport; Clear Path remained the only selection-clearing action.

Screenshots are under
`docs/audits/screenshots/architecture-evidence-91030a45/`. The task-owned
browser tab was closed and its viewport override reset. The historical #304
evidence below remains valid for its exact older Version only.

## Outcome

The first Explorer renderer was rejected because it presented a two-column
card catalog and permanent detail wall rather than an architecture graph. The
bounded manifest, build-time injection, private route, security boundary, and
exact-SHA source links were retained. The renderer is now a read-only React
Flow node-link graph with Dagre fallback, optional semantic layout constraints,
visible directed connectors. The final information architecture is
beginner-first without changing the accepted graph geometry or camera owner.

## Architecture gate

```text
Owner: Architecture manifest plus the private Explorer interaction reducer and renderer
Authoritative state/store: Git-tracked architecture/manifest.json; ephemeral browser interaction state has no durable store
Execution boundary: Bounded build-time loader to the lazy Admin React Flow/Dagre client chunk; one in-page reducer
Critical or optional: Optional private static presentation
Maximum work per operation: One manifest no larger than 65,536 bytes; one selected view and one mobile sheet render
Incremental cursor/revision/checkpoint: Manifest schema v2, immutable build SHA, and latest camera intent
Failure domain: Manifest validation, ephemeral mobile interaction state, and the private Explorer chunk only
Last-good/recovery behavior: Invalid manifests fail the build; reducer boundary events normalize invalid panel combinations; revert with no data migration
Architecture documents affected: architecture/README.md, WEB_AND_CLOUDFLARE.md, CODEBASE_MAP.md, this audit
```

There is no Architecture API, D1 table, Worker route, GitHub runtime request,
Markdown parser, Windows process, background thread, or production mutation.

## Manifest v2 inventory

- Schema: `architecture-explorer-v2`
- Views: 11
- Nodes: 37
- Edges: 66
- Guided scenarios: 4
- Explicit failure-impact definitions: 8
- Serialized bytes: 52,331 of the fixed 65,536-byte limit
- Edge IDs, endpoints, labels, kinds, criticalities, and descriptions are explicit.
- View edge membership, visible endpoints, continuous primary paths, lane
  membership, scenario continuity, and failure references fail closed.
- Coordinates are not stored; Dagre calculates finite fallback positions per
  selected view and validated hints may constrain semantic relationships.
- Every node has a non-empty `purpose` separate from summary and ownership.
- Every view declares a navigation role, audience, optional parent, disclosure
  mode, always-visible edges, secondary edges, and Show All permission.
- The package view has nine explicit canonical package nodes and 28
  `DEPENDENCY` edges matching `PACKAGE_DEPENDENCIES.md`; it contains no runtime
  transport, materialization, Candidate, or published-model node.

## Interaction and presentation evidence

### Beginner-first navigation and disclosure

- Explore is the default experience. It starts at the single beginner System
  Overview and removes the permanent 11-view selector.
- Desktop node selection retains click-to-open explanation behavior. On mobile,
  first tap selects and discloses the relationship path while keeping the graph
  visible; a compact dock then offers View Details, Open Subsystem when owned,
  explicit Failure Impact when available, and Clear Path.
- The compact toolbar contains search, scenarios, Fit, and one controlled
  Advanced trigger. Explore Advanced contains only Execution Topology, Runtime
  and Release, Canonical Package Dependencies, and Modularization Campaign.
  Reference exposes all views and dense controls from the same manifest.
- Overview defaults to the cTrader → Business Runtime → Decision → Evidence →
  Dashboard → Worker/Browser spine plus optional News → Decision. Feedback and
  release-control relationships remain secondary until selection or Show All.
- Training uses three lanes for the monotonic Evidence → Materialization →
  Generation → Published Model → Decision path. News and Dashboard start with
  only their subsystem-specific relationships. Runtime and Release starts with
  the release path while supervision remains secondary. Assistant remains PAUSED.
- Package Dependencies starts with nine nodes and zero edges. Selection reveals
  only incident dependencies; Show All Dependencies and Reference expose all 28.
- Complete-view layout, ports, and routes are calculated once. Disclosure only
  filters rendered edges and matching ports, so it cannot move nodes, change
  anchors, rerun Fit, or take camera ownership.
- Twenty-four added behavior tests cover taxonomy, default disclosure, selection,
  scenarios, stable geometry, subsystem semantics, package selection, Show All,
  and exact visible-port correspondence.

### Mobile interaction and viewport ownership

- One pure reducer owns `activePathNodeId`, `inspectorNodeId`, `inspectorOpen`,
  `advancedOpen`, and the mutually exclusive `mobilePanel`. It prevents an
  orphan Inspector and prevents Inspector and Advanced from coexisting.
- Inspector close preserves path disclosure, highlighting, view, scenario, and
  camera. Clear Path is separate. Opening either sheet closes the other without
  clearing the path; incompatible view/mode boundaries clear both deterministically.
- Both mobile sheets use portal-backed controlled dialogs with a visible close,
  backdrop and Escape close, focus containment/restoration, exact page-scroll
  lock/restore, internal scrolling, and safe-area padding.
- Visible canvas height is derived from the actual visual viewport. Portrait is
  `clamp(480px, 68dvh, 720px)` and short landscape is
  `clamp(280px, 72dvh, 360px)`. Graph/lane bounds remain camera input only.
  Automatic framing keeps the first graph bound 48px from the canvas top;
  manual Fit uses the same actual client box.
- Page vertical scroll remains available outside the graph. The contained graph
  owns explicit pan/pinch interaction, and a real pane click may clear the path;
  a completed drag does not become a pane click.
- Thirty-one controller/viewport/sheet/navigation tests exercise the required
  mobile transition matrix without depending only on source expressions.

### Semantic layout audit

All 11 views were reviewed. `web-cloudflare` is the only view with semantic
hints because it owns one unambiguous symmetric grid: Dashboard Sync and Stable
Release share a source rank; D1 and Cloudflare share the next rank; each pair
stays on its projection or release track; Worker is centered between the two
incoming tracks; and Explorer stays on Worker's presentation track. The same
rank/track/convergence contract drives LR and TB without stored coordinates.

The remaining ten views retain plain Dagre layout. Their linear paths, dense
dependency graph, feedback cycles, and control-plane sequences do not express a
single non-contradictory semantic grid, so adding hints would be decorative.
Unlisted nodes stay valid: the semantic engine derives bounded positions from
connected constrained neighbours and then Dagre, while at most eight global
spacing passes keep lane regions separate without independently moving a
hinted lane.

The checker and client reject unknown or duplicate membership, multiple rank or
track membership, occupied semantic cells, incomplete or contradictory
convergence, empty semantic mode, unsupported fields, and absolute coordinates.
Family-level tests prove deterministic LR/TB alignment, the Worker convergence,
finite bounds, lane separation, preserved routing, and a synthetic connected
node omitted from all hints.

- Initial Overview has no selected node or inspector and renders the full 11-node
  layout with six disclosed directed edges and arrow markers.
- Hover highlights direct neighbors. Selection highlights the transitive
  upstream/downstream path and dims unrelated nodes. Desktop opens the
  inspector; mobile keeps it optional behind View Details.
- Inspector starts with beginner questions, then collapsed architecture
  dimensions and compact Code/Test/Docs tabs. Its Chinese-primary questions
  keep summary, purpose, owner, and architecture ownership semantically separate.
- Node-owned subsystem links and breadcrumb history replace the generic drill action.
- Search selects and centers a node in its relevant graph; it never creates a card grid.
- Manifest-owned scenarios cover one Decision, Training-to-Decision,
  Cloudflare unavailable, and the exact-revision release path.
- Failure mode uses explicit `AFFECTED` and `CONTINUES` membership for Training,
  Cloudflare, Decision, Evidence Ledger, News, Dashboard Sync, D1, and Control
  Plane. Nodes without a contract expose a disabled control and explanation;
  the UI never classifies every non-neighbor as safe.
- Labelled lane regions sit behind the graph and do not intercept selection,
  pan, or zoom. Critical edge labels remain visible; background and optional
  labels appear for interaction, guidance, or sparse views while the text
  fallback always retains every label.
- One latest-intent camera controller exclusively owns automatic Fit, Focus,
  inspector-close refit, and manual Fit. It cancels pending frames and waits
  for React Flow node initialization plus stable measured canvas dimensions.
- Cross-view search and scenarios issue one final Focus rather than Fit then
  Focus. Small views permit a larger bounded fit zoom. Opening the inspector
  preserves zoom; desktop close waits for width transition completion before
  one refit, while mobile close preserves both path and viewport without a
  camera command.
- Mobile resolves the breakpoint before React Flow mounts, so the first graph
  is TB and performs one Fit without exposing an intermediate LR layout.
- Keyboard nodes support Enter, Space, arrows, Escape, visible focus, and an
  `aria-live` selected-path announcement. The relationship text equivalent is
  secondary and collapsible. Reduced motion disables guided edge animation.

## Bundle boundary

Measured from the production client build with maximum gzip compression:

| Artifact | Raw bytes | Gzip bytes | Boundary |
|---|---:|---:|---|
| Lazy Explorer JS | 337,344 | 100,769 | Private lazy chunk only |
| Lazy Explorer CSS | 38,913 | 7,524 | Private lazy chunk only |
| Public `DashboardApp` JS | 29,177 | 10,108 | Public initial path |
| Public shared `index` JS | 217,752 | 58,246 | Public initial path |
| Public initial CSS | 199,939 | 34,370 | Public initial path |

The public JS raw sizes are unchanged from the rejected #304 build boundary;
the graph packages occur only in `ArchitectureExplorerView-*.js`. The scoped
Explorer stylesheet replaced and removed the previous feature block from
`globals.css`, so graph CSS is no longer in the public stylesheet. The public
initial gzip regression is therefore below the 2 KiB ceiling.

## Local responsive QA

| Viewport | Graph | Inspector | Overflow | Targets |
|---|---|---|---|---|
| 1440x900 | LR Overview, full 11-node layout / 6 disclosed edges, arrows, labels, MiniMap; Decision selection keeps zoom unchanged | Closed initially; 380px drawer after selection | none | at least 44px |
| 390x844 | 574px viewport-derived TB canvas; 72px top distance; 168px node floor; first tap exposes eight-edge Decision path and dock | Controlled 72dvh sheet; close preserves all eight highlighted edges | none | at least 44px |
| 360x800 | 544px viewport-derived TB canvas with compact two-row toolbar and internal horizontal pan | Controlled sheet; Advanced and Inspector mutually exclusive | none | at least 44px |
| 375x812 | 552px viewport-derived TB canvas with the same readability floor | Complete tap/path/sheet/scenario/search/Fit flow passed | none | at least 44px |
| 320x568 | 480px bounded stress canvas; first graph bound remains 72px from top | Full usable bounded sheet with visible sticky close | none | at least 44px |
| 393x852 | 579px viewport-derived TB canvas with the same readability floor | Complete tap/path/sheet/scenario/search/Fit flow passed | none | at least 44px |
| 430x932 | 634px viewport-derived TB canvas with the same readability floor | Complete tap/path/sheet/scenario/search/Fit flow passed | none | at least 44px |
| 800x360 | 280px short-landscape canvas; internal graph drag retained 168px nodes | Full-height landscape sheets remain closable | none | at least 44px |
| 844x390 | 281px short-landscape canvas and 65px single-row toolbar | Full-height landscape sheet remains closable | none | at least 44px |

Browser checks exercised Decision selection/dimming, inspector close,
subsystem membership and breadcrumbs, Training's three-lane path, package
initial/selected/show-all disclosure, mobile beginner navigation, relationship
fallback, responsive direction, exact 168px mobile floor, and unchanged zoom
during Decision disclosure. The active local QA tab closed and its viewport
override was reset.
Local status/session requests correctly fail without Cloudflare bindings and
are not Explorer requests.

## Exact immutable Preview QA

Cloudflare version `e4245cc6-3f15-4320-8110-f3b9ef37a537` at
`https://e4245cc6-aurum-signal-room-preview.yiyousiow1234.workers.dev/admin/architecture`
exposed exact build `884f1223`. All eight mobile viewports in the table passed
the full affected flow. At 390x844 the dock/Inspector/Advanced heights were
88/608/574px; at 360x800 they were 88/576/544px. Inspector open and close each
retained eight path edges. Explore Advanced listed only four advanced/campaign
destinations, while Reference exposed all views and reference controls.
Screenshots are stored under
`docs/audits/screenshots/architecture-explorer-884f122/`.

The immutable-Preview tab closed and the viewport override was reset. The
browser still listed one earlier localhost error interstitial whose protected
`data:` URL prevented automated close; it remains turn-scoped. The known
shared Admin-shell React hydration #418 remains recorded and was not expanded
into this PR.

## Final local validation

- Architecture manifest: 37 nodes, 66 edges, 11 views, 4 scenarios, 52,331 bytes.
- Architecture manifest contracts: 21 passed.
- Explorer behavior/geometry/camera contracts: 103 passed, including 31 new
  mobile state, viewport, sheet, and navigation behaviors.
- Complete platform-neutral Python suite: 1,414 passed.
- Complete Web suite: 359 total; 353 passed and 6 intentionally skipped.
- Windows runtime contracts: 335 passed.
- Architecture TypeScript check and Web lint: passed.
