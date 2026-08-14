# Documentation Taxonomy Inventory

This point-in-time audit records the documentation surfaces reviewed before the
taxonomy migration. `Normative` means the document defines a current contract,
specification, or applicable protocol. `Historical` means it preserves an
inspection or completed-work record.

## Markdown inventory

| Original path and title | Primary purpose | Authority | Destination | Action and inbound repository references |
| --- | --- | --- | --- | --- |
| `README.md` — XAUUSD Forecaster | Repository introduction | Summary | unchanged | Update links; no authoritative rules added. |
| `AGENTS.md` — Repository Working Rules | Contributor policy | Normative | unchanged | Add concise taxonomy rule; referenced by repository agents. |
| `web/README.md` — Aurum Signal Room | Web workspace guide | Reference/runbook summary | unchanged | Keep near the workspace; no moved-doc references. |
| `ctrader/XauusdForwardQuoteBridge/README.md` — XAUUSD Forward Quote Bridge | Adapter guide and interface summary | Reference | unchanged | Keep near the adapter; no moved-doc references. |
| `docs/PRODUCT_CONTRACT.md` — Frozen Product Contract | Product behavior and evidence gates | Normative spec | `specs/PRODUCT.md` | Reclassify; referenced by root README and former system document. |
| `docs/SYSTEM_CONTRACT.md` — XAUUSD Forecasting System Contract | Durable system, ledger, data, validation, and safety boundaries | Normative contract | `contracts/SYSTEM_BOUNDARIES.md` | Keep as contract with product behavior linked to its spec; referenced by root README and U5 audit. |
| `docs/FORWARD_ONLY_CONTRACT.md` — Phase 2F Forward-only Evidence Contract | Active causality, evidence, generation, and evaluation invariants | Normative contract | `contracts/FORWARD_ONLY.md` | Keep as contract; referenced by root README. |
| `docs/REPLAY_CONTRACT.md` — Frozen Phase 2A Replay Contract | Versioned deterministic historical replay boundary | Normative contract | `contracts/REPLAY.md` | Keep as contract; referenced by U5 audit. |
| `docs/NEWS_EVENT_EVIDENCE_CONTRACT.md` — News Event Evidence Contract | Point-in-time news identity, eligibility, weighting, and generation boundaries | Normative contract | `contracts/NEWS_EVIDENCE.md` | Keep as contract; no previous inbound Markdown reference. |
| `docs/REPAIR_AND_EVIDENCE_LANES.md` — Repair and Evidence Lanes | Immutable lane and append-only migration boundaries | Normative contract | `contracts/EVIDENCE_LANES.md` | Reclassify explicitly as contract; no previous inbound reference. |
| `docs/FACTOR_COVERAGE_CONTRACT.md` — XAUUSD Factor Coverage Contract | Required factor states and presentation/eligibility behavior | Normative spec | `specs/FACTOR_COVERAGE.md` | Reclassify; no previous inbound reference. |
| `docs/LEARNING_CURVE_CONTRACT.md` — Live OOS Learning Curve Contract | Learning lifecycle, curve selection, and display semantics | Normative spec | `specs/LEARNING_CURVES.md` | Reclassify and link hard invariants to Forward-only; no previous inbound reference. |
| `docs/STORYLINE_RESEARCH_CONTRACT.md` — Storyline Research Contract | Display behavior plus research promotion procedure | Mixed normative | `specs/STORYLINES.md` and `protocols/STORYLINE_PROMOTION.md` | Split spec from protocol; hard evidence rules link to existing contracts; no previous inbound reference. |
| `docs/EXECUTION_MODEL_RESEARCH_CONTRACT.md` — Execution Model Research Contract | Candidate definitions and ordered evaluation gates | Normative protocol | `protocols/EXECUTION_MODEL_RESEARCH.md` | Reclassify and link runtime invariants to Forward-only; no previous inbound reference. |
| `docs/TEMPORAL_EVENT_GRAPH_V5_CONTRACT.md` — Temporal Event Graph V5 Contract | Display-only graph states and acceptance behavior | Normative spec | `specs/TEMPORAL_EVENT_GRAPH.md` | Reclassify; no previous inbound reference. |
| `docs/CLOUDFLARE_HOSTING.md` — Cloudflare Hosting Contract | Architecture, security boundary, failure semantics, and deployment commands | Mixed | `design/CLOUDFLARE_HOSTING.md`, `contracts/HOSTING_BOUNDARIES.md`, and `runbooks/CLOUDFLARE_DEPLOYMENT.md` | Split by authority; referenced by root README. |
| `docs/INPUTS_REQUIRED.md` — Confirmed Phase 2F Inputs | Confirmed input/provider catalog and configuration facts | Reference | `reference/INPUTS.md` | Reclassify; no previous inbound reference. |
| `docs/FREE_DATA_PLAN.md` — Free Data Feasibility Plan | Historical Phase 2F source assessment | Historical report | `reports/FREE_DATA_FEASIBILITY.md` | Mark status as historical because current provider facts moved on; no previous inbound reference. |
| `docs/PHASE2F_REPAIR_REPORT.md` — Phase 2F Repair Report | Completed migration receipt and result | Historical report | `reports/PHASE2F_REPAIR.md` | Move only; no previous inbound reference. |
| `docs/TEST_SUITE_AUDIT.md` — Test Suite Audit | Point-in-time test inspection | Historical audit | `audits/TEST_SUITE.md` | Move only; no previous inbound reference. |
| `docs/U5_AUTHORITY_AUDIT.md` — U5 Authority Audit | Point-in-time authority and repair findings | Historical audit | `audits/U5_AUTHORITY.md` | Move and update contract links; no previous inbound reference. |
| `docs/contracts/PREVIEW_ISOLATION.md` — Preview Isolation Contract | Non-negotiable Preview isolation and provenance | Normative contract | unchanged | Already correctly classified; referenced by AGENTS and Preview spec. |
| `docs/specs/PREVIEW_BEHAVIOR.md` — Preview Behavior Specification | Preview modes, states, refresh, and fallback behavior | Normative spec | unchanged | Already correctly classified; referenced by AGENTS and Preview contract. |
| `docs/plans/PR33_AI_PRIORITY_KEY_SCHEDULER.md` — PR 33: AI Priority And Key Scheduling | Implemented scheduler architecture | Design | `design/AI_PRIORITY_SCHEDULER.md` | Remove completed-plan status as current authority; no previous inbound reference. |
| `docs/plans/PR34_CANONICAL_EVENT_HANDOVER.md` — PR 34: Canonical Events And Model Handover | Completed handover scope and result | Historical report | `reports/CANONICAL_EVENT_HANDOVER.md` | Reclassify; no previous inbound reference. |
| `docs/plans/PR35_NEWS_METRICS_SOURCE_OF_TRUTH.md` — PR 35: News Metrics Source Of Truth | Completed metrics migration | Historical report | `reports/NEWS_METRICS_MIGRATION.md` | Reclassify; no previous inbound reference. |
| `docs/plans/PR37_PAGED_DASHBOARD_HISTORY.md` — PR 37: Paginate Growing Dashboard History | Implemented architecture and migration baseline | Design | `design/PAGED_DASHBOARD_HISTORY.md` | Reclassify and label measurements as migration baseline; no previous inbound reference. |
| `docs/plans/PR68_CORE_BROAD_NEWS_HANDOVER.md` — PR 68: Core And Broad News Handover | Completed handover rationale and acceptance result | Historical report | `reports/CORE_BROAD_NEWS_HANDOVER.md` | Reclassify; no previous inbound reference. |

## Machine-readable and implementation terminology

| Path | Classification | Decision |
| --- | --- | --- |
| `web/preview-manifest.json` | Build manifest | Keep; the name accurately describes configuration rather than authority. |
| `xauusd_forecaster/news_annotation.schema.json` | Compatibility schema | Keep; schema is the correct machine-readable type. |
| `config/forward.example.json` | Example configuration | Keep; it is not presented as a contract or spec. |
| `web/app/_lib/news-index-contract.ts` | Public payload-shape implementation | Keep; the module enforces a compatibility-sensitive interface. |
| `xauusd_forecaster/news_contracts.py` | Runtime contract values and compatibility identities | Keep; the name refers to executable invariant/version definitions, not documentation taxonomy. |
| `xauusd_forecaster/news_contract_migration.py` | Versioned contract migration | Keep; renaming would obscure its compatibility role. |
| `tests/test_news_semantic_contract_v15.py` | Semantic compatibility tests | Keep; tests enforce but do not author the documented rules. |
| `tests/test_registered_macro_collector_contracts.py` | Collector-family invariant tests | Keep; tests enforce but do not author the documented rules. |

No obsolete compatibility Markdown aliases are retained. Historical GitHub links
may continue to point at old commits, while current repository links use the new
authoritative locations.
