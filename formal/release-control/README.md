# Release Control formal verification

Required verification is a composition of bounded state-family proofs. It does
not multiply detailed CPU provider evidence by installation, News, Access, and
Switch/Observe state.

## Required shards

| Boundary | Safety | Liveness |
|---|---|---|
| CPU evidence | quotas, monotonic accumulation, hard failure, top-up budget, exact-key reuse, artifact invalidation, accepted-stage preservation | provider-pending recovery |
| Release integration | abstract CPU applicability, Candidate classification, pass and Promote gates | not applicable |
| Core release | Stable ownership, Prepare/Verify isolation, Switch/Observe and rollback | Switch/Observe/recovery termination |
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

## Local execution

Java 11 or newer is required. The runner downloads TLA+ tools `v1.8.0` into the
ignored `.local/tools` directory and verifies SHA-256
`dbcc75552f21978a4846688b8e23be1a6b6c0b3fcee35d78fec2df167958ec94`.

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
