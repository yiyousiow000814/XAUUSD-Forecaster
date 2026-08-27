# Hosting Boundaries Contract

## Public boundary

- Cloudflare Workers is the only deployment plane for this repository.
- Stable/Candidate ownership, coordinated Windows/Worker promotion, and reverse
  are governed by [`RELEASE_CONTROL.md`](RELEASE_CONTROL.md).
- GitHub is a source-control and validation plane only. Repository automation
  MUST NOT create GitHub Deployments or GitHub Environments, and pull requests
  MUST NOT request deployment to a GitHub environment.
- Public visitors read remote dashboard state and never connect to localhost.
- Public research API reads may be anonymous, but model-consuming Assistant
  routes are private under
  [`ASSISTANT_SECURITY.md`](ASSISTANT_SECURITY.md). Every ingest route requires
  the Worker secret `INGEST_TOKEN`.
- Local secrets remain outside Git and must not appear in public responses or
  logs.
- The ChatGPT Sites bypass header may be sent only to `*.chatgpt.site`; it must
  never be forwarded to Cloudflare or another target.

## Repository enforcement boundary

- `Repository policy` is the authoritative merge-time check for this hosting
  boundary. It reads a pull request with the checker from protected `main`; it
  MUST NOT execute code from the candidate branch.
- The check rejects GitHub Actions environments, `deployments: write`, and
  repository automation that invokes GitHub Deployments or Environments APIs.
  Cloudflare Workers builds, Git integration, and branch Previews remain
  allowed.
- The `Repository policy` check MUST be required by the `main` branch ruleset.
  With that external setting, a failing or missing check prevents acceptance
  of forbidden hosting architecture into protected `main`.
- Repository-local Actions are not a pre-execution firewall. GitHub may parse
  or begin another workflow from a pull request before this check completes.
  This contract guarantees merge-time repository enforcement, not universal
  prevention of every attempted workflow execution.

## Target isolation

- Each hosting target has independent synchronization state and health.
- Failure of one target must not stop synchronization to the other target.
- If both targets reject the heartbeat, synchronization must expose an error.
- Public-hosting failure must not stop local evidence collection.
- Optional growing resources, such as news details, must not mark an otherwise
  current live heartbeat offline.

## D1 capability readiness

- Deployed code declares named D1 capabilities as bounded sets of required
  schema objects. Runtime and deployment-readiness probes must fail with an
  explicit `D1_SCHEMA_CAPABILITY_MISSING` error and the missing capability and
  table names when production D1 does not satisfy that declaration.
- Capability checks do not execute migrations. Migration files and the
  reviewed Wrangler migration command remain the controlled schema authority.
- Capability names describe product behavior rather than a permanent migration
  number, so later additive migrations can extend or supersede the required
  object set without embedding one incident's filenames in runtime logic.

## Cross-boundary growth and critical paths

- Before data crosses a process, service, hosting, storage, synchronization, or
  API boundary, its owner and source of truth must be identified, the path must
  be classified as critical or optional, and its work and transport growth must
  be classified against accumulated state.
- Boundary correctness includes producer and consumer compatibility. Field and
  resource names, serialized shape, units, optionality, ordering, and deletion
  meaning must agree at the real production entry points. Producer validation
  alone cannot establish that the consumer preserves required behavior.
- A transport optimization must preserve required consumer semantics, not only
  remain within a byte or item limit. Partial, compact, delta, and incremental
  transports must define the authoritative complete baseline, delta-owned
  fields, merge and deletion behavior, stale and sequence handling, and
  reconnect/resync behavior. A compact update MUST NOT erase unrelated richer
  baseline state unless complete replacement is the explicit contract. The
  receiver must have a bounded path to recover every required complete field.
- Recurring cross-boundary work has exactly one explicit execution owner with a
  defined startup and supervision path, cadence, disabled/not-configured state,
  activation boundary, durable state, retry and failure isolation, rollback,
  shutdown, and process/machine restart recovery. A callable component or
  schedule definition without a supervised production invocation is not an
  execution owner.
