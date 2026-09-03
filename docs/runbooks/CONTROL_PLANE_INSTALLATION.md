# Control Plane Installation Runbook

Updating `.local/runtime-control` is a local Control Plane transaction. It is
not a Business Runtime Promote and must not use `InstallRuntime`.

## Watchdog ownership boundary

The scheduled-task `IgnoreNew` setting is defense in depth, not Watchdog
ownership authority. Before a Watchdog reads or writes release state, emits a
heartbeat or diagnostic, or supervises a business service, it must hold the
machine-wide mutex derived from the Windows user SID, normalized Runtime Root,
normalized Repository Root, and the fixed singleton contract version. Git
revision and process ID are deliberately excluded so installation handoff
contends on the same authority.

The mutex owner atomically publishes `watchdog-owner-v2.json` under the Runtime
Root. The bounded receipt binds its instance and process start token, canonical
launcher identity, root hashes, mutex identity, installed Control Plane
revision and bundle digest, and ACTIVE or transaction-bound QUIESCED_INSTALL
mode. The heartbeat must bind the same instance and receipt digest. A duplicate
launcher exits without touching shared state. Guard recovery trusts a verified
receipt/process/heartbeat chain rather than Task Scheduler state, and never
starts a replacement until an unhealthy verified tree is proven terminated.

Installation may accept one fully shaped pre-v2 Watchdog only as the source
owner for the final upgrade. Its replacement must publish and verify the v2
receipt before the handoff is accepted. Guard remains disabled during that
bounded legacy bridge.

Close the Control Center GUI and leave the quote bridge, collector, annotator,
Dashboard API, and Dashboard Mirrors running. Fetch `main`, resolve its exact
revision, and call the bootstrap installer from the repository checkout:

```powershell
$repositoryRoot = "C:\Users\yiyou\XAUUSD-Forecaster"
$runtimeRoot = "C:\Users\yiyou\XAUUSD-Forecaster-runtime"
git -C $repositoryRoot fetch origin main
$targetRevision = (git -C $repositoryRoot rev-parse origin/main).Trim()
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$repositoryRoot\scripts\install_control_plane.ps1" -TargetRevision $targetRevision -RuntimeRoot $runtimeRoot -RepositoryRoot $repositoryRoot
```

The installer requires the target to equal the freshly fetched `origin/main`.
It stages an immutable detached worktree, verifies all bundle hashes before
stopping the old watchdog. The aggregate bundle digest is a canonical commitment
to the schema, exact revision, exact file count, and ordinally ordered relative
path/file-hash pairs; it is independent of JSON formatting, locale, PowerShell
version, and installation path. Existing schema-2 bundles are accepted only when
their exact legacy ordinal `path=hash` commitment and every individual file hash
verify; the installer always writes the current canonical schema for the target.
It disables and stops the guard but keeps the main
task enabled for restart recovery. The replacement first emits an exact
transaction-bound `QUIESCED` heartbeat. Only after service isolation is
unchanged does the installer grant activation. Success then requires a
different process-start token, exactly one watchdog owner, and an exact,
hash-verified `ACTIVE` target heartbeat. The Business Runtime revision and
all five service process identities must remain unchanged.

The installer fails closed when a Promote/Reverse transaction is active, the
Control Center GUI is open, the current or staged bundle is unverifiable, or
service ownership is ambiguous. Failure after the old watchdog stops restores
the recorded baseline without re-running the failed contextual owner rule,
restores the previous complete bundle, launches a new process from that bundle,
verifies its heartbeat and single ownership, and reports `ROLLED_BACK`. After a
machine or installer-process interruption, the main task verifies the staged or
backup bundle and resumes the same transaction; do not start a second install.
Installer disappearance is not an activation grant. The replacement remains
`QUIESCED` while it independently verifies transaction ID, bundle revision and
hash, old-owner fencing, single ownership, the recorded service baseline,
release/hold context, and absence of another release transaction. Any mismatch
restores the previous verified bundle and re-enables the recorded safe
supervision path.

Inspect bounded status in:

```text
.local/forward/control-plane-install-state.json
```

Do not hand-edit that file, `runtime-control-bundle.json`, release history, or
Stable/Candidate identities. The transaction does not change Worker traffic,
D1, or SQLite.
