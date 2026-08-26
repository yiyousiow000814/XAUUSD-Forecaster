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
| `scripts/run_news_annotator.py` (job dispatch, account/model routing, durable batch transitions, lock retry, scheduler sleep) | `xauusd_forecaster/news_scheduler_runtime.py` | Annotator scheduler-runtime owner | Entry-point wrappers retain thread-pool/process wiring and legacy call names | Move behind `news/scheduler/runtime.py` during D3, then remove the flat shim after imports migrate |
| `scripts/run_news_annotator.py` (Daily Brief backlog cycle) | `xauusd_forecaster/daily_brief_runtime.py` | Daily Brief runtime owner | Entry-point compatibility import only | Move behind `news/brief/runtime.py` during D3, then remove the flat shim after imports migrate |
| `scripts/run_forward_collector.py` (news-contract reconciliation and five-minute grid append rules) | `xauusd_forecaster/collector_runtime.py` | Collector domain runtime owner | Entry point imports canonical functions; cadence/process wiring remains in the script | Fold into the canonical Decision/Evidence packages during D1 after all callers migrate |
| `scripts/xauusd_control_center.ps1` (runtime supervision) | `scripts/xauusd_control_center_runtime.ps1` | Control Center runtime owner | Stable entry path dot-sources the owner into the same script scope | Retain because the dot-source file is part of the hashed runtime-control bundle |
| `scripts/xauusd_control_center.ps1` (release transactions and validation) | `scripts/xauusd_control_center_release.ps1` | Control Center release owner | Stable entry path dot-sources the owner into the same script scope | Retain because the dot-source file is part of the hashed runtime-control bundle |
| `scripts/xauusd_control_center.ps1` (diagnostics and UI) | `scripts/xauusd_control_center_presentation.ps1` | Control Center presentation owner | Stable entry path dot-sources the owner into the same script scope | Retain because the dot-source file is part of the hashed runtime-control bundle |
| `xauusd_forecaster/decision/__init__.py` legacy decision import surface | `xauusd_forecaster/decision/selection.py` | Decision-selection owner | Package facade contains explicit imports and `__all__` only | Retain while root package exports `ShadowDecisionGate` and `select_recommended_action` |
| `xauusd_forecaster/forward_engine.py` | `xauusd_forecaster/decision/engine.py` | Five-minute Decision orchestration owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/inference_v2.py` | `xauusd_forecaster/decision/inference.py` | V2 Decision inference owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/live_v2.py` | `xauusd_forecaster/decision/live.py` | Frozen Decision/outcome append owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/forward_ledger.py` | `xauusd_forecaster/evidence/ledger.py` | Append-only evidence-store owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/evidence_v2.py` | `xauusd_forecaster/evidence/schema.py` | V2 evidence schema/integrity owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/executable_label.py` | `xauusd_forecaster/evidence/executable_label.py` | Executable-price evidence label owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/training/__init__.py` legacy training import surface | `xauusd_forecaster/training/materialization.py` | Training materialization owner | Package facade contains explicit imports and `__all__` only | Retain while external callers use the historical training module surface |
| `xauusd_forecaster/training_v2.py` | `xauusd_forecaster/training/generation.py` | Generation fitting/publication owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/training_owner.py` | `xauusd_forecaster/training/runtime.py` | Background training lease/runtime owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/ridge.py` | `xauusd_forecaster/training/ridge.py` | Ridge artifact/fitting owner | THIN_SHIM | Remove after external callers migrate from the legacy module |

Future Phase D rows must name every retained flat facade. `THIN_SHIM` means the
legacy Python file contains only a docstring, explicit canonical imports,
`__all__`, and a documented alias when necessary. Canonical package code may
not import a path marked `THIN_SHIM`.
