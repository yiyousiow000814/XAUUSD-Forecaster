# Release Control Progressive Delivery Audit

## Scope and observed baseline

This audit precedes implementation on Draft PR #348 at
`f95e5d1caa8e27520b1c49fc268214f64d3ee3f0`. Read-only production evidence
showed one watchdog and one Dashboard Sync owner. Sync was publishing a fresh
critical heartbeat while reporting pre-existing Audit and legacy News debt.
Stable Worker traffic remained unchanged at 100 percent.

The authoritative complete News store is local SQLite. D1 generations are a
bounded derived projection. Stable heartbeat publication is critical; Audit,
learning, market, News, and other growing projections are independently bounded
resources. Candidate verification is read-only except for explicitly staged,
bounded Candidate validation operations.

## Sync stop and suppression inventory

| Location | Existing reason | Decision |
|---|---|---|
| `Enter-CoordinatedMigrationSyncHold` from **Verify Migration** | Freeze every live migration-evidence field for a two-hour receipt and suppress watchdog recovery. | Remove. Generation identity, activation time, snapshot, and receipts provide an explicit verification watermark. Normal additive verification must tolerate a later independently valid CURRENT. |
| Promotion `Restart-CodeReloadableServices(..., DeferredServiceKeys = sync)` | Prevent old Windows Sync from writing after Worker/Windows authority starts changing. | Retain only as the short SWITCH boundary. The durable release transaction owns the temporary absence and the same Sync owner resumes with the target revision. |
| Reverse explicitly stops Sync before Worker/Windows restoration | Prevent mixed old/new writers during return to Previous Stable. | Retain as the short SWITCH boundary; recovery observation must prove the restored owner. |
| Watchdog `Test-WatchdogRecoverySuppressed` | Preserve an intentional migration hold or in-flight Promote/Reverse cutover. | Delete migration-hold suppression. Retain only exact in-flight SWITCH transaction suppression. |
| Control Plane install isolation | Treat stopped Sync as authoritative when an exact migration hold exists. | Delete the exception. PREPARE installation must preserve the normal one-owner baseline; a missing Sync owner fails closed and restores the prior verified supervisor path. |
| Manual `Stop`, `ServiceStop`, restart, and watchdog unhealthy-service replacement | Explicit operator operation or direct service recovery, not Candidate verification. | Retain. `ServiceStart` is the legal direct recovery; it no longer needs to clear a release hold. |
| Old-watchdog/guard process stops during Control Plane handoff | Fence the supervisor, not Dashboard Sync. | Retain with the installer-death activation proof and rollback path. |

The exact mutable state previously frozen was the active News generation,
snapshot/digests/counts, legacy counts, and the bounded dashboard decision
count. Those values can advance under normal Stable Sync. They need not be
frozen because activation already publishes an immutable generation and
`activated_at` watermark, and each later CURRENT can be revalidated against the
same schema, migration, identity, Reverse projection, and receipt invariants.

## Candidate and Promote gate classification

Class A is an unconditional hard-safety blocker. Class B is required when the
Candidate changes, depends on, or can regress the boundary. Class C is an
unchanged Stable defect: it remains visible evidence and blocks only if the
Candidate worsens it or if it is reclassified as A.

