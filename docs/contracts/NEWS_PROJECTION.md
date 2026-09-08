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
- Index membership belongs to one generation and is retained in its append-only
  batch receipts rather than copied into a generation-keyed indexed table.
  Receipt payloads remain bounded and the final identity and payload digest
  chains prove the complete ordered membership. Detail bodies use their immutable,
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
- Derived impact event, availability, and expiry clocks cross the projection
  boundary as canonical UTC timestamps with microsecond precision. A source
  timestamp may carry another explicit offset, but the materialized projection
  must preserve the instant while normalizing its representation to `+00:00`.
- Activation is one D1 transaction. The prior verified `CURRENT` generation
  remains readable until the replacement has exact counts, no missing detail,
  no review-state or active-cluster invariant violation, and a receipt digest
  equal to its source manifest. The same transaction materializes the complete
  review-state, category, and parsed-count summary for that exact generation;
  every future generation repeats this step, so a one-time migration backfill
  is not activation authority.
- The legacy Reverse-Stable index is also replaced only inside that activation
  transaction. Activation applies only genuinely new, changed, or removed rows;
  an unchanged row preserves its prior `received_at` and causes no index
  mutation. STAGING index batches cannot change its active identity set.
  Superseded legacy rows and global derived details with no retained generation
  reference are deleted when their superseded generation is cleaned; the local
  SQLite authority, not D1, retains historical news evidence.
- `CURRENT`, `RECOVERY_REQUIRED`, `REPLAYING`, `VERIFYING`, and `DEGRADED` are
  user-visible truth states. Only a receipt-matched, verified `CURRENT`
  generation may claim a complete selected-window total.
  While a replacement is `REPLAYING`, its verified serving generation remains
  complete and may display its own totals, explicitly labelled with the serving
  activation time. These are not replacement totals or a freshness guarantee.
  Replacement progress comes from indexed acknowledged receipt offsets and is
  informational only; it cannot authorize activation or replace strict ACK.

## Bounds and retention

- The recurring reader source selects the latest 10,000 eligible candidate
  identities within 60 days, ordered by publication time (first receipt when
  absent), first receipt, source, source item identity, and revision descending.
  Relevance withdrawals are applied within that selected window. New arrivals
  displace its oldest members without deleting authoritative local records.
  Exceeding the window is normal growth, not a source-generation failure.
  Public totals describe the selected generation, not the entire local archive.
  Changed-key capture cursors retain their independent ascending change order.
- A generation contains at most 10,000 index rows and 10,000 detail rows, plus
  at most 10,000 unique withdrawal identities. Raw source processing is not
  materialized D1 membership: withdrawals contribute to the exact source digest
  and manifest count, not a second set of staged rows. Source capture has its
  independent byte, metadata and per-step work bounds below. This does not
  increase Worker batch limits or establish D1 storage/Free-plan acceptance.
- One detail batch contains at most eight items and 400,000 serialized bytes.
  One index batch contains at most four items and 100,000 serialized bytes; its
  Worker envelope is capped at 120,000 bytes.
  One sync work slice advances at most four generation batches, then yields
  to other due resources. An ACKed incomplete replay is immediately eligible
  again; the 60-second discovery cadence applies only after completion.
  Failed attempts retain the existing bounded backoff. This slice is not an
  upload rate limit and must not introduce idle time while replay is pending.
- D1 retains at most one `CURRENT`, one replacement `STAGING`, and one
  short-lived `SUPERSEDED` receipt generation. A new prepare removes older
  superseded receipts and obsolete v3 staging rows. Staging expires after 24
  hours and may be abandoned only by exact generation identity; `CURRENT`
  cannot be abandoned.
- Global content-addressed details are bounded by those retained generations
  and the active Reverse-Stable set. They are not an append-forever archive.
- Health reads use generation metadata, counts, progress, and digests. Routine
  health MUST NOT scan or deserialize all news bodies. An explicit verification
  step may perform bounded indexed count and relationship checks before or
  after activation.
- Public index pagination reads materialized immutable totals and indexed active
  Reverse-Stable rows. It MUST NOT rescan complete CURRENT receipts for filtered totals,
  category buckets, or review buckets on every visitor request. The only
  time-dependent aggregate is the unexpired model-candidate count. Activation
  stores its canonical fixed-width expiry timestamps in sorted order and the
  Worker obtains the active count by binary search, without scanning D1 rows.

