# Architecture Explorer Manifest

`manifest.json` is the single machine-readable source used by the private
Architecture Explorer. Detailed Markdown contracts remain authoritative for
full rules and explanations.

## Architecture gate

```text
Owner: Architecture manifest and private Admin presentation
Authoritative state/store: Git-tracked architecture/manifest.json
Execution boundary: Bounded build-time loader to the lazy Admin React Flow/Dagre chunk
Critical or optional: Optional private static surface
Maximum work per operation: One manifest no larger than 65,536 serialized bytes
Incremental cursor/revision/checkpoint: Manifest schema and immutable build SHA
Failure domain: Build/validation and private Explorer chunk only; no runtime API or store
Last-good/recovery behavior: Malformed manifests fail the build; reverting the static chunk restores the prior viewer without data migration
Architecture documents affected: Architecture README, Codebase Map, Web and Cloudflare design, Explorer audit
```

## Maintenance contract

- Update the manifest and the relevant architecture document in the same PR
  whenever an owner, boundary, process, state/store, path, or dependency changes.
- Keep `runtime_state` separate from `implementation_state`. A pending PR is
  `PENDING` even when its implementation exists on a branch.
- Use repository-relative paths. Code paths must not point into `tests/`, and
  test paths must remain under `tests/` or `web/tests/`.
- The UI receives this file through the Vite build constant. It must not fetch
  GitHub, parse Markdown, or call an Architecture API at runtime.
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

The checked-in representation keeps explicit `node_fields` and `edge_fields`
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

Lane membership renders as non-interactive labelled swimlane regions. Desktop
uses Dagre layout; narrow screens use a bounded lane-first TB compaction so all
nodes fit without shrinking text below a useful size. Critical labels remain
visible, while background and optional labels expand on interaction or in a
sparse view. Failure controls are available only for nodes with explicit
`AFFECTED` and `CONTINUES` contracts.

The client uses Dagre to calculate finite positions from the selected bounded
view. Coordinates are not stored in the manifest. React Flow renders only that
view's nodes and edges as a read-only graph. Both libraries and their CSS remain
inside the lazy private Explorer boundary.

One camera-intent controller exclusively owns automatic Fit, node Focus,
post-inspector refit, and manual Fit. It waits for React Flow node
initialization, a stable measured canvas, and any inspector width transition.
Replacing an intent cancels the pending animation frame, so an older view can
never move the current viewport.

Run `python scripts/check_architecture_manifest.py --root .` before committing.
