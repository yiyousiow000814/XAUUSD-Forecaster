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
