# Generated Architecture Evidence

The private Architecture Explorer consumes deterministic artifacts under
`architecture/generated/`. Those files are compiled from repository source,
small human-owned semantic declarations under `architecture/declarations/`,
and executable contracts under `architecture/contracts/`; they must never be
edited directly. Detailed Markdown contracts remain authoritative for full
rules and explanations.

## Architecture gate

```text
Owner: Offline architecture compiler and private Admin presentation
Authoritative state/store: Repository source plus architecture/declarations and architecture/contracts; generated JSON is derived
Execution boundary: Bounded local/CI compilation and build-time loader to the lazy Admin React Flow/Dagre chunk
Critical or optional: Optional private static surface
Maximum work per operation: One manifest no larger than 65,536 serialized bytes
Incremental cursor/revision/checkpoint: Stable source digest, artifact schema, and immutable build SHA
Failure domain: Build/validation and private Explorer chunk only; no runtime API or store
Last-good/recovery behavior: Stale or malformed output fails closed; regenerate from source or revert the compiler/declaration change
Architecture documents affected: Architecture README, Codebase Map, Web and Cloudflare design, Explorer audit
```

## Maintenance contract

- Run `python scripts/compile_architecture.py --root .` after an architecture-
  affecting source or declaration change, then review the generated diff.
- `--check` must be byte-clean. Manual edits to generated files are rejected.
- Source extraction automatically inventories ordinary code structure. Owner,
  authority, criticality, failure behavior, learner copy, and view taxonomy
  remain explicit semantic declarations and must not be inferred from imports.
- Keep `runtime_state` separate from `implementation_state`. A pending PR is
  `PENDING` even when its implementation exists on a branch.
- Use repository-relative paths. Code paths must not point into `tests/`, and
  test paths must remain under `tests/` or `web/tests/`.
- The UI receives the compact graph through the Vite build constant. Code and
  evidence indexes are separate bounded virtual modules and load only inside
  the private Explorer. It must not fetch GitHub, parse Markdown, call an
  Architecture API, or read production D1 at runtime.
- Detailed invariants belong in `docs/contracts/` or the relevant design map;
  the manifest provides concise navigation, not a duplicate contract system.

## Graph contract v2

Schema `architecture-explorer-v2` makes graph relationships explicit. Every
edge has a stable ID, source, target, visible label, semantic kind,
criticality, and concise description. Every view owns its visible node and edge
sets, layout direction, entry node, continuous primary path, and complete lane
membership. Guided scenarios own ordered continuous paths; failure exploration
uses explicit `affected` and `continues` membership and never infers safety from
graph distance.

Every node owns a non-empty beginner-facing `purpose` distinct from `summary`,
`owner`, and the six architecture dimensions. The inspector presents those
facts separately: what the node is, why it exists, and who owns it.

Each view also owns navigation and progressive-disclosure metadata. Navigation
classifies the view as `OVERVIEW`, `SUBSYSTEM`, `ADVANCED`, or `CAMPAIGN`, names
its `BEGINNER` or `ADVANCED` audience, and links non-overview views back to the
System Overview. There is exactly one beginner Overview. Explore mode starts
there and reaches subsystem graphs through node-owned drill-down; Reference
mode exposes the same manifest as a complete direct-access catalogue.

Disclosure modes are `PRIMARY_PATH`, `VIEW_RELATIONSHIPS`, `SELECTED_NODE`, and
`SELECTED_PACKAGE`. A view declares always-visible and secondary edge IDs plus
whether an explicit Show All action is allowed. The engine always lays out the
complete view, assigns ports, and routes every edge before disclosure. Changing
selection or disclosure filters only rendered edges and matching ports; it
must never run Dagre again, move a node, reassign an anchor, trigger Fit, or
change zoom. Missing future UI specialization falls back to the manifest view,
while unlisted semantic nodes retain Dagre auto-placement.

The generated Explorer representation keeps explicit `node_fields` and `edge_fields`
beside compact rows. The bounded build loader restores named node and edge
objects before client validation. View node membership is derived from its
complete lane membership, and scenario node/edge paths are derived from the
ordered steps plus the unique edge graph. Node inputs and outputs are likewise
derived from edges instead of being serialized a second time. These are
lossless storage reductions; the client still receives and validates the full
v2 graph contract.

`Canonical Package Dependencies` is a compile-time import graph derived from
`docs/contracts/PACKAGE_DEPENDENCIES.md`. Its nine `package-*` nodes and
`DEPENDENCY` edges are separate from operational runtime/data nodes. The view
states that `A → B` means A may import or depend on B and lists prohibited
reverse directions without drawing them as valid edges.
Its Explore default is selection-first: nine package nodes and no edges. A
selected package reveals only its incoming and outgoing dependency edges; Show
All Dependencies and Reference mode expose the complete manifest edge set. The
text dependency list is derived from those same edges.

