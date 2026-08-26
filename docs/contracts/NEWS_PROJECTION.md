# News Projection Contract

## Authority and scope

- Forecasting SQLite is the authoritative news store. D1 is a derived public
  projection and MUST NOT become an independent news authority.
- One source snapshot fixes a 60-day `window_start`, a `watermark`, the
  projection contract, expected index and detail counts, withdrawal count, and
  deterministic source and receipt digests.
- Receipt payload hashes cover a canonical JSON value encoding shared by the
  Python producer and Worker consumer. Object keys sort by UTF-8 bytes, arrays
  retain order, strings include their UTF-8 byte length, and JSON numbers use
  normalized IEEE-754 binary64 bytes (`0`, `0.0`, and `-0.0` are equivalent).
  Runtime-specific JSON text formatting MUST NOT affect a receipt.
- Local source manifest and batch reads are loopback operator-bridge endpoints;
  they require the bridge credential and are not browser or public APIs.
- A source snapshot is immutable while it is being replayed. Source changes
  create a replacement generation; they never alter an in-flight generation.
- The local producer persists the accepted frozen generation atomically before
  exposing it for replay. Process or machine restart restores that exact
  snapshot and its batches; a newer source snapshot is discovered only after
  the frozen snapshot has reached `CURRENT`. Ordinary source advancement never
  authorizes abandonment of a healthy in-flight generation.

## Generation lifecycle

- The lifecycle is `prepare -> details -> index -> reconcile -> validate ->
  CURRENT`. Details MUST be complete before any index batch is accepted.
- Index membership belongs to one generation. Detail bodies use their immutable,
  content-addressed `detail_key` identity in the global derived detail store.
  The key binds both source revision identity and the exact detail payload hash,
  so a later annotation or impact update under the same source revision stages a
  new detail without mutating the detail visible to CURRENT. Replacement
  generations reuse exact existing evidence instead of copying it.
  A repeated key with a different hash or payload is a contradiction and MUST
  fail before either evidence or batch progress changes. Readers MUST select
  index membership only from the active generation and resolve every identity
  to exactly that immutable detail; they MUST NOT combine index generations.
  Detail and index batches independently advance an append-only identity digest
  over their ordered `detail_key` values. Activation requires those final
  digests to match, so an identity left over in the global store cannot satisfy
  a different generation's detail membership proof.
- Activation is one D1 transaction. The prior verified `CURRENT` generation
  remains readable until the replacement has exact counts, no missing detail,
  no review-state or active-cluster invariant violation, and a receipt digest
  equal to its source manifest. The same transaction materializes the complete
  review-state, category, and parsed-count summary for that exact generation;
  every future generation repeats this step, so a one-time migration backfill
  is not activation authority.
- The legacy Reverse-Stable index is also replaced only inside that activation
  transaction. STAGING index batches cannot change its active identity set.
  Superseded legacy rows and global derived details with no retained generation
  reference are deleted when their superseded generation is cleaned; the local
  SQLite authority, not D1, retains historical news evidence.
- `CURRENT`, `RECOVERY_REQUIRED`, `REPLAYING`, `VERIFYING`, and `DEGRADED` are
  user-visible truth states. Only a receipt-matched, verified `CURRENT`
  generation may claim a complete 60-day total.

## Bounds and retention

- A generation contains at most 10,000 index rows and 10,000 detail rows.
  Withdrawal identities are counted within the same 10,000-source-row bound.
- One write batch contains at most 20 rows. The local producer additionally
  enforces 400,000 bytes for detail and index batches, below each 450,000-byte
  Worker request bound.
  One sync cycle advances at most four generation batches.
- D1 retains at most one `CURRENT`, one replacement `STAGING`, and one
  short-lived `SUPERSEDED` generation. A new prepare removes older superseded
  data. Staging expires after 24 hours and may be abandoned only by exact
  generation identity; `CURRENT` cannot be abandoned.
- Global content-addressed details are bounded by those retained generations
  and the active Reverse-Stable set. They are not an append-forever archive.
- Health reads use generation metadata, counts, progress, and digests. Routine
  health MUST NOT scan or deserialize all news bodies. An explicit verification
  step may perform bounded indexed count and relationship checks before or
  after activation.
- Public index pagination reads materialized immutable totals and indexed page
  rows. It MUST NOT rescan the complete CURRENT generation for filtered totals,
  category buckets, or review buckets on every visitor request. The only
  time-dependent aggregate is the unexpired model-candidate count. Activation
  stores its canonical fixed-width expiry timestamps in sorted order and the
  Worker obtains the active count by binary search, without scanning D1 rows.

## Retry and recovery

- Batch offsets and receipts are idempotent. Replaying an accepted exact batch
  succeeds; changing an accepted batch is a receipt contradiction and fails
  closed. The append-only batch receipt is the staging progress record; a
  second mutable generation-progress row is not rewritten for every batch.
  Replaying an unchanged detail performs no detail mutation; only its new
  generation's append-only batch receipt is written. A genuinely new detail is
  appended once and can become readable only when a receipt-complete index
  generation activates.
  Activation copies the final receipt-backed offsets into immutable generation
  evidence in the same transaction that makes the generation CURRENT.
- A retry resumes the remote detail and index offsets and preserves the prior
  `CURRENT` generation. It MUST NOT restart an accepted stage blindly.
- Building the frozen local source universe runs outside the HTTP request path.
  Before the first source exists, manifest reads return `REPLAYING` with a retry
  interval. Later refreshes keep returning the frozen source that backs
  `CURRENT` until its replacement is ready; neither path holds the request open.
- Manifest mismatch, receipt contradiction, missing detail, partial activation,
  stale staging, and source-snapshot mismatch require an explicit recovery
  state. An orphan staging generation may be removed using its rejection
  identity before a new prepare; active data is never deleted by that repair.

## Release evidence

- Release acceptance binds the exact source snapshot, generation, source
  digest, receipt digest, counts, contract version, Candidate code identity,
  and D1 capability.
- Preview and production reads share this contract, but Preview writes remain
  prohibited by [`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md). Stable activation
  remains governed by [`RELEASE_CONTROL.md`](RELEASE_CONTROL.md).
