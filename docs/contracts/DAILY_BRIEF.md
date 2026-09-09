# Daily Brief Contract

Daily Brief is one date-scoped, display-only evidence product. A date may have
many immutable revisions while it is live and exactly one recorded
finalization. It is not a forecasting input or a trading signal.

## Date and population

The authoritative timezone is `Asia/Kuala_Lumpur`. An article belongs to the
local date containing its `collector_first_seen_time`. Receipt time is used
because it proves when the system could first have known the item; publisher
time does not.

For a cutoff, the population contains the latest revision received within that
local date, with sufficient fetched body content, after canonical cluster
deduplication. A population item is reviewed only when the same revision and
content hash has a valid annotation under the active annotation prompt and an
approved annotation model by the cutoff. Title translation and impact analysis
are optional presentation and ranking inputs; they are not completion gates.

The population query first chooses the latest, body-qualified representative
for each source item and cluster within the receipt date. Superseded rows are
therefore absent rather than terminal. An unreviewed representative is settled
only when its active annotation job reaches an explicit terminal state. A peer
received on another date must not remove or terminally settle that date's
representative. Settled terminal items are counted separately and do not block
the date forever.

## Lifecycle

- `WAITING`: the current date has no reviewed material yet.
- `UPDATING`: a rolling revision exists or reviewed material is ready while
  more semantic work remains.
- `DEFERRED`: generation is retryable but model capacity or a recorded failure
  currently prevents it.
- `FINAL`: a historical date is fully settled and its latest revision is final.
- `DEGRADED`: the date is final, but one or more items settled terminally.
- `EMPTY`: a closed historical date has no in-scope received material.

The current date is never finalized. Crossing midnight does not abandon the
previous date: each worker cycle processes the current date and a bounded,
newest-first backlog of unfinished dates. Once a historical date has no pending
items, finalization is recorded without waiting for live-day refresh eligibility.
Restart preserves refresh, retry, and finalization state in SQLite.
The shared annotation scheduler reserves a bounded part of each discovery batch
for those unfinished historical dates so continuous current-day arrivals cannot
starve their remaining semantic reviews.

Discovery checks the exact annotation job identity before reading its source
body again. Queued, leased, and backing-off work remains owned by the existing
claim/retry path; discovery must not reset its due time or failure history.
An owned job is not a completed annotation. Protected-date reconciliation uses
the unfinished-date authority, independently of the rows newly discovered in
that cycle, so later cross-date peers cannot retire already-owned reviews.
Once the date is effectively finalized, normal supersession rules apply.

The same job-count mutation owner maintains two fixed operational metadata keys:
`NEWS_JOB_INPUT_REVISION_V1` and `NEWS_JOB_RECONCILIATION_CACHE_V1`. They are
discardable, rebuildable optimization metadata, not immutable evidence or remote
ACKs. Only their values may be updated; key renames, creation-time updates and
replacement over an existing key are rejected. `FORWARD_EPOCH`, all pre-existing
metadata keys, and even a schema-valid null key keep their original immutable
UPDATE/DELETE protection. No general mutable metadata namespace is introduced.

The job version changes in the same transaction as each relevant job insertion,
deletion or field change, including changes that leave aggregate counts equal.
Rollback rolls back both. Installing the hooks acquires the writer lock before
checking their exact definitions, atomically replaces old hooks and invalidates
prior reconciliation acceptance before repairing missing authority. Installation
inside an active caller transaction is rejected without committing it. Old
positional count-metadata writes and old `CREATE IF NOT EXISTS` installers remain
compatible. Missing, noncanonical or overflowing versions cannot authorize reuse.
This global version is not a per-date synthesis or Sync acceptance authority.

Global job reconciliation may return unchanged only when its exact post-work
job version, four immutable source tails, eligibility/materialization contract,
Forward epoch and protected receipt-day set match accepted local work. Its
bounded cache is read with those inputs in one snapshot. A changed or missing
input, changed contract, corrupted/oversized metadata or backward clock runs the
original completion and retirement rules. Reconciliation does not use current
time to make source eligibility decisions; claim, backoff and synthesis due
times still belong to their existing owners. A hit creates no new observation
timestamp, job transition or cache write and reads no article/annotation payload.

