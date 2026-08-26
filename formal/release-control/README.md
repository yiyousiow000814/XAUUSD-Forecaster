# Release Control Formal Model

This simplification-first model covers the Release/Control Plane composition
boundary, not the forecasting product or Worker route payloads. Its only
release-attempt lifecycle phases are `PREPARE`, `VERIFY`, `SWITCH`, and
`OBSERVE`. Review reasons, holds, handoff checkpoints, reverse, and recovery are
internal operations. It still abstracts exact identity, immutable acceptance,
Sync ownership, watchdog epochs, Control Plane handoff, CURRENT/Reverse-Stable
compatibility, restart, cleanup, return, and recovery.

## Run

Java 11 or newer is required. The runner downloads TLA+ tools `v1.8.0` into the
ignored `.local/tools` directory and verifies SHA-256
`eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a`
before execution.

```text
python scripts/run_tla_model.py
```

The default command runs:

- `ReleaseControlSafety.cfg`: failure/restart exploration with safety
  invariants and deadlock detection;
- `ReleaseControlLiveness.cfg`: healthy-dependency exploration with fairness
  and progress properties. Its documented environment assumption disables an
  infinite stream of new operator-initiated Control Plane installs or identity
  drift while a valid target is trying to progress; install, restart, stale
  supervisor, main movement, and identity-drift behavior remain explored by the
  safety model.

For a focused run, pass `--config ReleaseControlSafety.cfg`. CI uses one TLC
worker because liveness correctness is more important than parallel speed and
the bounded model is intentionally small. A deeper local run may increase
`--workers`, but it must use the same pinned tool and properties.

TLC state directories are created in the operating-system temporary directory
and removed after each run. They are never repository artifacts.

## Incident classes represented

The following actions and invariants generalize the observed failure classes:

| Class | Model elements |
|---|---|
| Watchdog defeats migration freeze | `BeginHold`, `WatchdogRecover`, `ActiveHoldOwnsStoppedSync` |
| Install requires owner during intentional stop | `CaptureBaseline` preserves the observed baseline; owner legality is a separate Prepare precondition |
| Snapshot/handoff TOCTOU | `FenceSupervisor`, `InstallQuiescedSupervisor`, `StaleSupervisorAttempt`, `StaleSupervisorIsFenced` |
| REVIEW_REQUIRED has no exit | `RetryEvidence`, `RetryableReviewEventuallyExits`; review remains data inside Verify |
| One-time legacy repair drifts later | `PrepareGeneration`, `ActivateGeneration`, `CurrentKeepsReverseCompatibility` |
| Recovery repeats failed normal precondition | `FailInstall`, `RecoverInstall`, `InstallEventuallyRecovers` |
| Hold expires during work | `HoldExpires`, `EndMigrationHold`, `StoppedSyncEventuallyResumes` |
| Restart during lifecycle transition | `RestartMachine`, `RecoverInstall`, `ApplyRecoverySwitch`, `ObserveRecovery` |
| Main moves during immutable transaction | `MainMoves` changes main but never the exact in-flight target; discovery is disabled during a transaction |
| Old supervisor mutates after fencing | epoch mismatch enables only `StaleSupervisorAttempt`, which cannot mutate ownership |

Scenario and implementation mappings live in
[`docs/design/RELEASE_CONTROL_STATE_MACHINE.md`](../../docs/design/RELEASE_CONTROL_STATE_MACHINE.md).
