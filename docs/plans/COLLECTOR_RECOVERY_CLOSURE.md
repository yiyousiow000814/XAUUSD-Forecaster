# Collector recovery closure plan

Status: implementation and rehearsal pending. This plan is not release evidence.

## Change contract

The change is limited to deterministic real-process fixture identity setup and
admission of the proven old local News evidence timeout within the existing
collector incident. Normal installation and release authority stay unchanged.
Production state, source snapshots, service owners, secrets, and provider
authority remain with their existing owners. No second Sync owner is permitted.

## Recovery dependency and acceptance matrix

| Stage / existing owner | Admissible incident fact | Required proof / unconditional blockers |
| --- | --- | --- |
| Install / Get-CollectorClockRecoveryBaseline | Collector and Watchdog absent; exact old local News GET timeout | Complete process enumeration, exact single remaining owners/revisions, fresh Sync heartbeat, basic API health, snapshot hash/integrity, Stable placement and no conflicting transaction |
| Bootstrap / independent activation | Same recorded broken business baseline | Exact bundle, installer/Watchdog fencing, mutex, isolation and ACTIVE proof; no business restart |
| Snapshot repair / Invoke-CollectorClockRecoveryOperation | Same precisely bound News failure | Re-run incident baseline under release lock; preserve snapshot and idempotent exclusion; no fabricated historical prediction |
| Qualification / evidence DAG | Old producer cannot supply corrected business evidence | Corrected copied-database API-to-Sync proof; all applicable Candidate, migration, CPU, capacity and Access gates; only explicit producer-dependent obligations may defer |
| Action freshness / Publish-PromotionFreshnessEvidence | Verified degraded baseline, never HEALTHY | Same target and broken revision, fresh incident admission, provider placement, rollback target, locks and evidence identities |
| Switch / Start-ReleasePromotion | Frozen degraded rollback baseline | Existing NORMAL transaction, checkout, real service replacement and Worker ordering; no alternate release engine |
| Observe / Test-RuntimeObservation | Recovery pending is not a pass | Exact new producer, successful News read and normal remote ACK or verified no-change; existing deferred-obligation timeout and rollback; no commit while pending |
| Rollback / captured RuntimeRecoveryPlan | Restored old baseline may remain degraded | Exact old code and owned service set, preserved data; report degraded recovery, never healthy rollback |

The same baseline function is called at install and again at snapshot repair and
action-time freshness. Changing only the initial install would retain the
deployment deadlock. Existing deferred projection handling currently restricts
routes to Candidate-only audit projections; a News obligation must be explicitly
validated at creation, dispatch, observation and commit, not added to a general
health whitelist. Rollback health also needs to distinguish a proven recorded
resource failure from owner or basic-health failure.

## Compatibility, failure and evidence plan

New controller / old runtime may admit only the exact recorded incident; new
controller / new runtime must prove resource recovery. Old controller behavior
is unchanged. Old data remains authoritative and snapshot-only repair preserves
immutable evidence. Rollback restores captured old contracts and truthfully
reports the degraded baseline. Installer death retains independent activation
and isolation requirements. Duplicate/missing owners, stale heartbeat, other
resource failure, remote generation conflict, corruption, unknown mutation or
traffic drift block admission. New SQL timeout, failed ACK or Switch failure
must prevent commit and exercise normal bounded recovery.

The fixture identity uses an owned live child and Windows creation time, then
verified exit. It must not infer a start token from wall clock or invent a PID.
Both real withdrawal and ACTIVE rehearsals retain real bundle, launcher, mutex,
receipt and heartbeat boundaries. Isolated endpoints may replace providers, not
the installer or service's own heartbeat. A degraded-start full rehearsal and
real copied-database API-to-Sync run are required before source readiness.

External provider reads remain authoritative only within their actual response
contracts. Cached prior observations are not fresh production proof. No source
change or fixture pass authorizes production cutover without exact-head and
new-main gates. Efficiency acceptance remains separately PARTIAL.