| Gate | Class | Acceptance rule |
|---|---|---|
| Production artifact, exact main provenance, Git/Worker/Windows identity, validation-key binding | A | Exact identity is mandatory. |
| Exact-SHA repository policy, CodeQL, formal, Python, Windows, and Web required checks | A | The immutable Candidate must have the authoritative successful check set. |
| Isolated Windows production-shape preflight and required runtime startup | A | Candidate must start and serve its required critical runtime. |
| Candidate at 0 percent and exact Version override/identity headers | A | Candidate cannot gain Stable authority during PREPARE/VERIFY. |
| One Windows production owner, transaction lock, stale-actor fence | A | Exactly one writer/transaction authority. |
| D1/binding identity, migration ledger, additive/reverse contract, capability and receipt integrity | A when storage changes | Unknown, destructive, corrupting, or non-reversible storage changes fail closed. |
| Stable and Previous-Stable placement plus rollback availability | A | SWITCH cannot begin without a legal return path. |
| Directed Worker auth, bounds, dry-run non-mutation, request/event-universe integrity | A for every selected changed family | Changed writes cannot mutate authority and evidence cannot be ambiguous. |
| Worker 5xx, 1102, exceeded limits, contaminated/missing samples | A for every selected changed family | Real platform failures always block the affected Candidate. |
| Worker CPU headroom | B | Required for changed/owned Worker families and baseline families selected by shared runtime changes. |
| Broadcast compatibility/live delivery | B | Required only when broadcast ownership changes. |
| Platform configuration behavior | B, with A resource identity sub-gate | Review only when platform configuration changes; missing/wrong resources remain A. |
| `/api/status` identity, schema, monotonic decisions, freshness, and open-market quote | A | Critical production/readiness contract remains globally required. |
| Audit, Audit split, learning, market, News, and evidence semantic parity | B when owned or depended on | Changed boundary must pass full semantic and freshness acceptance. |
| Unchanged Audit, learning, market, News, or evidence defect already present in Stable | C | Record both Stable and Candidate outcomes. Matching debt is nonblocking; Candidate-only failure, worsened count/freshness/schema, or identity ambiguity is regression and blocks. |
| Access unauthenticated-boundary behavior | B | Blocking when auth/Access ownership changes; otherwise record inspection without turning unrelated Stable Access debt into a gate. |
| Deferred Candidate-only projection receipts | B | Allowed only for an explicitly changed producer that cannot publish before cutover; exact post-cutover observation is mandatory. |
| Promote validation PASSED, compatibility PASSED, exact placement/runtime, one owner, rollback target | A | Rechecked under the SWITCH transaction before mutation. |
| Post-SWITCH heartbeat/API/Sync/decision cadence | A | Stable authority is not committed until observation succeeds. |
| Post-SWITCH changed/deferred projection semantics | B | Changed surfaces must publish exact target-producer evidence. |

The global-perfection defect is `Test-CandidateDataParity`: it reads every
listed route and turns any failure into `SEMANTIC_DATA_PARITY_REVIEW_REQUIRED`
without asking whether Stable already had the same failure or whether the
Candidate owns that boundary. The replacement policy keeps `/api/status` hard,
uses the existing validation-manifest ownership mapping for B, and records
unrelated equal Stable/Candidate failures as C. A C result never authorizes a
Candidate regression.

## Before and after flow

```text
Before
Stable Sync -> Verify Migration -> two-hour hold / zero Sync owner
                                  -> global route parity -> SWITCH -> OBSERVE

After
Stable Sync -----------------------------------------------------------> continues
       Candidate PREPARE -> immutable/staged generation
                         -> VERIFY at generation/activation watermark
                         -> A + changed B + non-regression C evidence
                         -> short SWITCH -> OBSERVE -> commit Stable
                                         \-> restore previous Stable and observe
```

PREPARE and VERIFY do not mutate Stable authority and do not stop its Sync
owner. Candidate verification may observe a newer CURRENT than its original
receipt only when the newer generation has a nondecreasing activation watermark
and independently passes all CURRENT, receipt, identity-set, schema, Stable-read,
Candidate-read, and Reverse compatibility checks.

## Deleted complexity

- `migration_sync_hold`, its two-hour ownership record, enter/exit helpers,
  watchdog exception, installer-isolation exception, GUI abort coupling, and
  hold-specific tests and formal transitions;
- exact equality between receipt-time and recheck-time advancing projection
  fields;
- unconditional all-route semantic perfection as a Candidate pass condition;
- documentation and runbook steps that require replay while production Sync is
  stopped.

No operator phase is added. The lifecycle remains
`PREPARE -> VERIFY -> SWITCH -> OBSERVE`.

## Non-negotiable invariants and recovery

- Stable authority changes only after successful post-SWITCH observation.
- PREPARE/VERIFY cannot change Stable Worker traffic, Windows authority, or the
  sole production writer.
- At most one production writer exists; zero Sync owners is legal only inside
  an exact bounded SWITCH transaction or direct operator service operation.
- Candidate and evidence identities bind exact Git SHA, Worker Version, Windows
  revision, D1 UUID, migration hashes, and validation key.
- Storage changes remain additive/reverse-compatible unless a separately
  reviewed destructive-downtime contract exists; no destructive migration is
  treated as the normal path.
- CURRENT, receipt integrity, exact active legacy identity equality, cleanup
  protection, security, request bounds, and real Cloudflare failure evidence
  remain hard blockers where applicable.
- Existing Stable debt is never hidden. It becomes nonblocking only when it is
  unrelated, unchanged, and not a hard-safety violation.
- Every stopped or isolated production owner has a direct legal recovery. On
  degradation, preserve evidence, restore the last-known-safe Stable service
  first, then perform permanent correction. Failed SWITCH/OBSERVE returns to
  Previous Stable and verifies recovery; failed Control Plane install restores
  the prior verified bundle and supervisor.
