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

Future Phase D rows must name every retained flat facade. `THIN_SHIM` means the
legacy Python file contains only a docstring, explicit canonical imports,
`__all__`, and a documented alias when necessary. Canonical package code may
not import a path marked `THIN_SHIM`.