## Retry and recovery

- A bootstrap source capture is not a replay generation. Its bounded local
  artifact progresses from `BUILDING` to `SOURCE_COMPLETE` over one retained,
  checkpoint-complete SQLite input. Exact input lineage, source identity, epoch,
  60-day window and watermark are bound before progress; nonempty WAL is never
  silently excluded from that input. Ordinary API refresh does not acquire a
  full-database-copy responsibility from this bootstrap boundary.
- Capture uses at most 128 source identities per step, scopes the existing
  annotation claimability predicate to those identities, and steps the actual
  detail cursor without a full page `fetchall`. All SQLite work in one step
  shares a 15-second / 40-million-VM-operation refusal budget. Actual elapsed
  time and sampled memory are reported; this is not an OS memory sandbox or a
  claim that SQLite interruption is an exact wall-clock deadline.
- Each accepted immutable part contains at most 4 MiB of canonical content;
  total local capture content is bounded at 256 MiB and metadata at 32 MiB.
  The owned directory has a separate 324 MiB physical-byte ceiling: 256 MiB
  content plus two 32 MiB manifest copies and one 4 MiB incoming atomic part.
  Bounded name/stat accounting includes temporary files and unreferenced parts
  before reserving another write; interrupted output is not silently deleted.
  SQLite row length is limited to 2 MiB, its page cache to 8 MiB, and observed
  process memory above 512 MiB refuses source work. These are local capture
  safety limits, not larger remote generation limits or Free-plan evidence.
- Progress commits the last accepted raw cursor, including withdrawal
  identities, only after its part is atomically written and read back. A crash
  before manifest commit cannot advance progress; a crash after commit cannot
  roll it back. Failures retain the prior prefix and a 30-second backoff; later
  progress does not erase the recorded failure. Restart restores the same
  frozen input rather than reopening newer live data under its identity.
- A publication error reconciles the actually committed manifest before
  recording failure/backoff; it cannot overwrite a newly committed prefix with
  old state. If storage cannot persist even that decision, the result is
  non-retryable `NEWS_SOURCE_CAPTURE_STORAGE_UNRESOLVED`. The caller must stop
  automatic retries and repair the storage condition. An in-memory refusal is
  not claimed as a durable retry receipt when the storage itself is unwritable.
- Capture never overwrites the published schema-1 generation artifact.
  `SOURCE_COMPLETE` alone cannot be passed to Sync,
  remote prepare, qualification or CURRENT; global ordering, exact source and
  receipt digests, batch plans and applicable capacity gates are separate
  obligations. Original detail payload key order and its existing detail hash
  remain unchanged; recursive sorting applies only to the source digest.
- The existing owner may expose a retained replay view only after validating
  the complete plan, original/derived identity, distinct detail keys, equal
  index/detail counts, unique withdrawal capacity, exact manifest and receipt
  chain, and the caller's finite total batch budget. Old diagnostic admission
  labels remain historical facts, not current policy decisions. This read-only
  conversion never changes the v4 generation identity or invents an ACK. API and
  Sync share the same direct batch accessor; an ordinary inspection reader still
  has no replay manifest. Actual D1 peak storage, retained generations, cleanup,
  resource failure and final release gates remain separate prerequisites.
- Bootstrap pins an admitted retained view in the existing schema-1 generation
  envelope before replay. The envelope binds the exact version origin and
  projection contract; its payload binds the original capture identity, plan
  input digest, complete manifest and finite work budget. The capture location
  is derived as a fixed sibling, not accepted as an arbitrary stored path.
  Reopening revalidates the retained plan and the current caller's budget;
  a previous larger budget does not authorize a smaller caller to exceed it.
  Missing or contradictory pinned input requires explicit recovery, never a
  fallback to a newer source. Older materialized envelopes remain readable.
- Canonical planning verifies each retained part once, sorts only bounded
  detail-key/byte-offset metadata, and streams the original globally ordered
  detail then index batches. It reads exact records through at most eight file
  handles, not a whole-part cache refill for each shuffled key. Reported logical
  content reads are one complete part scan plus two exact item-record streams;
  they are not claimed as physical disk reads or D1 rows. Finalization has its
  own 15-second refusal budget and sampled memory ceiling. A durable attempt
  marker precedes retained-byte reads; interrupted/failed planning retains source
  parts and requires explicit recovery, rather than restarting the hash pass.
  Publication failure reconciles the committed plan and observes storage
  backoff even when no attempt was committed. No digest or plan is published
  until the entire calculation succeeds. Batch boundaries may
  cross capture parts and preserve the existing global receipt chain exactly.
