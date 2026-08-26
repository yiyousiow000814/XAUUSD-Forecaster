# Runtime and Release

## 1. Purpose

This subsystem supervises the five Windows services, validates immutable
Candidate revisions, and changes the coordinated Windows/Cloudflare Stable
identity only through explicit local operator control.

## 2. Execution boundary

The Control Plane and Business Runtime are distinct identities. The Control
Plane owns the installed, hash-verified runtime-control bundle and watchdog;
the exact child Control Center owns operator actions against that bundle. The
watchdog is a long-running control process launched through hidden VBS/guard
paths and may be maintained by scheduled control. Each managed Business Runtime
service is a separate process. Cloudflare
Versions and placement are an external control plane; GitHub is validation and
source control only.

| Dimension | Current state |
|---|---|
| Ownership | Control Center/Watchdog owns service lifecycle and release transaction state. |
| Boundary | Control `PROCESS`, five managed service `PROCESSES`, Cloudflare Version control plane. |
| Critical Path | Current Stable service health and exact release identity. |
| Bounded Work | Fixed service set, bounded checks, diagnostics and observation windows. |
| Incremental | Candidate watermark, immutable version IDs, transaction phases and append-only history. |
| Failure Isolation | Failed Candidate retains/reverses to Stable; unrelated service evidence is preserved. |

## 3. Owner

`scripts/install_control_plane.ps1` installs the exact fetched `origin/main`
revision through a detached clean staging worktree and verifies the complete
runtime-control bundle before an ordered watchdog handoff. The installed
`scripts/xauusd_control_center.ps1` child is the stable CLI, `Action`, and
`ServiceKey` entry path. It dot-sources three same-process owners:
`xauusd_control_center_runtime.ps1` for supervision and runtime identity,
`xauusd_control_center_release.ps1` for Candidate validation and release
transactions, and `xauusd_control_center_presentation.ps1` for diagnostics and
WPF/WinForms presentation. The release owner is the only normal owner allowed
to Promote or Reverse Stable. Child path, installed control revision and bundle
hashes must match the parent Control Plane identity. Each Business Runtime
service owns its own heartbeat; Control Plane installation preserves its
revision and processes while the watchdog observes the service contracts.

## 4. Inputs and outputs

Inputs are `origin/main` revisions, required CI state, immutable Cloudflare
Versions and metadata, local configuration/secrets, service heartbeats, API
readiness, production-shape checks, and operator actions. Outputs are service
processes, Candidate validation evidence, release transactions, Stable and
previous-Stable identity, runtime observation, history, and visible failure.

## 5. Durable state

Ignored local runtime-control files store Candidate discovery, Stable/Candidate
identity, transaction phase, applied/observed Windows revision, service state,
and release history. Per-service status JSON stores liveness. Cloudflare stores
immutable Version metadata and active placement. Git history is code identity,
not active release state.

## 6. Current data flow

```text
new immutable Version + main provenance
  -> Candidate discovery watermark
  -> CI/platform/data/API/Windows preflight
  -> Candidate PASSED or visible BLOCKED/FAILED
  -> explicit Promote
  -> Windows staged reload + Cloudflare placement + real observation
  -> Stable committed
  -> explicit Reverse Stable when rollback is required
```

Managed services are Quote Bridge, Collector, News Annotator, Dashboard API,
and Dashboard Sync.

## 7. Critical path

The watchdog protects current Stable liveness. Candidate validation and build
work must not mutate Stable. Push, PR merge, and `main` movement upload or
identify code only; they cannot activate it. Observation requires real service
and decision evidence before committing Stable.

## 8. Bounded-work mechanisms

The service inventory is fixed. Candidate checks have explicit timeouts,
bounded diagnostics, exact required routes, and a finite observation window.
Release validation uses a copied preflight database and production-shape
fixtures. State/history projections are bounded for operator display. This
document does not redefine the numeric values in release contracts/code.

## 9. Incremental mechanisms

Candidate discovery stores a Version watermark. Repository and GitHub reads
retry bounded transient transport, rate-limit and 5xx failures for the same
Candidate identity while deterministic authentication, permission, invalid-ref,
missing-commit and reachability failures remain fail-closed. Identities bind Worker Version
ID and Git SHA. Release transactions persist phase and prior Stable before a
switch. Applied revision, observation boundary, and history events allow a
restart to continue or reverse the same transaction instead of inventing a new
one.

