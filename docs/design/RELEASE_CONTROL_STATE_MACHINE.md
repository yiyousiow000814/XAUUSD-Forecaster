# Release Control State Machine

## Design objective

Release Control uses the smallest operator lifecycle that preserves exact
identity, evidence, single ownership, recovery, and return-to-previous-Stable
guarantees. Technical handoff checkpoints remain internal and durable only when
restart recovery needs them.

## Requirements and actors

The authoritative release record owns Stable, Previous Stable, the version
being prepared, immutable evidence references, and at most one switch
transaction. The local Control Center is the sole release mutator. Cloudflare,
the Windows runtime, Sync, watchdog/guard, GitHub checks, migration tools,
generation activation, and the operator are interacting actors. Stale actors
must be fenced before an isolation snapshot becomes authoritative.

## Before and after

```text
Before (operator had to interpret implementation states)

READY -> STAGING/TESTING -> REVIEW_REQUIRED -> retry -> PASSED
      -> PRECHECK -> CUTOVER -> OBSERVING -> READY
      -> RECOVERY_REQUIRED -> REVERSING -> REVERSE_OBSERVING -> READY
      + separate hold/install/handoff states and exceptions

After (operator lifecycle)

Stable -> Prepare -> Verify -> Switch -> Observe -> Stable
                    ^    |                 |          |
                    | retry in place       +-- failure+
                    |                         returns to previous Stable,
                    +-- more evidence -------- verifies recovery in Observe
```

`Stable` is the resting condition, not a release attempt phase. Prepare,
Verify, Switch, and Observe are the only release-attempt phases.

## Retained phases

| Phase | Why it is independently durable |
|---|---|
| Prepare | Exact target identity and prerequisites may survive process or machine restart before evidence collection can begin. |
| Verify | Evidence collection may span external retries and time. Review reasons remain data inside Verify, never an alternate success state. |
| Switch | Worker placement and Windows ownership cannot be one physical write; one serialized transaction records the target and previous Stable across the bounded cutover. |
| Observe | Production evidence is necessarily collected after Switch. The old Stable remains the committed Stable until success; failure returns to it and verifies recovery inside this phase. |

## Removed or merged lifecycle states

| Former concept | New location | Why no independent lifecycle identity is needed |
|---|---|---|
| Candidate discovery | Prepare entry | It creates the exact target; it is not an operator decision. |
| Migration verification | Prepare/Verify internal operation | Verification uses an immutable generation and activation watermark while Stable Sync continues. It is not a separate lifecycle state. |
| Control Plane install and supervision handoff | Prepare internal transaction | Quiesce, baseline, bundle swap, acknowledgement, and activation are crash checkpoints behind one owner. |
| Repository, Windows, Worker, CPU, parity, and compatibility gates | Verify evidence set | Independent accepted evidence is immutable; incomplete or reviewable evidence stays in Verify. |
| `REVIEW_REQUIRED` and retry | Verify reason and transition | Review is not success. An allowed exact retry refreshes only rejected evidence and remains in Verify. |
| Promote precheck | Switch entry guard | It is a check-and-use boundary under the switch transaction, not a phase. |
| Cutover | Switch internal operation | Partial physical writes are recovered from the single target/previous identity record. |
| Commit Stable | Observe success transition | Stable changes once, only after observation. |
| Reverse and reverse observation | Switch/Observe with Previous Stable as target | The same identity, ownership, and observation rules apply in either direction. |
| Recovery | Parent-phase outcome | Prepare failure restores its baseline; Verify failure stays fail closed; Switch/Observe failure returns to previous Stable and verifies it in Observe. |
| CURRENT/STAGING compatibility repair | Prepare/Verify invariant | Generation activation owns ongoing compatibility; a one-time migration is not lifecycle authority. |

## Internal transaction rules

An internal checkpoint is not an operator state. Control Plane installation uses
one transaction identity and supervision epoch. The old supervisor is fenced;
the new supervisor first acknowledges `QUIESCED`; isolation is then compared;
activation is granted; and only the active epoch may mutate services. A
replacement after installer death repeats every activation proof independently;
a persisted checkpoint is recovery input, never authority. Failed revalidation
restores the verified prior bundle and supervision path.

Stable Sync keeps its single owner throughout Prepare and Verify. Additive
migration evidence binds an immutable generation and activation watermark;
later CURRENT advancement is accepted only after independent invariant checks.
The Switch transaction alone owns the short intentional Sync stop, and Observe
requires that owner to resume.

`release-runtime-read-model-v1` composes the operator phase with separately
observed Active Worker, Active Windows and Active health. Committed Stable and
its derived LKG remain the prior successful Observe result while Active moves
during Switch/Observe. Previous Worker artifact existence is established by an
exact immutable-version lookup; deployment membership remains a separate
placement fact. Reverse repeats the composed live precheck after locking and
before transaction creation, so presentation never owns mutation authority.