Acceptance is published only after both real reconciliation statements, using
their post-transition version inside the same writer transaction. Caller-owned
transactions and explicit unmanaged calls retain the original uncached path;
they do not publish optimization acceptance. Mixed-source restores and direct
non-owner rowid reuse cannot use tail-token acceptance; restore a coherent
database or invalidate the disposable cache. No global reconciliation counter
is substituted for a date-specific synthesis input or a remote Sync ACK.

Protected discovery must also leave ordinary contract backfill able to advance
without increasing the total allowance. When at least two historical slots are
available, one is reserved for the durable ordinary cursor. With a single slot,
each exact owned job leaves discovery, so a fixed finite protected population
drains before ordinary discovery resumes. This is not a guarantee under an
unbounded stream of new historical inputs; provider dispatch quotas are unchanged.

Date-list discovery may reuse one disposable, at-most-4,096-byte JSON value in
the existing current-date refresh row. It must not create a lifecycle row just
to hold this cache or expose the internal field in the public Brief summary.
The key binds the selector contract, Forward epoch, recovery version, requested
limit, local day, and bounded exact scalar tails of `news_revisions`, original
finalizations, and recovery corrections. These inputs remain append-only and
their owners allocate implicit increasing rowids; no payload, COUNT, MAX or
historical DISTINCT is needed on an unchanged hit. The cache is supported for
the existing bounded backlog limit; other limits retain the ordinary selector.

Within a day, newly due receipts can only add the current day, which is already
returned before the newest unfinished historical dates. Thus future receipts
do not require another full-history deadline scan. Local midnight, a time before
the cached observation, contract/limit movement, and any changed input tail
invalidate reuse. SQLite and Python must agree on the current receipt day;
ambiguous clock interpretation uses the ordinary selector. This says nothing
about generation, retry, leases, reconciliation or other owners' due work.

The source token and selected dates must come from one SQLite read snapshot.
Release that snapshot before an independent short cache UPDATE, so normal WAL
appends cannot cause a read-to-write upgrade failure. A late publication retains
its original token: a newer source must reject it, not treat it as current.
Inside a caller-owned transaction, return its snapshot result without publishing
or committing any cache; rollback cannot leak an uncommitted source revision.
Normal read-only consumers may compute without caching. Independent cache
publication uses a zero-wait writer budget and restores the connection's normal
budget afterward. An ordinary busy writer or read-only consumer may leave the
cache unpublished, with no immediate retry or loss of the computed result.
This tolerance is confined to optional publication: source-read, authorization,
extended moved-database and storage failures remain visible. Missing old-schema
state or malformed/oversized cache values use the ordinary computation; they do
not imply completed work.

Cache validity assumes the existing coherent SQLite backup/restore boundary:
immutable evidence and its derived state are copied together. The Forward epoch
and tail token are not a globally unique database identity or proof of arbitrary
history integrity. Independently transplanting a cache between databases, or
restoring inputs while retaining unrelated derived state, is not supported.
The nullable field is installed by the existing additive lifecycle migration;
old named-column Brief writers preserve it and old readers need not use it.
No production migration is implied by an isolated compatibility test.

Frequent worker checks do not imply generation. For a changed live-day packet,
the durable refresh decision combines age since the last successful revision,
new canonical event or episode identities, material updates, major-event
semantics, packet information change, annotation and impact backlog, provider
cooldown, and repeated scheduler deferral. The policy derives its age bands from
the existing 30-minute progress boundary: a major event may qualify after one
boundary, accumulated material change after two, ordinary material change as
the Brief reaches four, quiet low-information change may wait eight, and any
changed packet qualifies by twelve so continuous pipeline work cannot starve it.
Pipeline or provider pressure raises the earlier eligibility bands but never the
twelve-boundary starvation limit. These are conditional age bands, not a cron
cadence or a replacement fixed debounce.

## Revisions and candidates

