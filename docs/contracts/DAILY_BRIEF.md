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
items, finalization is recorded without waiting for the live-day regeneration
debounce. Restart preserves refresh, retry, and finalization state in SQLite.
The shared annotation scheduler reserves a bounded part of each discovery batch
for those unfinished historical dates so continuous current-day arrivals cannot
starve their remaining semantic reviews.

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

The generated product is a synthesis, not an evidence index. Every current
revision contains a concise model-written overview that relates the day's
material events, followed by at most five summarized developments with exact
evidence IDs. The model receives bounded short citation references; validated
references are mapped back to exact internal evidence IDs before persistence,
so copying a long opaque identifier is never part of the model task. Raw
candidate headlines and annotations remain supporting input;
the UI does not present them as the Daily Brief itself.

## Capacity and failure

`DAILY_BRIEF` has a declared model route. The background worker uses only
normal `ROUTINE` credentials and scheduler-owned account RPM, TPM, and RPD
accounting. A PREEMPTIBLE credential is not required. Missing capacity becomes
`DEFERRED` with a retry time. Account headroom is re-ranked for each date in a
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
