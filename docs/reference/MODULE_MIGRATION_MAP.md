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
| `xauusd_forecaster.news` legacy flat module | `xauusd_forecaster/news/collection/intake.py` | News collection intake | NONE; namespace is now side-effect-free | Legacy direct symbol imports must migrate to the concrete collection owner |
| `xauusd_forecaster/news_collection_owner.py` | `xauusd_forecaster/news/collection/runtime.py` | News collection runtime owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/source_polling.py` | `xauusd_forecaster/news/collection/source_polling.py` | News source polling owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_source_registry.py` | `xauusd_forecaster/news/collection/source_registry.py` | News source registry owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/content.py` | `xauusd_forecaster/news/collection/content.py` | News content intake owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/macro_release.py` | `xauusd_forecaster/news/collection/macro_release.py` | Macro release evidence owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_pruning.py` | `xauusd_forecaster/news/collection/pruning.py` | News maintenance owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_semantics.py` | `xauusd_forecaster/news/semantics/contracts.py` | News semantic contracts owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_time.py` | `xauusd_forecaster/news/semantics/time.py` | News event-time qualification owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_relevance.py` | `xauusd_forecaster/news/semantics/relevance.py` | News relevance intake owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_contracts.py` | `xauusd_forecaster/news/semantics/model_contracts.py` | News model contract owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/semantic_transition.py` | `xauusd_forecaster/news/semantics/transitions.py` | Semantic transition owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/critical_annotation_state.py` | `xauusd_forecaster/news/semantics/critical_state.py` | Critical annotation state owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_input_coverage.py` | `xauusd_forecaster/news/semantics/input_coverage.py` | News input coverage owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_features_v2.py` | `xauusd_forecaster/news/semantics/features.py` | News feature evidence owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_evidence.py` | `xauusd_forecaster/news/semantics/evidence.py` | News event evidence owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_qa.py` | `xauusd_forecaster/news/semantics/qa.py` | News QA owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_contract_migration.py` | `xauusd_forecaster/news/semantics/migration.py` | News contract migration owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/annotation.py` | `xauusd_forecaster/news/annotation/product.py` | Annotation product owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_impact.py` | `xauusd_forecaster/news/annotation/impact.py` | Impact product owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/storylines.py` | `xauusd_forecaster/news/annotation/storylines.py` | Storyline projection owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_scheduler.py` | `xauusd_forecaster/news/scheduler/state.py` | Durable scheduler state owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_scheduler_runtime.py` | `xauusd_forecaster/news/scheduler/runtime.py` | Scheduler execution runtime owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/ai_task_registry.py` | `xauusd_forecaster/news/scheduler/task_registry.py` | Scheduler task registry owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/scheduler_model_gateway.py` | `xauusd_forecaster/news/scheduler/model_gateway.py` | Scheduler model routing owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_pipeline_health.py` | `xauusd_forecaster/news/scheduler/health.py` | News pipeline health owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_retrieval.py` | `xauusd_forecaster/news/retrieval/search.py` | News identity retrieval owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_identity.py` | `xauusd_forecaster/news/retrieval/identity.py` | News source identity owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_event_identity.py` | `xauusd_forecaster/news/retrieval/event_identity.py` | Canonical event identity owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/gemini_embeddings.py` | `xauusd_forecaster/news/retrieval/gemini_embeddings.py` | Gemini Embedding 2 retrieval owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/local_embeddings.py` | `xauusd_forecaster/news/retrieval/local_embeddings.py` | Offline retrieval test embedding owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/news_retrieval_benchmark.py` | `xauusd_forecaster/news/retrieval/benchmark.py` | Retrieval benchmark owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/named_reference_benchmark.py` | `xauusd_forecaster/news/retrieval/named_reference_benchmark.py` | Named-reference benchmark owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/daily_brief.py` | `xauusd_forecaster/news/brief/product.py` | Daily Brief lifecycle owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/daily_brief_runtime.py` | `xauusd_forecaster/news/brief/runtime.py` | Daily Brief runtime owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/ai_provider_registry.py` | `xauusd_forecaster/ai/provider_registry.py` | AI provider registry owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/model_gateway.py` | `xauusd_forecaster/ai/model_gateway.py` | Provider-neutral model gateway owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/gemini_quota.py` | `xauusd_forecaster/ai/quota.py` | AI quota accounting owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/credential_identity.py` | `xauusd_forecaster/ai/credentials.py` | Credential identity owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/model_limits.py` | `xauusd_forecaster/ai/model_limits.py` | Model output limits owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_agent.py` | `xauusd_forecaster/assistant/agent.py` | Assistant agent contract owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_capacity.py` | `xauusd_forecaster/assistant/capacity.py` | Assistant capacity owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_chat_worker.py` | `xauusd_forecaster/assistant/chat_worker.py` | Retained Assistant chat execution contract owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_compaction.py` | `xauusd_forecaster/assistant/compaction.py` | Assistant compaction owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_content.py` | `xauusd_forecaster/assistant/content.py` | Assistant content owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_events.py` | `xauusd_forecaster/assistant/events.py` | Assistant event ledger owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_evidence.py` | `xauusd_forecaster/assistant/evidence.py` | Assistant evidence owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_memory_index.py` | `xauusd_forecaster/assistant/memory_index.py` | Assistant memory index owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_routing.py` | `xauusd_forecaster/assistant/routing.py` | Assistant routing owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_titles.py` | `xauusd_forecaster/assistant/titles.py` | Assistant titles owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/assistant_tools.py` | `xauusd_forecaster/assistant/tools.py` | Assistant tools owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/runtime_health.py` | `xauusd_forecaster/runtime/health.py` | Runtime heartbeat owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/operational_health.py` | `xauusd_forecaster/runtime/operational_health.py` | Operational health owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/operational_taxonomy.py` | `xauusd_forecaster/runtime/taxonomy.py` | Operational taxonomy owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/production_shape.py` | `xauusd_forecaster/runtime/production_shape.py` | Production-shape validation owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/dashboard_payloads.py` | `xauusd_forecaster/dashboard/payloads.py` | Dashboard payload owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/dashboard_read_models.py` | `xauusd_forecaster/dashboard/read_models.py` | Dashboard read model owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/dashboard_summaries.py` | `xauusd_forecaster/dashboard/summaries.py` | Dashboard summary owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/learning_curves.py` | `xauusd_forecaster/dashboard/learning_curves.py` | Dashboard learning curve owner | THIN_SHIM | Remove after external callers migrate from the legacy module |
| `xauusd_forecaster/collector_runtime.py` | `xauusd_forecaster/decision/collector_runtime.py` | Collector decision runtime owner | THIN_SHIM | Remove after external callers migrate from the legacy module |

Future Phase D rows must name every retained flat facade. `THIN_SHIM` means the
legacy Python file contains only a docstring, explicit canonical imports,
`__all__`, and a documented alias when necessary. Canonical package code may
not import a path marked `THIN_SHIM`.