- Monotonic identity, cursor, or sequence authority required by a transport must
  survive or reconcile across process restart, machine restart, and reconnect.
  Ambiguous ownership or stale acknowledgements fail closed rather than
  resetting authority or silently accepting an older update.
- Externally mutable readiness is distinct from deterministic contract failure.
  Where release eligibility depends on an external check, provisioning step, or
  service becoming available, the same immutable identity remains retryable and
  non-promotable until readiness succeeds. Release-specific state and promotion
  semantics are governed by [`RELEASE_CONTROL.md`](RELEASE_CONTROL.md).
- A path that communicates liveness, readiness, current authority, deployment,
  or control state must have bounded work and bounded transport independently
  of history, record, user, retry, or generation growth. Its representation is
  an explicit projection of current state and bounded summaries; newly added
  data does not enter that path merely because it appears in a shared source
  object.
- Growing state crosses the boundary through an independently bounded resource,
  such as a cursor page, byte-bounded batch, indexed D1 ledger, or lazy read.
  One operation must remain bounded as total authoritative state grows, and a
  complete source of truth must remain reachable without creating another
  full-history blob.
- News projection generation v3 keeps its index arrays within 100,000 serialized
  bytes and four items; its Worker envelope is capped at 120,000 bytes.
  News-evidence staging keeps each complete request within 80,000 serialized
  bytes and eight items. The Worker enforces the matching item limits and these
  route byte bounds; the larger platform ceiling is not a normal target.
- The retained legacy News tables are a Reverse-Stable projection, not another
  authority. Each bounded generation batch mirrors the same accepted rows into
  that projection in the D1 batch transaction. CURRENT activation marks
  legacy-only identities `SUPERSEDED_CONTRACT` in the same transaction that
  moves the generation pointer, so every completed activation restores exact
  active identity equality without replaying an unbounded serialized payload.
- Production-shaped News projection release validation remains a bounded,
  zero-mutation D1 JSON1 path. Each request crosses into D1 once and expands its
  item array once; all item counts and invariants are aggregated in that single
  scan so validation does not multiply serialized transport or batch parsing.
- Bounded dashboard snapshots cross the Worker-to-D1 boundary exactly once.
  Smaller snapshots retain the authoritative producer's exact valid UTF-8
  request bytes through D1 JSON1 validation and storage. Above the measured
  64,000-byte D1 transport boundary, where the ArrayBuffer bridge exceeds the
  Worker CPU headroom budget, the shared writer performs one strict UTF-8
  decode and binds the equivalent text; malformed UTF-8 fails closed. The
  transport boundary is independent from each route's larger business payload
  envelope. Every production entry point, including the minimal API router,
  uses this shared adaptive writer. Release dry-runs use the same transport
  selection and D1 JSON1 validation without mutating authoritative rows.
- Snapshot cleanup remains bounded per Worker request, reports whether cleanup
  debt remains, and the producer advances a fixed number of cleanup steps per
  cycle. While eligible cleanup debt remains, the producer must not admit
  another replacement snapshot. Immutable replacement therefore cannot turn
  into unbounded retained duplication even though each request is bounded.
- A producer may abandon only the staging generation recorded in its own
  durable state. A foreign busy generation is retained for its owner to advance;
  cleanup excludes fresh staging snapshots. Prepare reconciles staging receipts
  with the actual contiguous persisted prefix and replays only from the first
  gap.
- Pre-promotion News bootstrap freezes one online backup of the authoritative
  production SQLite database with Candidate source semantics and atomically
  persists that exact generation before remote prepare, then advances it through
  the normal bounded Sync replay. Restart restores the artifact instead of
  rebuilding from newer source state. Deterministic source or remote invariant
  rejection fails immediately with its reason; it is not retried as transport
  unavailability. Bootstrap must not require the still-active Stable API to
  implement a Candidate-only source protocol, activate Candidate Windows early,
  or create a second production Sync owner.
