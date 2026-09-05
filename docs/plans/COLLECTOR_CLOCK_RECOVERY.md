# Collector clock atomicity and degraded recovery

## Change contract

The collector owns one clock-event commit, not each persistence helper. Quote,
News and model preparation runs before acquiring the SQLite writer reservation.
The resulting snapshot, decision, prediction families, derived evidence and
collector completion must commit together. A failed commit cannot advance U5 or
the collector cursor. Exact complete replay reads immutable evidence; it does
not rerun inference. Missing required evidence is not a completed clock.

Existing snapshot-only events retain their exact bytes and hash. When historical
prediction inputs were not frozen, recovery appends a bounded repair batch and
LEGACY_ENGINEERING snapshot assignment. It does not manufacture a decision,
prediction, outcome or LIVE_OOS eligibility. Conflicting content or downstream
evidence rejects this recovery route. No table, trigger or uniqueness constraint
is removed or weakened.

## Owners and impact

Collector entrypoint -> ForwardEngine -> prepared market/News/model evidence ->
ForwardLedger transaction -> existing immutable tables -> completion/cursor/U5.
Local SQLite is controlled-exact authority. U5 files are reconstructible runtime
checkpoints, not completion authority. Code comes from the installed Business
Runtime; state stays beneath the authoritative runtime root. Configuration and
secrets keep their existing owners. Provider availability is not a commit fact.

Control Center owns the incident maintenance transition, installed Watchdog
singleton and normal Switch/Observe. The explicitly permitted degraded baseline
has absent Collector and Watchdog, stale receipt, healthy single-owner remaining
services, Stable traffic 100%, and no active install/release transaction. Absence
requires successful identity enumeration; unknown and duplicate are not absent.
No direct bootstrap before the old collector restart/discovery loop is contained.

## Compatibility and recovery matrix

| Combination | Required behavior |
| --- | --- |
| New code / new complete event | Exact replay; no extra rows |
| New code / old snapshot-only event | Preserve and exclude with explicit repair evidence |
| New code / contradictory old event | Reject without mutation |
| Old code / new complete event | Existing schema remains readable |
| Old code / excluded snapshot-only event | May still fail; rollback is degraded, not healthy |
| Dead Watchdog / dead Collector | Verified incident maintenance, never fake healthy |
| Live or unknown old owner | Adopt only proven owner or block takeover |
| Failed candidate runtime | Existing transaction recovery; retain immutable evidence |

## Failure matrix and rehearsal

Preparation failure writes nothing. Process death after snapshot, decision or
partial v2 insertion must roll back the entire clock. Death after commit and
before cursor/checkpoint must replay the committed result, preserving U5
continuity. Writer contention remains BUSY/LOCKED, not an integrity error.
Recovery death either commits the exclusion batch and assignment together or
commits neither; restart is idempotent.

The preserved 6.95 GB online backup is a read-only baseline. Write rehearsals use
an independent copy, never overwrite production. Reproduce the old UNIQUE error,
recover, restart in a new process, replay the same clock, then process a legitimate
later fixture clock. Real WAL/multiple-connection crash tests supplement this.
Staged Windows recovery must include stale ACTIVE receipt, zero Watchdog and
Collector, living other services, exact bundle identities and real service state.
Production requires a fresh read-only owner/transaction/traffic preflight after
staging and exact-head CI. No production recovery has yet been performed.

## Acceptance

### Incident installation and supervision containment

The zero-owner installation must use the existing install transaction and the
existing Watchdog kernel mutex. A bootstrap installer reserves that mutex while
checking a successfully enumerated absent-owner baseline and suspending both
canonical scheduled tasks. It releases the reservation before waiting for the
replacement Watchdog. The prior stale receipt is evidence, not a lock to delete.

The existing install record carries the incident's clock/hash, broken Business
revision, target revision and runtime/repository binding. This is an explicit
maintenance context, not a new receipt family. While the broken revision is
active, Collector startup is held and automatic Candidate discovery is held;
remaining services retain their actual health and supervision. Only the normal
exact-target Switch may start the corrected Collector. Reboot cannot implicitly
release this hold. Heartbeats expose the maintenance context.

Failure before or during zero-owner installation restores the verified old
bundle and captured business owners, but does not start the broken supervisor.
Its safe outcome is the explicitly degraded baseline with scheduled bootstrap
disabled, not a healthy rollback. Successful installation only restores
supervision; it does not grant Candidate qualification or Promote authority.
Normal Promote must independently revalidate this exact incident baseline and
retain full qualification and Observe. The rollback receipt must name the
degraded baseline rather than claiming all old business services are healthy.

Implementation is under review. Offline copied-database crash/restart and the
real staged QUIESCED launcher/receipt path passed; complete ACTIVE takeover and
production verification remain outstanding. This is not an authorization
receipt or evidence that production bootstrap is presently safe.

SOURCE_READY, SUPERVISION_RECOVERED, COLLECTOR_RECOVERED and STABLE_COMMITTED are
separate results. Offline work continues if supervision is unavailable. Production
does not proceed until the verified maintenance/switch path can preserve other
services and report truthful rollback health. Exact qualification, Access, Free,
Observe and Assistant PAUSED rules remain unchanged. This plan is not evidence
that any of those gates has passed.
