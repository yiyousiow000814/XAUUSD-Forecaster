# Script entrypoints

| Directory | Responsibility |
| --- | --- |
| `runtime/` | Collector, news worker, dashboard API/sync and broadcast processes |
| `maintenance/` | Explicit backfill, bootstrap, migration, pruning and recovery rehearsal |
| `research/` | Offline experiments, panels, reports and retrieval audits |
| `architecture/` | Source compiler, extractors, evidence and architecture checks |
| `validation/` | CI selectors/runners, health checks, Preview and fixture builds |

Run Python scripts by their repository-relative path, for example
`python scripts/runtime/run_forward_collector.py --help`. Their code root is
resolved from the entrypoint location; data-root and CLI contracts are unchanged.
Reusable business behavior belongs in `xauusd_forecaster`, not another script.

Four Windows deployment files intentionally remain at this directory's root:
`run_main_services.ps1`, `main_services_launcher.vbs`, `main_runtime.ps1` and
`windows-service-launch-contract.json`. Installed scheduled tasks and an
already-running controller reference those public paths. They are the actual
single-owner launch/registry implementation, not old-path forwarding copies.
The registry names the classified Python service paths. Changing an installed
launcher path requires a separate task-registration migration, not a file move.