## 10. Failure behavior

Candidate validation failure preserves current Stable and already-valid
Windows evidence. A reload/observation
failure restores the previous revision and Worker placement where possible and
records explicit failure. If rollback itself fails, the system exposes a
distinct operator-action-required state. One service failure is not silently
reclassified as global data loss.

Control Center actions publish the current structured operation-result schema
through an atomic result file and explicit process exit code. The semantic
result is independent from ambient PowerShell native exit state and binds exact
Candidate/Stable identity, immutable completion-history proof and an audit
event. The GUI consumes that result, falls back only to matching state plus
immutable history, and otherwise presents `INDETERMINATE`. Handles and busy
state are cleared before presentation refresh.

## 11. Restart/recovery behavior

The watchdog reads durable release and runtime state, verifies current process
identity, resumes observation when safe, and resolves incomplete Promote or
Reverse transactions according to their stored phase. Hidden launchers prevent
visible terminal windows and the guard maintains a single control owner. WPF
may fall back to WinForms only before the first successful render; post-render
failures remain owned and visible in WPF rather than switching release engines.

## 12. Entry points

- `scripts/xauusd_control_center.ps1`
- `scripts/install_control_plane.ps1`
- `scripts/xauusd_control_center_runtime.ps1`
- `scripts/xauusd_control_center_release.ps1`
- `scripts/xauusd_control_center_presentation.ps1`
- `scripts/xauusd_control_center_launcher.vbs`
- `scripts/xauusd_watchdog_launcher.vbs`
- `scripts/xauusd_watchdog_guard.ps1`
- `scripts/xauusd_watchdog_guard_launcher.vbs`
- `scripts/build_release_validation_fixtures.py`
- Cloudflare build/release commands in `web/package.json` and
  `web/wrangler.jsonc`

## 13. Core modules

- `xauusd_forecaster/runtime_health.py`: atomic service heartbeat writer.
- `scripts/xauusd_control_center_runtime.ps1`: process discovery, service
  supervision, runtime identity, watchdog, shortcut and autostart behavior.
- `scripts/xauusd_control_center_release.ps1`: release persistence, Candidate
  validation, Promote/Reverse transaction ordering and recovery.
- `scripts/xauusd_control_center_presentation.ps1`: bounded diagnostics and the
  WPF/WinForms shells.
- `xauusd_forecaster/production_shape.py`: preflight state contract.
- `scripts/check_production_shape.py`: one-shot production-shape CLI.
- `scripts/check_public_health.py`: bounded public health probe.
- `.github/workflows/quality-gates.yml`: validation only.
- `.github/workflows/windows-runtime-gates.yml`: Windows contracts only.
- `.github/workflows/repository-policy*.yml`: protected hosting-policy checks.

## 14. Relevant tests

`tests/test_runtime_launchers.py`, `tests/test_control_plane_install.py`, `tests/test_runtime_health.py`,
`tests/test_production_shape.py`, `tests/test_release_validation_fixtures.py`,
`tests/test_ci_contracts.py`, and `tests/test_repository_policy.py` cover
service inventory, exact child/bundle identity, safe Control Plane installation
and rollback, bounded repository retries, WPF lifecycle, structured operation
results, Candidate/Stable transitions, production shape, and control-plane
boundaries.

## 15. Authoritative contracts/specs

- [Release Control](../contracts/RELEASE_CONTROL.md)
- [Cloudflare Deployment Runbook](../runbooks/CLOUDFLARE_DEPLOYMENT.md)
- [Hosting Boundaries](../contracts/HOSTING_BOUNDARIES.md)
- [Operational Health](../contracts/OPERATIONAL_HEALTH.md)

## 16. Known current gaps

The runtime, release, and presentation owner files still share one PowerShell
process and script scope by design, so they are not independent failure
domains. The thin entry script remains their composition root. Changes must
continue to review shared state and sibling boundaries even though ownership is
now visible in separate files.

## 17. Links back to System Architecture

Return to [System Architecture](SYSTEM_ARCHITECTURE.md) or continue to the
[Codebase Map](../reference/CODEBASE_MAP.md).
