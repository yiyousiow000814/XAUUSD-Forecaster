# Private Architecture Explorer Audit — 2026-08-24

## Outcome

The first Explorer renderer was rejected because it presented a two-column
card catalog and permanent detail wall rather than an architecture graph. The
bounded manifest, build-time injection, private route, security boundary, and
exact-SHA source links were retained. The renderer is now a read-only React
Flow node-link graph with Dagre layout and visible directed connectors.

## Architecture gate

```text
Owner: Architecture manifest and private Explorer renderer
Authoritative state/store: Git-tracked architecture/manifest.json
Execution boundary: Bounded build-time loader to the lazy Admin React Flow/Dagre client chunk
Critical or optional: Optional private static presentation
Maximum work per operation: One manifest no larger than 65,536 bytes; only the selected view renders
Incremental cursor/revision/checkpoint: Manifest schema v2 and immutable build SHA
Failure domain: Manifest validation and the private Explorer chunk only
Last-good/recovery behavior: Invalid manifests fail the build; revert the static change with no data migration
Architecture documents affected: architecture/README.md, WEB_AND_CLOUDFLARE.md, CODEBASE_MAP.md, this audit
```

There is no Architecture API, D1 table, Worker route, GitHub runtime request,
Markdown parser, Windows process, background thread, or production mutation.

## Manifest v2 inventory

- Schema: `architecture-explorer-v2`
- Views: 11
- Nodes: 28
- Edges: 38
- Guided scenarios: 4
- Explicit failure-impact definitions: 2
- Serialized bytes: 50,891 of the fixed 65,536-byte limit
- Edge IDs, endpoints, labels, kinds, criticalities, and descriptions are explicit.
- View edge membership, visible endpoints, continuous primary paths, lane
  membership, scenario continuity, and failure references fail closed.
- Coordinates are not stored; Dagre calculates finite positions per selected view.

## Interaction and presentation evidence

- Initial Overview has no selected node or inspector and renders 11 nodes with
  11 visible directed edges and arrow markers.
- Hover highlights direct neighbors. Selection highlights the transitive
  upstream/downstream path, dims unrelated nodes, and opens a closable inspector.
- Inspector starts with beginner questions, then collapsed architecture
  dimensions and compact Code/Test/Docs tabs.
- Node-owned subsystem links and breadcrumb history replace the generic drill action.
- Search selects and centers a node in its relevant graph; it never creates a card grid.
- Manifest-owned scenarios cover one Decision, Training-to-Decision,
  Cloudflare unavailable, and the exact-revision release path.
- Failure mode uses explicit `AFFECTED` and `CONTINUES` membership. It does not
  classify every non-neighbor as safe.
- Keyboard nodes support Enter, Space, arrows, Escape, visible focus, and an
  `aria-live` selected-path announcement. The relationship text equivalent is
  secondary and collapsible. Reduced motion disables guided edge animation.

## Bundle boundary

Measured from the production client build with maximum gzip compression:

| Artifact | Raw bytes | Gzip bytes | Boundary |
|---|---:|---:|---|
| Lazy Explorer JS | 294,225 | 89,990 | Private lazy chunk only |
| Lazy Explorer CSS | 30,690 | 6,119 | Private lazy chunk only |
| Public `DashboardApp` JS | 29,177 | 10,110 | Public initial path |
| Public shared `index` JS | 217,752 | 58,249 | Public initial path |
| Public initial CSS | 199,919 | 34,359 | Public initial path |

The public JS raw sizes are unchanged from the rejected #304 build boundary;
the graph packages occur only in `ArchitectureExplorerView-*.js`. The scoped
Explorer stylesheet replaced and removed the previous feature block from
`globals.css`, so graph CSS is no longer in the public stylesheet. The public
initial gzip regression is therefore below the 2 KiB ceiling.

## Local responsive QA

| Viewport | Graph | Inspector | Overflow | Targets |
|---|---|---|---|---|
| 1440x900 | LR Overview, 11 nodes / 11 edges, arrows, labels, MiniMap | Closed initially; 380px drawer after selection | none | at least 44px |
| 390x844 | TB Dagre graph, pan/pinch/Fit, no MiniMap | 58% bottom sheet | none | at least 44px |
| 360x800 | TB Dagre graph and nine-step Decision guide | bottom sheet | none | zero visible targets below 44px |

Browser checks exercised Decision selection/dimming, inspector close,
subsystem membership, guided next-step navigation, Training nodes, explicit
Cloudflare failure state, relationship fallback, and responsive direction.
Local status/session requests correctly fail without Cloudflare bindings and
are not Explorer requests. Exact deployed Preview evidence is recorded in the
pull request after the immutable branch build completes.