- Provider-capacity status crosses the dashboard boundary only as bounded,
  secret-safe per-authority/account projections. Forecasting may use a retained
  quota-day summary, but critical status must never scan accumulated provider
  requests or model attempts. Historical contract migration is preemptible and
  may consume only forecast-safe surplus provider capacity; its latency remains
  separate from LIVE pipeline health.
- A display limit or business selection window is not a transport guarantee.
  Transport bounds are enforced on serialized bytes at the transport boundary,
  with enough normal headroom that the emergency host limit remains a final
  guard rather than the storage model.
- Failure belongs to the resource whose write or read failed. Optional or
  growing-resource failure remains visible as that resource's degraded state,
  but shared plumbing must still publish unrelated healthy critical state.
- Public static shells and immutable assets do not enter the Worker execution
  path. `/`, `/health`, and `/audit` are canonical public URLs with distinct
  prerendered HTML identities; a direct reload or a client without JavaScript
  must receive the requested page rather than a generic Live shell. API
  requests enter a minimal router that loads only the selected API module;
  React rendering and dashboard view modules are not part of the API execution
  boundary. Snapshot JSON already validated by D1 is returned as its stored
  string, without a parse-and-serialize cycle in the Worker.
- Dashboard synchronization gives the critical heartbeat an execution owner
  independent from control and heavy optional work. A slow optional build must
  not delay the next heartbeat or make an otherwise current public status stale.
  Each optional resource owns a durable next-run time and failure backoff, each
  accumulated resource advances through a bounded page, and one cycle admits
  only a fixed number of heavy resources. The single serial heavy owner selects
  the earliest due resource and immediately admits another bounded operation
  while overdue work remains; heartbeat cadence is not a heavy-work admission
  limit. Successful schedules advance from the prior durable due time without
  accumulating execution-time drift, while missed periods coalesce instead of
  creating a catch-up burst. Restarting the synchronizer must not collapse those
  independent cadences into one upload burst.
- The audit landing resource is a fixed summary contract. Daily Brief bodies,
  decision inspection rows, and storyline presentation detail are separate
  lazy snapshots with independent item and serialized-byte bounds. Local
  SQLite remains the complete authority; D1 contains only those bounded
  display projections. A detail snapshot that has not loaded or is unavailable
  must not be represented as an empty collection or zero count.
- A bounded News index page is projected in one D1 query after the current
  generation identity is established. Review/category/parsed counts that cannot
  change inside an immutable generation are materialized atomically at every
  activation and read from the generation summary; they must not be recomputed
  by scanning CURRENT on each visitor request. Page rows use review/category
  ordering indexes. Canonical fixed-width candidate-expiry timestamps are
  stored once in sorted order with the generation summary; the Worker derives
  the time-dependent active count by binary search without another D1 row scan.
  Page rows, counts, review buckets, category buckets, and staging identity cross
  the D1 boundary together; the Worker decodes the page array once and preserves
  the established public-copy, expiry, ordering, and pagination semantics.
- During a split-snapshot handover, the read boundary selects the freshest
  valid compatible snapshot by durable `received_at`, with the split snapshot
  winning only an exact timestamp tie. Legacy audit detail is projected and
  item-bounded inside D1 JSON1; a Worker must not deserialize the growing
  legacy document merely to decide freshness. Invalid or oversized candidates
  are skipped, and absence of a valid bounded source fails closed.
- The storyline display projection retains at most 12 current storylines, 12
  candidates per candidate family, eight streams per stream family, and six
  first/last timeline events per storyline. Exact aggregate totals remain in
  `storyline_summary`. The same limits govern the Python split producer and the
  Worker legacy projection so a fresh authoritative legacy snapshot cannot be
  displaced by an older validation-shaped split row merely because its former
  display selection exceeded the 120,000-byte transport envelope.
