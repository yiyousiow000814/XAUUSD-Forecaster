# Module Migration Map

## Status

This is the path-level handover register for the `PENDING` modularization
campaign. A row does not make its canonical path `CURRENT` before the owning PR
merges.

| Legacy import/path | Canonical import/path | Owner | Shim state | Removal condition |
|---|---|---|---|---|
| `scripts/run_dashboard_api.py` (`StatusSnapshotCache`) | `xauusd_forecaster/dashboard/status_cache.py` | Dashboard status snapshot cache | Entry-point compatibility import in PR #283 | Remove only after callers no longer rely on the entry-point name |
| `scripts/run_dashboard_api.py` (runtime health projection) | `xauusd_forecaster/dashboard/health_projection.py` | Dashboard runtime-component projection | Entry-point compatibility import in PR #285 | Remove only after callers no longer rely on the entry-point names |
| `scripts/run_dashboard_sync.py` (resource serializers, learning/news/market projections, byte bounds) | `xauusd_forecaster/dashboard/resource_contracts.py` | Dashboard resource-contract owner | Entry-point compatibility imports; no copied logic | Remove after Preview/release builders and all tests import the canonical owner |
| `scripts/run_dashboard_sync.py` (cursor/checkpoint files, cadence/backoff, status contracts) | `xauusd_forecaster/dashboard/sync/progress.py` | Dashboard Sync progress owner | Entry-point compatibility imports; one canonical schedule lock | Remove after orchestration callers import only the owner |
| `scripts/run_dashboard_sync.py` (remote/local HTTP, auth headers, target configuration) | `xauusd_forecaster/dashboard/sync/transport.py` | Dashboard Sync transport owner | Entry-point compatibility imports; no retained transport implementation | Remove after orchestration callers import only the owner |
| `scripts/run_dashboard_sync.py` (per-resource mirror protocols) | `xauusd_forecaster/dashboard/sync/resource_protocols.py` | Dashboard Sync resource-protocol owner | Entry-point compatibility imports; builders now import package owners directly | Remove after orchestration/tests no longer rely on entry-point aliases |
| `scripts/run_dashboard_api.py` (news archive, evidence generation/paging, news display metrics) | `xauusd_forecaster/dashboard/news_resources.py` | Dashboard news-resource owner | Entry-point compatibility imports; shared cache exists only in canonical owner | Remove after route integration callers no longer rely on entry-point names |
| `scripts/run_dashboard_api.py` (quote-file cache, market history SQL/paging, current chart projection) | `xauusd_forecaster/dashboard/market_resources.py` | Dashboard market-resource owner | Entry-point compatibility imports; shared quote cache exists only in canonical owner | Remove after route integration callers no longer rely on entry-point names |
| `scripts/run_dashboard_api.py` (current status, deployment/learning/session projections, optional resource composition) | `xauusd_forecaster/dashboard/status_resources.py` | Dashboard status-resource owner | Entry-point compatibility imports; derived learning cache exists only in canonical owner | Remove after API/process callers no longer rely on entry-point names |
| `scripts/run_dashboard_api.py` (operator authorization, retry-job read, override batch application) | `xauusd_forecaster/dashboard/operator_bridge.py` | Local scheduler operator-bridge service owner | Handler delegates through explicit imports; scheduler retains transition authority | Remove entry-point aliases after HTTP callers use only the service boundary |

Future Phase D rows must name every retained flat facade. `THIN_SHIM` means the
legacy Python file contains only a docstring, explicit canonical imports,
`__all__`, and a documented alias when necessary. Canonical package code may
not import a path marked `THIN_SHIM`.
