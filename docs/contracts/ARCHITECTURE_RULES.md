# Architecture Rules Contract

## Purpose

These rules keep production work bounded and make ownership visible. They
apply to runtime services, release control, Cloudflare projections, and local
evidence. Product and evidence semantics remain governed by their dedicated
contracts.

## Ownership and authority

1. Every mutable state has one authoritative owner and one authoritative
   store. Mirrors, caches, projections, receipts, and indexes must identify
   their source authority and must not silently become a replacement owner.
2. Local SQLite remains the forecasting evidence authority. Cloudflare D1 is
   a public projection and separate retained Assistant authority; it is not a
   forecasting recovery source.
3. Process launchers and scripts compose configuration, processes, and owner
   APIs. Reusable domain logic belongs under `xauusd_forecaster/`. Package code
   must not import from `scripts/`.
4. A compatibility shim may contain no domain logic or mutable state and must
   have an explicit removal condition.

## Critical paths and bounded work

1. Critical work and optional work have separate budgets and failure domains.
   A failed optional projection, model rebuild, provider call, or display
   resource must not block an otherwise valid decision append.
2. Every recurring or request-driven owner must bound its work independently
   by items, bytes, time, or an authoritative finite window. A display limit
   is not proof that upstream serialization or querying is bounded.
3. Growing histories use a cursor, revision, generation, partition, checkpoint,
   or exact requested window. Normal work must not rescan all retained history
   when only a bounded delta is required.
4. Recovery is bounded by total work and required confidence. It must not rely
   on an unsupported assumption that only one external category can fail.

## Failure, recovery, and evidence

1. Mutation boundaries fail closed on unknown identity, ownership, authority,
   or provenance. Existing last-good derived state remains available when a
   replacement cannot be proven valid.
2. Retry starts from the narrow failed owner and preserves independently valid
   work. It must not replay a complete workflow when an exact failed stage can
   resume safely.
3. Release qualification is keyed by behavior-affecting identity. Freshness is
   a renewable live fact and is separate from immutable or reusable semantic
   qualification.
4. Release evidence nodes declare their owner, behavior inputs, dependencies,
   receipt digest, and invalidation reason. Git movement alone never changes
   Stable.
5. Observability and receipts expose failure; they do not redefine a failed
   workflow as success.

## Dependency direction

```text
entrypoint / process launcher
        -> package owner API
        -> domain contracts and authoritative stores

browser -> Worker route -> D1 projection
                         X local forecast authority

release integration -> abstract qualification receipt
                    X provider event implementation details
```

Cross-owner imports must follow the documented source-of-truth direction. A
new import cycle requires an explicit contract change and a smaller shared
abstraction; moving files without removing the cycle is not an architecture
correction.

### Dashboard resource serialization

`xauusd_forecaster/dashboard/resource_contracts.py` owns pure, bounded
Critical, Audit, Learning, Market chart, and News batch serialization. API
and Sync consume that owner; it reuses the field projections in
`xauusd_forecaster/dashboard_payloads.py` and the News generation contract in
`xauusd_forecaster/news_projection.py`. It accepts already-read source values
and an explicit optional producer revision. It must not discover a runtime
root, query Git or a database, perform HTTP, advance an ACK, or schedule work.
Field selection, input validity and transport limits remain governed by
[Hosting boundaries](HOSTING_BOUNDARIES.md), not by the location of the code.

The explicit serializer re-exports in `scripts/run_dashboard_sync.py` preserve
existing build and runtime callers with the same function and exception
objects; they introduce no second implementation or mutable state. Remove
compatibility-only aliases after every external importer (including immutable
Preview and release fixture builders) has moved to the package owner and the caller
compatibility tests have been updated. Internal Sync calls retain the imports
they use. Sync still owns orchestration, source I/O, producer-revision
discovery, retry and checkpoint state. Moving pure
serialization does not qualify the remaining entrypoint logic as extracted.

### Executable Python import policy

`scripts/check_architecture_imports.py` shares the current compiler's Python
AST parser. `architecture/critical-paths.json` declares its canonical package
namespaces, exact script-composition edges and any legacy shims. The existing
Current source architecture CI check executes the policy on all package Python
files and immediate Python scripts, independently of the selected graph slices.
It does not import application modules or infer runtime loading from syntax.

- Package source must not import `scripts`, including relative and aliased
  spelling of that namespace. Script shared-library dependencies need a precise
  source/target declaration, reason and removal condition. Developer compiler
  composition and temporary production compatibility are distinct reasons,
  not permission for arbitrary new script dependencies.
- Package initializers and declared whole-file shims contain only docstrings,
  explicit imports and literal string-list/tuple `__all__`. They must not
  perform inline calls, schema installation, client creation or thread startup.
  This declaration-only rule does not prove transitive imports have no effects.
- Within explicitly declared canonical package namespaces, non-Dashboard
  packages may not import Dashboard; Decision may not import Assistant or Web;
  canonical source may not import a declared legacy shim. Dashboard is the
  terminal read/projection namespace. These are declared syntax boundaries,
  not inferred process, timing or mutation authority.
- Flat modules remain unclassified by this package-direction rule. For example,
  a flat file named `decision.py` is not silently treated as an implemented
  Decision package. The package-to-script ban still applies to every flat file.
  Unpopulated declared namespaces do not count as completed owner extraction.
- A shim declaration requires an existing source and distinct existing owner
  plus an explicit removal condition. The current whole-file shim list is
  empty. Neither the root public facade nor the mixed Sync orchestrator is a
  whole-file shim merely because it exposes compatibility imports.

All runtime module resolution remains UNKNOWN. Literal dynamic module requests
are reported with their known requested spelling, separately from unknown
expressions; neither proves the actual callee, loaded file or revision.
`check_deferred_projection_parity.py` explicitly selects another producer root,
so its declared module request never becomes current-checkout source identity.
A passing syntax check is not a complete dynamic-import or runtime-safety proof.
Malformed policy, missing declared files and Python parse errors fail the check.
Current source-index coverage, UNKNOWN edges and transport budgets are unchanged.

## Safety boundaries

- The product is Shadow research only and has no order-submission authority.
- Assistant chat, Q&A, title generation, compaction, and indexing remain
  PAUSED until a separately authorized activation contract passes.
- Stable changes only through explicit Release Control Promote and observation.
- Unknown storage is never deleted automatically.
- Performance work must preserve point-in-time causality, append-only evidence,
  model behavior, rollback authority, and fail-closed validation.

See also [Hosting boundaries](HOSTING_BOUNDARIES.md),
[Local storage lifecycle](LOCAL_STORAGE_LIFECYCLE.md), and
[Release control](RELEASE_CONTROL.md).