Generated revisions and finalization/failure evidence are append-only.
Operational refresh state is mutable. The UI reads the authoritative latest
revision for a date and never overwrites older revisions.
When a lifecycle defect made an earlier degraded finalization incorrect, a
versioned append-only correction may reopen that date once and record a new
effective finalization. The original finalization remains immutable audit
evidence; processing and display use the correction for that recovery version.
The generation source hash covers both the bounded evidence packet and the
prompt contract version, so a new synthesis contract creates a new immutable
revision even when the underlying candidates are unchanged.
Revision persistence and finalization are separate append-only commits. If a
worker stops after the revision commit, the next run must reuse that exact
date-and-source-hash revision and finish finalization; it must not call the
model again or attempt a duplicate insert. The same resume rule applies to the
deterministic degraded fallback.

The model packet is selected deterministically from the complete reviewed
population. It keeps one highest-ranked update per canonical event, then ranks
by existing review priority, impact/update semantics, major event category,
materiality, novelty, confidence, and receipt identity. Only after event-level
deduplication and ranking is the packet capped. Arrival order alone cannot push
an important early event out of the packet.

The refresh snapshot uses those same canonical event and episode identities;
it does not create a second identity system. Syndicated or duplicate rows that
do not change the selected event packet remain on `CANDIDATES_UNCHANGED` and
spend no model request. Pending packet, information-gain, next-eligible, and
provider-deferral state are persisted so restart cannot manufacture a refresh.

The generated product is a synthesis, not an evidence index. Every revision
under the current synthesis contract contains one concise conclusion, one to
three distinct drivers, one supported next item to watch, and at most five
summarized developments with exact evidence IDs. All synthesis fields are
required and bounded; incomplete structure fails validation instead of being
partially published. The model receives bounded short citation references;
validated references are mapped back to exact internal evidence IDs before
persistence, so copying a long opaque identifier is never part of the model
task. Raw candidate headlines and annotations remain supporting input; the UI
presents the synthesis first, keeps the two highest-ranked developments
immediately visible, and progressively discloses the remainder. Historical
revisions that predate the structured synthesis remain readable without
inventing missing drivers or watch items.

## Source-first synthesis preparation

The existing date refresh row may hold one disposable, at most 32 KiB synthesis
source cache. It is local optimization state, not an immutable Brief, a remote
ACK, or a new date owner. A hit must not update `last_observed_at`, clear a
failure, advance a Sync checkpoint, or fabricate a new revision. Public summary
output excludes this internal field. Missing, malformed, future-dated or
inapplicable state uses the genuine business path.
Actual finalization retires this new disposable cache without deleting Brief
revisions or source evidence; interrupted optional cleanup may resume later.

Applicability binds the receipt date and current civil day, Forward epoch,
annotation and synthesis contracts, recovery version, effective input budget,
the existing latest revision/finalization and refresh state, and scalar tail
identities from revisions, annotations, translations, impacts and event
resolutions. Each identity is read in the same SQLite snapshot. These source
families retain their append-only implicit-rowid contract. Coherent backup and
restore preserve both source and optimization state; a partial/mixed restore
must invalidate the cache. A rewind, unknown identity or backward clock is not
unchanged evidence.

If immutable tails moved, at most 128 appended scalar identities across all
five families may be examined before deciding dated relevance. An unrelated-day
append may advance these examined tails without rebuilding a packet. Impacts
are traced through their actual annotation foreign key, and event resolutions
through assessment to annotation; duplicated source fields are not substituted
for those relationships. Delta overflow uses the uncached path, not a truncated
claim that the day is unchanged.

Existing future timestamps require a separate visibility deadline even when no
row is appended. Initialization and due-time refill inspect a conservative
scalar superset across the five families, without body or annotation JSON. Each
materialized family is bounded, and more than 1,024 total facts withholds cache
acceptance. This limit does not truncate the authoritative population or change
model eligibility. Due work or unproven cache applicability uses the existing
complete population; partial scalar inspection is not accepted as unchanged. An
indexed MIN returning one scalar does not make its input work constant.

The three performance-only access paths are receipt Julian day, translation
revision/content plus parse time, and event-resolution assessment plus resolve
time. They add no uniqueness or data authority. Initial index construction
visits existing table keys and can allocate storage or take a writer lock;
rollout must budget that separately from hot no-change reads. Installation is
additive and retry-safe; completed DDL can remain after an autocommit failure.
It must never rewrite old source evidence. Missing or unrecognized access paths
do not trigger an optional full-history initialization scan.

