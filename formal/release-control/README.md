# Release Control Formal Model

This simplification-first model covers the Release/Control Plane composition
boundary, not the forecasting product or Worker route payloads. Its only
release-attempt lifecycle phases are `PREPARE`, `VERIFY`, `SWITCH`, and
`OBSERVE`. Review reasons, holds, handoff checkpoints, reverse, and recovery are
internal operations. It still abstracts exact identity, immutable acceptance,
mutable dependency health, Sync ownership, watchdog epochs, Control Plane
handoff, CURRENT/Reverse-Stable identity compatibility, cleanup, return, and
recovery.

## Run

Java 11 or newer is required. The runner downloads TLA+ tools `v1.8.0` into the
ignored `.local/tools` directory and verifies SHA-256
`eabd140a70f49eb9305a3bd3f3df944eddf87e5a90d329789085f8953a80533a`
before execution.

```text
python scripts/run_tla_model.py
```

The default command runs:

- `ReleaseControlSafety.cfg`: mutable-health, Switch/Observe failure, News
  generation, installer-death, stale-owner, identity-drift, cleanup, and
  fail-closed exploration with invariants and deadlock detection;
- `ReleaseControlLiveness.cfg`: mutable-health progress with fairness. Health
  may degrade during Switch or Observe and later recover in the same behavior.
  New operator installs, main movement, and identity corruption are disabled in
  this configuration so they cannot create an infinite unrelated work stream;
  the safety configuration still explores them.

CI and the local command always run both configurations with one TLC worker.
The runner intentionally accepts no command-line executable, config, cache, or
worker overrides; the model boundary is small enough that flexibility would add
command-execution surface without a verification benefit.

TLC state directories are created in the operating-system temporary directory
and removed after each run. They are never repository artifacts.

## Incident classes represented

The following actions and invariants generalize the observed failure classes:

| Class | Model elements |
|---|---|
| Watchdog defeats migration freeze | `BeginHold`, `WatchdogRecover`, `ActiveHoldOwnsStoppedSync` |
| Install requires owner during intentional stop | `CaptureBaseline` preserves the observed baseline; owner legality is a separate Prepare precondition |
| Success, observation failure, automatic return | `ApplySwitch`, `DegradeHealth`, `ObserveFailure`, `ApplyRecoverySwitch`, `ObserveRecovery` |
| Failure before Switch applies | `DegradeHealth`, `FailSwitch`, recovery Switch and observation |
| Snapshot/handoff TOCTOU | `StartQuiescedSupervisor`, supervision epochs, `StaleSupervisorIsFenced` |
| Installer death skips safety | five `InstallerDiesAt` checkpoints, `VerifyAbandonedInstall`, `RejectAbandonedInstall`, `RecoveredActivationRequiresIndependentChecks` |
| Missing/extra legacy projection identities | `StageLegacyInvalid`, `FreshStagingCompatible`, `ActivateGeneration` |
| One-time legacy repair drifts later | generation 0 -> 1 -> 2; every `PrepareGeneration` clears staged compatibility evidence |
| Cleanup deletes CURRENT or fresh STAGING | `CleanupObsolete`, `CurrentGenerationCannotBeCleaned`, `FreshStagingCannotBeCleaned` |
| Hold expires during work | `HoldExpires`, `WatchdogRecoversSync`, `ActiveHoldOwnsStoppedSync` |
| Main or exact identity moves | `MainMoves`, `CorruptCandidateIdentity`; neither can mutate the in-flight target or produce accepted evidence |

Scenario and implementation mappings live in
[`docs/design/RELEASE_CONTROL_STATE_MACHINE.md`](../../docs/design/RELEASE_CONTROL_STATE_MACHINE.md).
