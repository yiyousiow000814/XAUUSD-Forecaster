# Release Control formal verification

Required verification is a composition of bounded state-family proofs. It does
not multiply detailed CPU provider evidence by installation, News, Access, and
Switch/Observe state.

## Required shards

| Boundary | Safety | Liveness |
|---|---|---|
| Watchdog singleton | one mutex owner, one active writer, receipt-bound authority, Guard fail-closed termination, quiesced writer exclusion | proven-empty owner replacement |
| CPU evidence | quotas, monotonic accumulation, hard failure, top-up budget, exact-key reuse, artifact invalidation, accepted-stage preservation | provider-pending recovery |
| Release integration | abstract CPU applicability, complete receipt waterfall, behavior-key applicability, lease freshness, frozen dependency digests, pass and Promote gates | not applicable |
| Core release | Stable ownership, Prepare/Verify isolation, Switch/Observe and rollback | Switch/Observe/recovery termination |
| Recovery Hotfix | orthogonal mode, exact LKG restoration, bounded eligible-family hotfix, same Evidence DAG, short Observe | recovery termination without assuming healthy Active |
| Release runtime read model | Active/Committed/LKG separation, immutable artifact lookup versus placement, and Reverse entry authority | not applicable |
| Install recovery | fencing and independent abandoned-install checks | abandoned-install termination |
| News migration | CURRENT/Reverse identity compatibility, generation replacement, cleanup | not applicable |
| Access evidence | exact receipt validity and idempotent approval | not applicable |

`shards.json` is the authoritative property and implementation ownership map.
It also defines shared formal interfaces: a shared-interface change selects all
required shards, a family change selects that family and its integration owners,
and a change outside modeled ownership produces a bounded no-op result.

Each required model checks one exact release transaction or bounded generation
sequence. Reapplying the verified transition relation to later exact
transactions is an induction over the same interface contract, not a second
independent lifecycle dimension. CPU families are semantic model values for
quota-unsatisfied/satisfied behavior; concrete route counts, request identities,
and byte-level fixture identities remain implementation contract tests.

The interface contract and the complete old-to-new property mapping are in
`DECOMPOSITION_AUDIT.md`. In particular:

1. `CpuQualified` guarantees no hard failure, required quotas, and an applicable
   fresh or exact-key reused qualification.
2. `ReleaseIntegration` consumes only an abstract CPU state and that guarantee.
3. `CoreRelease` cannot start Promote without abstract CPU qualification.
4. Qualification-key movement invalidates CPU applicability.
5. CPU-only recovery preserves independently accepted stages.
6. Provider pending remains non-promotable without becoming a false Candidate
   regression.
7. The runtime read-model shard guarantees that read-only observations never
   mutate Committed Stable/LKG; Reverse requires available Active observation,
   exact Active-to-Committed identity, exact rollback artifact lookup, and
   control authority. Business health is independent, so `DEGRADED` does not
   block otherwise-safe recovery. `CoreRelease` alone owns the proof that Stable
   changes only after successful Observe.
8. The Evidence Authority refines the 15-node waterfall to one abstract valid,
   missing, or tampered state. Promote requires a complete valid waterfall,
   matching behavior keys, fresh action-time leases, and frozen dependency
   digests; planning is modeled as mutation-free.

## Local execution

The install shard also explores a verified incident baseline with no old
supervisor. `incidentBaseline` abstracts the exact incident admission checks,
not an operator override. `Facts` remain independently required for activation.
The installer's reservation is the existing Watchdog mutex: replacement startup
requires its release; installer death releases that OS-owned reservation. The
singleton shard owns mutual exclusion and business-owner preservation. Windows
installation tests exercise that handoff with a real named kernel mutex.
Before a replacement exists, abandoned installation restores the old bundle;
for an incident baseline this restores verified absence, not fictitious ACTIVE
supervision. `AbandonedInstallEventuallySafe` retains its original ACTIVE
guarantee for normal baselines and permits only the explicitly degraded,
zero-owner terminal state for an incident. No CPU, News or release lifecycle
state is added to this shard. Environment-equivalent recovery rehearsals remain
required; TLC is not proof of successful production takeover.

Java 11 or newer is required. The runner resolves the checked artifact from
`formal/tools/tlc/tool-lock.json`, verifies its size, full digest and JAR
structure, and uses a digest-addressed `.local/tools` cache. There is no network
or latest-release fallback. See [tool provenance](../tools/tlc/README.md).

Run one authoritative shard:

```text
python scripts/run_tla_model.py --shard cpu-evidence-safety --output local
```

The JSON report records elapsed time, generated and distinct states, maximum
observed queue depth when TLC emits it, result, and the authoritative property
set. The adjacent log is the bounded TLC output.

## CI contract

The PR workflow selects shards from `shards.json`, runs them in parallel, and
gives every required job a hard five-minute timeout. A final job named exactly
`Release Control TLC` only aggregates results; it does not rerun TLC and fails
when planning or any selected matrix job fails, times out, skips unexpectedly,
or is cancelled. PR-scoped concurrency cancels work from superseded heads.

The historical combined `ReleaseControl.tla` model and its two configurations
remain available only for manually requested deep engineering exploration. They
are not a required PR gate and cannot replace the compositional proofs.