The existing job-count mutation hooks invalidate only an already-present
affected receipt-day cache when an ACTIVE_ANNOTATION DEAD_LETTER membership or
its identity can change the population. A normal lease transition does not
invalidate unchanged synthesis content. Invalidation shares the job transaction;
rollback cannot publish it. A live reader checks all three canonical hook
definitions in the same source snapshot and again during publication. Missing,
oversized or old-writer definitions make the cache inapplicable; readers never
repair schema. The same job-revision reader gates global reconciliation, so an
old hook cannot leave either optimization trusting a stale counter. Old schema
variants intentionally retain their ordinary uncached business paths. The
existing installer invalidates stale optimization before
repairing lost/replaced hooks or missing job-version metadata. Publication checks the
exact observed job version and state under a short writer transaction, so a
concurrent invalidation cannot be overwritten by an earlier prepared result.
SQLite catalogs have no name index: canonical-hook inspection reads fixed schema
metadata and returns at most three 8 KiB definitions. Its work is reported
separately from historical source reads; it is not advertised as an indexed
constant-time seek. No schema catalog or history mutation occurs on a cache hit.

An unchanged successful candidate set bypasses population/packet work. A failure
whose unchanged input still waits for its retry retains its reason, counter and
deadline. Adaptive pending work reuses only the accepted bounded event snapshot
and runs the existing adaptive decision with current backlog/provider/time
facts; it does not invent a second policy or freeze cooldown and aging. At the
actual due boundary the normal builder and generation path run. Optional cache
publication uses a zero-wait writer acquisition; exact BUSY/READONLY can defer
only that optimization. Source reads and other storage errors still surface.

## Capacity and failure

`DAILY_BRIEF` has a declared model route. The background worker uses only
normal `ROUTINE` credentials and scheduler-owned account RPM, TPM, and RPD
accounting. A PREEMPTIBLE credential is not required. Missing capacity becomes
`DEFERRED` with a retry time. Local account/model exhaustion is
`MODEL_CAPACITY_DEFERRED`; provider governor pacing is
`PROVIDER_DISPATCH_DEFERRED`; missing compatible routine credentials retain
`NO_COMPATIBLE_ROUTINE_ACCOUNT`. These reasons must not be collapsed.
Account headroom is re-ranked for each date in a
batch, so one exhausted account cannot starve the remaining bounded backlog.

Malformed or incomplete JSON, non-STOP provider completion, schema violations,
unknown evidence references, and provider
errors fail closed: no brief is written, an immutable failure is recorded, and
bounded exponential retry state is persisted. Unknown evidence references use
`MODEL_OUTPUT_CONTRACT_FAILED`; malformed response structure uses
`MODEL_OUTPUT_INVALID`; transport failures use `MODEL_REQUEST_FAILED` or the
more specific provider code. Contract-failure evidence is bounded to a response
hash, at most eight unknown evidence IDs, and the allowed evidence count. It
must not retain the prompt, article bodies, or complete model output. Database
errors still surface.
One failed brief must not terminate the annotation worker. A closed historical
date stops synthesis retries after five consecutive failures for the same
candidate set and records a `DEGRADED` deterministic fallback made only from
the already-reviewed headlines, summaries, and evidence IDs.

## Historical reconstruction

Missing historical dates may be reconstructed only from real immutable news
revisions and semantic artifacts present in the ledger. The revision's actual
`cutoff_at` and `generated_at` disclose that it was generated later; they must
never be rewritten to impersonate a contemporaneous brief. Reconstruction uses
the same bounded backlog, population, capacity, validation, and finalization
rules as normal processing.

Reader-facing prose must not expose temporary evidence-packet references such
as [E04, E14]. Generation puts refs in structured evidence_ids only. The reader
also removes bracketed packet refs from all brief text fields for retained
revisions, without rewriting stored text, changing canonical evidence IDs,
issuing model requests or removing ordinary bracketed source content.