## Formal boundary

The TLA+ model covers the four phases, exact release identity, accepted
evidence, mutable health, forward Switch failure, post-Switch observation
failure and recovery observation, Sync ownership, generation watermarks, supervision
fencing, five installer-death checkpoints, generation identity sets, active
legacy Reverse compatibility, and protected cleanup. It omits individual News
rows, route payloads, SQL details, browser rendering, cryptography, and
quantitative CPU/load behavior; those use the verification techniques in the
safety mechanism inventory.

The bounded `ReleaseRuntimeReadModel` safety shard composes with Core Release.
It proves observation cannot mutate Committed/LKG, Active may differ without
moving them, artifact existence is independent of placement, failed or unknown
exact lookup fails closed, and Reverse entry requires a ready precheck. It does
not carry CPU, Access, News, GUI, or provider-pagination state.

## Formal-to-production mapping

| Formal element | Implementation owner | Contract and tests | Production evidence |
|---|---|---|---|
| `DiscoverCandidate` / Prepare entry | `Find-NewCandidateRelease`, `Invoke-CandidateDiscovery` | Release identity/discovery contract; Windows runtime contracts | immutable Worker annotation, discovery watermark, release history |
| Stable Sync during Prepare/Verify | migration receipt watermark and `Test-WatchdogRecoverySuppressed` | Release Control migration section; runtime launcher family | advancing CURRENT receipt, Sync process identity, watchdog log |
| migration readiness | coordinated migration verifier and receipt | Release migration receipt contract; receipt/live-evidence tests | exact candidate/database/schema/projection receipt |
| supervisor fence and baseline | `Suspend-ControlPlaneSupervision`, `Stop-VerifiedWatchdogOwner`, `Assert-ControlPlaneIsolationBaseline` | Control Plane installation contract; `test_control_plane_install.py` | install transaction, old process token, before snapshot |
| quiesced handoff and activation | `Start-WatchdogReplacement`, `Wait-VerifiedWatchdogHandoff`, `Wait-ControlPlaneInstallActivation` | acknowledged-handoff family tests | heartbeat mode, install transaction ID, bundle revision/hash, process token |
| install recovery | `Assert-AbandonedControlPlaneInstallActivation`, `Restore-AbandonedControlPlaneInstallForWatchdog` | five-checkpoint and recovery-fact family tests | old/new owner identities, exact baseline, release context, rollback/recovery result |
| Prepare → Verify | `Get-ReleaseLifecyclePhase`; migration/platform prerequisites inside validation | lifecycle projection contract test | operator phase plus explicit reason/evidence |
| Verify review/retry/pass | `Invoke-AutomaticCandidateValidation`, `Retry-CandidateValidation`, `Approve-CandidateAccessBoundary` | exact retry, Access receipt, and immutable-evidence contracts | validation key, prior reason, retained evidence, exact protected-host checklist receipt |
| identity fail closed | `Test-ReleaseIdentity`, provenance and exact-SHA gates | release validation/runtime tests | Git/Worker/Windows response and state identities |
| forward Switch | `Start-ReleasePromotion`, internal `PRECHECK/CUTOVER` | switch contract and promotion tests | transaction target/previous, Windows revision, Worker placement |
| Observe and commit | `Test-RuntimeObservation`, `Complete-ReleasePromotion` | observation/commit tests | heartbeat, API, Sync, decision cadence, deferred projection receipts |
| return/reverse | `Invoke-RuntimeRollback`, `Invoke-ReverseStable`, `Complete-ReleaseReverse` | same Switch/Observe contract; reverse tests | previous-Stable placement/runtime and recovery observation |
| restart reconciliation | `Reconcile-ReleaseControlState`, install-state adoption in `Invoke-ForecasterWatchdog` | restart/recovery family tests | durable transaction plus observed Worker/Windows/process identities |
| generation compatibility | News generation activation and projection receipt owners | News projection contract and cross-runtime tests | CURRENT/STAGING identity, legacy projection, receipt chain |
| cleanup protection | Worker generation cleanup transaction | News projection and D1 bounded-work tests | current/fresh staging rows and bounded cleanup receipts |

The 2026-08-26 install audit supplies observed production evidence for the
handoff race and recovery dependence. The amended model removes constant
health: `DegradeHealth` can run during Switch or Observe, and fairness only
requires that a bad dependency can recover. News uses two successive generation
transitions with explicit CURRENT, active legacy, STAGING, and staged-legacy
identity sets, so a repaired first generation does not authorize the next.