- The same committed plan retains its sorted detail-key, original-ordinal,
  part, byte-offset, length and record-digest index. Detail and index batches
  share those locations; bodies are not copied into another artifact. Opening
  a reader validates bounded metadata once. Reading a later batch seeks only
  its at-most-eight or at-most-four records and verifies original record,
  detail identity, batch bytes and receipt hash; it must not rescan previous
  batches, complete parts, or source SQLite.
- A later derived planner records its actual producer identity separately from
  the original source-capture identity. It cannot relabel old input or old
  evidence as a new source run. An older plan without locations needs explicit
  retained-input enrichment, not silent source recapture. A plan reader alone
  is not an admitted generation and cannot authorize remote prepare.
- A proven query-only reader correction may continue an incomplete capture
  through one explicit derived capture schema. The original capture identity,
  fixed clocks, input lineage and accepted parts remain unchanged. The same
  manifest records the original prefix counters, cursor and descriptor digest,
  corrected execution identity, exact retained-page canonical proof, and
  independent semantic-review identity. A later clock/input supplement binds
  the original input bytes; it does not alter the earlier observed proof report.
  The atomic schema transition must make the actual unchanged old reader reject
  before querying or appending. New code verifies its executing reader owner,
  bootstrap and capture files against the active producer identity before source
  work. The current reader owner is `dashboard/news_resources.py`; relocating
  that owner does not reinterpret an existing API-bound reader-correction proof.
  API source identity readers recognize the retained `scripts/run_dashboard_api.py`
  locator and the classified `scripts/runtime/run_dashboard_api.py` locator.
  Exactly one may occur; ambiguous identities are rejected. This lookup never
  rewrites or rehashes stored identity bytes or grants a new producer authority.
  Partial captures retain their admitted producer or explicitly reviewed
  transition. Completed retained artifacts remain immutable evidence and do not
  grant a new producer permission to append source parts.
- Derived parts bind that single reader segment; all readers verify the original
  prefix and complete derived suffix. The identity proof hashes the original
  identity serialization in the validated capture manifest. A caller restored
  through another JSON envelope must match its identity values exactly, but
  cannot replace those proof bytes with its own object-key ordering. Altering
  the stored identity serialization still invalidates the original proof.
  A retry after suffix progress reuses the
  original transition, not a newly calculated current-prefix authority. A
  conflicting or second transition is refused. Publication failure follows the
  same atomic reconciliation and backoff rules. There is no automatic downgrade
  or relabeling; old published generation artifacts and production Stable remain
  untouched. Final planning includes the segment in its input digest while
  preserving the separate original and derived producer identities. Capture
  schema migration does not alter projection semantics or remote admission.

- Batch offsets and receipts are idempotent. Replaying an accepted exact batch
  succeeds; changing an accepted batch is a receipt contradiction and fails
  closed. The append-only batch receipt is the staging progress record; a
  second mutable generation-progress row is not rewritten for every batch.
  Replaying an unchanged detail or index performs no projection-row mutation;
  only its new generation's append-only batch receipt is written. A genuinely new detail is
  appended once and can become readable only when a receipt-complete index
  generation activates.
  Activation copies the final receipt-backed offsets into immutable generation
  evidence in the same transaction that makes the generation CURRENT.
- A retry resumes the remote detail and index offsets and preserves the prior
  `CURRENT` generation. It MUST NOT restart an accepted stage blindly.
- HTTP success alone cannot advance Sync state. Prepare requires exact
  generation identity and typed progress; every staged ACK must match the
  actual item count and cumulative canonical receipt. Activation and final
  health require exact identity, counts, digests and booleans without coercion.
  A malformed or contradictory response preserves the prior local checkpoint;
  the next bounded attempt reconciles the remote accepted prefix. Local state
  binds both target routes and the projection contract. A mismatched target
  requires explicit recovery; older unbound state obtains fresh remote facts
  before establishing that binding.
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