- The coordinated migration seeds a missing fixed `news_metrics` aggregate from
  the last valid legacy audit owner into the bounded audit summary. This is a
  one-time, reverse-compatible handover write: Stable ignores the split row,
  Candidate reads only the bounded summary, and the promoted Dashboard Sync
  owner replaces that summary on its next normal cycle. Public reads must not
  use the growing legacy audit document as a recurring fallback.
- If the still-active legacy Stable has lost derived News detail rows, the
  coordinated migration may rebuild its rollback-only `news_index` and
  `news_details` projection from the exact receipt-verified `CURRENT`
  generation. Details are materialized before index rows, no generation or
  forecasting authority is deleted, and obsolete legacy index identities are
  retained as `SUPERSEDED_CONTRACT`, model-ineligible evidence. The active
  legacy identity set must exactly equal the verified CURRENT identity set;
  equal counts alone are insufficient. Migration acceptance independently
  requires that identity equality, all D1 legacy invariants, and the real
  Stable News health endpoint to pass. Candidate and the promoted runtime
  continue to read generation storage; the legacy copy is not a normal fallback.
  While the pre-cutover Stable writer remains active, D1 fences verified CURRENT
  legacy index and detail identities against deletion or mutation, and rejects
  non-CURRENT legacy index inserts without active logical mutation. Replacement
  activation changes the CURRENT owner and its legacy mirror in one atomic
  transaction; a one-time migration repair alone is not continuing compatibility
  authority.
- Candidate-era Audit split projection handover has one explicit owner record:
  Dashboard Sync is the sole execution owner; forecasting SQLite is the
  authoritative store; the active Business Runtime revision is the producer;
  the exact Candidate Worker is the consumer; and the boundary is
  `SQLite -> Windows Dashboard Sync -> D1 -> Worker`.
  `/api/audit-briefs`, `/api/audit-stories`, and `/api/audit-decisions` are
  optional display resources during normal operation but mandatory release
  obligations when their producer changes. Each write is independently item-
  and byte-bounded. Its checkpoint is `generated_at` plus exact
  `producer_revision`; release evidence also binds the validation key and
  post-cutover boundary.
- Before Promote, the legacy producer remains the only Sync owner and all
  Stable-capable projections retain normal parity. Candidate-only projections
  are explicit deferred obligations, not passes or ignored routes. After Sync
  is paused, Windows and Worker cut over, and that same Sync owner resumes from
  Candidate code, observation requires fresh exact-producer snapshots and full
  semantic equality. Failure retains the last-good D1 value and immutable
  evidence, blocks Stable commit, and is isolated from forecasting SQLite.
  Pending evidence retries within observation; hard failure or timeout uses the
  release rollback. A second pre-promotion Sync owner is forbidden.
- Local `/api/status` and `/api/critical-status` are the same bounded
  first-paint contract. They include only the fixed recent 90-minute decision
  window and the fixed `news_metrics` aggregate required by the Live headline;
  News rows, audit, learning, market detail, and older history retain
  independent lazy/paged owners. A compatibility alias must not rebuild or
  serialize the complete historical dashboard payload. Missing aggregate
  authority must not be rendered as a true zero. During a single-owner release
  handover, the Worker may fill a missing status aggregate from the fixed audit
  summary in the same bounded D1 read; it must not scan News rows or the legacy
  full audit snapshot for that repair.
- Local audit, learning, and market-chart summary GETs read durable derived
  models rather than invoking historical builders. A single background owner
  tracks each resource independently, builds outside the request boundary, and
  atomically replaces a model only when its source revision is unchanged.
  Contract/hash mismatch or corruption fails closed; a failed rebuild retains
  the prior known-good model and cannot delay the critical status owner.
- When a bound is exceeded, repair ownership, projection, pagination, batching,
  or failure isolation first. Do not default to raising the host limit or
  deleting authoritative evidence.

Preview-specific write isolation and provenance guarantees are defined in
[`PREVIEW_ISOLATION.md`](PREVIEW_ISOLATION.md).
