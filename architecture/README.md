# Architecture Explorer Manifest

`manifest.json` is the single machine-readable source used by the private
Architecture Explorer. Detailed Markdown contracts remain authoritative for
full rules and explanations.

## Architecture gate

```text
Owner: Architecture manifest and private Admin presentation
Authoritative state/store: Git-tracked architecture/manifest.json
Execution boundary: Build-time validation and lazy React view
Critical or optional: Optional private static surface
Maximum work per operation: One manifest no larger than 65,536 serialized bytes
Incremental cursor/revision/checkpoint: Manifest schema and immutable build SHA
Failure domain: Build/validation and private Explorer chunk only
Last-good/recovery behavior: Malformed manifests fail the build; public UI remains independent
Architecture documents affected: Architecture README, Codebase Map, Web and Cloudflare design, AGENTS.md
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

Run `python scripts/check_architecture_manifest.py --root .` before committing.
