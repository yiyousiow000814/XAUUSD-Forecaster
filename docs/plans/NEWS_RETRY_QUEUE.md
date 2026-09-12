# News retry queue change

## Change contract

Ordinary annotation, title and impact failures return to the existing eligible
queue instead of imposing per-record 15/60/360/720-minute delays. Failure evidence
remains immutable and incomplete results remain unusable. Provider/account quota,
dispatch cooldown, prerequisites and explicit operator scheduling keep authority.
No new service, table, lock, model request stage or credential is introduced.

The production news annotator owns discovery and scheduling; SQLite owns durable
jobs, leases, failures and recovery receipts. Account lanes retain independent
connections and atomic claims. Failure producers publish immediate eligibility;
pending readers still require the same evidence and honor any unrecovered failure
barrier. The existing bounded recovery grant removes old automatic barriers and
requeues affected operational jobs, while preserving active operator overrides,
leases, completed jobs and superseded-source retirement.

## Impact and lifecycle

`news annotator -> run_scheduled_batch -> claim_job -> pending_record_for_job ->
annotation/title/impact -> immutable failure + queue release -> later batch`.
Queue age is available_at (initial enqueue or last release), not original creation
age. Each batch has a fixed eligibility cutoff, shared by all its account lanes;
work requeued after that cutoff cannot monopolize that round. Existing priority,
live/backfill and account boundaries remain. The next supervised loop can admit
requeued work using current capacity; no extra per-record timer is installed.

Empty queues return without provider work. Missing evidence remains pending under
its prerequisite schedule; this change does not mistake a missing reader result
for an ordinary provider failure. Partial success preserves accepted earlier
stages. Crashes retain failures and leased jobs; lease expiry and next discovery
recover them. Recovery grants commit with operational changes and are idempotent.
New failures do not receive legacy recovery grants because their retry time is
not later than their failure time. Persistent provider failures remain visible;
finite batch admission and existing quota/dispatch admission bound their work.

## Compatibility and recovery

| Boundary | Behavior |
|---|---|
| New code / old state | Existing receipt ledger lifts delayed nonterminal failures in pages of at most 200 per task family; current eligibility still applies. |
| New code / new state | Immediate ordinary requeue; immutable failure timestamps are not revised. |
| Old code / new state | Same schema; old runtime may reintroduce its delays on later failures. Historical evidence stays readable. |
| Restart during grant | SQLite transaction is atomic; repeat discovery does not duplicate grants. |
| Failure before lease release | Existing lease expiry recovers the queue; failure and accepted artifacts remain. |
| Operator override | Recovery cannot replace a chosen availability time or claim a leased job. |
| Deployment | Protected main remains the only production source; no PR runtime is activated. |

Known-good baseline is main 427adb6ac0485ebc03f11cab499329cef16a5267.
Code recovery uses the existing main-only service process, independently of retry
success; it never restores an older database. No code cutover is performed until
normal required gates and the existing review requirements are satisfied.

## Evidence and verification

SQLite identities, transactions and queue order are controlled exact. Provider
availability is external and may change; account and provider admission remain
the authoritative dispatch boundary. Provider delivery is not promised, and a
retry becoming eligible is not a successful model result.

Extend existing family tests for annotation/title/impact failure -> immediate
reader eligibility; old failure -> bounded receipt -> same job recovery; fairness
across two jobs, one batch and concurrent lanes; cooldown/quota denial and later
success; supersession, user overrides and restart preservation. Use real Python
with temporary file-backed SQLite and the production scheduler entrypoint, with
provider results supplied by a deterministic fixture. Read-only production SQL
confirms actual failure shapes, source identities and provider state without
calling models or altering credentials. A post-main runtime check must identify
the deployed source and observe old delayed work requeued and actual processing.

Focused tests precede the news family and required CI. No timeout increases or
whole-database recapture are required. Detailed rehearsal output stays local.

## Execution matrix and pre-merge evidence

| Runtime / entry | Boundary exercised | Success and rejection evidence |
|---|---|---|
| Python 3.14 / repository pytest / temporary file-backed SQLite | `run_scheduled_batch -> account lanes -> claim/release -> next batch` | Each task family attempts each identity once per batch across serial and concurrent lanes; the next batch completes those same jobs after the fixture provider recovers. |
| Python / existing recovery functions / production pending reader | Immutable old failure -> recovery receipt -> exact leased source | All three task families recover a real source record while preserving original failure rows; explicit operator waits and retired sources remain unavailable to claims. |
| Python / provider dispatch state | Real capacity admission and explicit Retry-After | Denied before the provider deadline, admitted afterward; ordinary failures do not manufacture a per-record deadline. |
| Read-only SQLite URI / current Windows runtime database | Current prompt and LIVE-lane jobs/failures | Before deployment, 130 annotation and 264 impact jobs remained BACKING_OFF; recent failures had 15-minute, one-hour and twelve-hour automatic delays. Counts are operational jobs, not a claim that every source remains current. |

The production caller is `scripts/runtime/run_news_annotator.py`, through
`run_scheduled_batch_with_lock_retry` and the existing supervised sleep policy.
The queue cutoff is supplied by `run_scheduled_batch` to every account lane and
claim. Tests exercise these Python/SQLite boundaries from this checkout without
provider traffic. Runtime source before this change is the baseline above.
Read-only preflight and detailed logs are retained in the task-local acceptance
folder; no authoritative database copy or rewrite was performed.

Pre-merge focused family: 368 passed. Extended exact-reader recovery coverage:
30 passed. Repository policy and architecture/import checks passed. Generated
architecture was checked against the changed source. Required remote CI and
post-main production observations remain separate acceptance steps.
