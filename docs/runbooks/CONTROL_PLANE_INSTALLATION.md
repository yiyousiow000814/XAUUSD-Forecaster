# Control Plane Installation Runbook

Updating `.local/runtime-control` is a local Control Plane transaction. It is
not a Business Runtime Promote and must not use `InstallRuntime`.

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
stopping the old watchdog, and disables new task/guard triggers during handoff.
Success requires a different process-start token, exactly one watchdog owner,
and an exact, hash-verified target heartbeat. The Business Runtime revision and
all five service process identities must remain unchanged.

The installer fails closed when a Promote/Reverse transaction is active, the
Control Center GUI is open, the current or staged bundle is unverifiable, or
service ownership is ambiguous. Failure after the old watchdog stops restores
the previous complete bundle, launches a new process from that previous bundle,
verifies its heartbeat and single ownership, and reports `ROLLED_BACK`.

Inspect bounded status in:

```text
.local/forward/control-plane-install-state.json
```

Do not hand-edit that file, `runtime-control-bundle.json`, release history, or
Stable/Candidate identities. The transaction does not change Worker traffic,
D1, or SQLite.