Lane membership renders as non-interactive labelled swimlane regions. Desktop
uses LR layout; narrow screens use TB layout with a bounded readable initial
viewport and horizontal canvas panning. Critical labels remain visible, while
background and optional labels expand on interaction or in a sparse view.
Failure controls are available only for nodes with explicit `AFFECTED` and
`CONTINUES` contracts.

Mobile interaction state has one reducer-owned contract. `activePathNodeId`
owns graph selection, relationship disclosure, path highlighting, and the
compact selected-node action dock. `inspectorNodeId`, `inspectorOpen`, and
`advancedOpen` are normalized through the mutually exclusive `mobilePanel`.
Opening or closing a controlled Inspector or Advanced sheet never clears the
active graph path. Only Clear Path, a real blank-canvas click, or an incompatible
view/mode boundary clears it. Search selects a path without forcing details;
scenario highlighting remains independently owned.

The visible mobile React Flow canvas is sized from `visualViewport` (falling
back to the window viewport), not graph bounds: portrait uses a bounded 68% of
the visible height with a 480–720px range, while short landscape uses a bounded
72% with a 280–360px range. Graph and lane bounds feed camera calculations
only. The page owns vertical scrolling; React Flow owns explicit graph pan and
pinch interaction, with horizontal overflow contained by the stage. The
Inspector and Advanced sheets lock and exactly restore page scroll, trap focus,
restore their invoker, and include safe-area padding.

### Optional semantic layout

Dagre remains the deterministic default. A view may additionally declare
`layout_hints` with mode `SEMANTIC_GRID` when its architecture has a meaningful
rank, track, or convergence relationship that generic topology cannot express.
The same contract is orientation-independent: ranks become columns in LR and
rows in TB; tracks become rows in LR and columns in TB. A convergence centers
its target track between the declared source tracks on the cross axis.

Semantic hints describe relationships, never pixels. Absolute `x`, `y`,
position, or coordinate fields are invalid. Every declared node must belong to
the view; a node cannot belong to multiple rank or track groups; two nodes
cannot occupy the same declared rank/track cell; and convergence membership
must be complete and unambiguous. Unlisted nodes are placed automatically from
their connected neighbours and the Dagre fallback. The engine performs at most
eight deterministic global spacing passes to keep nodes and lane boxes apart;
it never shifts one hinted lane independently and thereby breaks alignment.

The compact checked-in form stores `[mode, rank rows, track rows, convergence
rows, auto-place]`; the Python checker and build loader expand it to the named
contract before validation. Of the current 11 views, only `web-cloudflare`
needs semantic hints: its projection and release branches are symmetric and
converge on Worker. The other ten views remain Dagre-owned after audit because
their linear, dense, feedback, or control-plane structure has no equally clear
semantic grid. Do not add hints merely to decorate a view.

The client uses Dagre to calculate finite fallback positions from the selected
bounded view, then applies any validated semantic relationships. Coordinates
are not stored in the manifest. React Flow renders only that view's nodes and
edges as a read-only graph. Both libraries and their CSS remain inside the lazy
private Explorer boundary.

One camera-intent controller exclusively owns automatic Fit, node Focus,
desktop post-inspector refit, and manual Fit. Mobile Inspector close does not
refit because the active path and canvas width do not change. It waits for React Flow node
initialization, a stable measured canvas, and any inspector width transition.
Replacing an intent cancels the pending animation frame, so an older view can
never move the current viewport.

Run `python scripts/check_architecture_manifest.py --root .` before committing.

## Evidence and code drill-down

The high-level learner graph remains semantic. Its compact indicator states
only whether a declaration has a static match, is declaration-only, stale,
contradicted, or unresolved. It never calls a declaration "verified" merely
because the declaration exists. The Inspector's Evidence tab expands the full
chain: declaration key, source binding and exact line span, extractor rule,
contract and test IDs, current source digest, normalized runtime trace IDs, and
targeted mutation outcomes. Links require the immutable build SHA.

`Open code structure` is generated from `code-index.json`. It drills from the
selected semantic node into matching repository modules and then extracted
top-level symbols; ordinary file or symbol changes therefore update the tree
without a manually maintained child view. This code containment tree is not a
replacement for the learner architecture graph and never infers ownership.

The package dependency reference keeps three modes separate: observed imports,
allowed policy (including unused permissions), and violations. Test
effectiveness likewise separates tests that merely touch code from tests bound
to a durable contract, and it keeps every surviving designated mutation
visible. Raw test count is inventory, not a trust score. Architecture diff is
shown as `UNAVAILABLE` when the build lacks exact base metadata.
