# Test Organization Audit — 2026-08-24

## Scope and architecture gate

This audit covers Phase E of the pending modularization campaign. The change
organizes existing tests and updates their invocation paths; it does not change
runtime behavior, data flow, state, APIs, storage, scheduling, deployment, or
production configuration.

```text
Owner: Repository test and validation contracts
Authoritative state/store: Git-tracked test sources and runner configuration
Execution boundary: pytest, Node test runner, and Windows CI invocations
Critical or optional: Critical validation path; no production runtime path
Maximum work per operation: Existing complete test suites and explicit runner lists
Incremental cursor/revision/checkpoint: Git revision and pytest collection inventory
Failure domain: Test discovery or validation invocation only
Last-good/recovery behavior: Revert the test-only move and runner-path update
Architecture documents affected: Codebase and subsystem maps plus this audit
```

## Collection reconciliation

- Latest `main` before the campaign: 1,569 collected Python cases.
- Original pre-rebase campaign closure: 1,512 collected Python cases.
- Reconstructed Phase E head: 1,601 collected Python cases.
- Path normalization leaves nine renamed or split current-main node IDs. The
  replacement table below accounts for all nine; unexplained removals: zero.
- Intentional duplicate-test removals: none.
- The reconstructed head preserves every normalized logical case from current
  `main` and adds the 32 campaign cases; no current-main case was removed.
- Windows-routed contracts remain explicitly listed in
  `.github/workflows/windows-runtime-gates.yml`; platform-neutral CI excludes
  only the relocated Control Center contract module.

## Python ownership moves

| Previous path | Owner-oriented path | Classification |
|---|---|---|
| `tests/test_forward_only.py` | `tests/integration/test_forward_only.py` | Cross-owner forward-only invariant |
| `tests/test_evidence_integrity_v2.py` | `tests/evidence/test_integrity_v2.py` | Evidence integrity contract |
| `tests/test_runtime_launchers.py` | `tests/runtime/test_control_center_contracts.py` | Integrated Control Center runtime contract |
| `tests/test_operational_health.py` | `tests/runtime/test_operational_health.py` | Runtime health projection |
| `tests/test_runtime_health.py` | `tests/runtime/test_runtime_health.py` | Runtime health contract |
| `tests/test_release_validation_fixtures.py` | `tests/runtime/test_release_validation_fixtures.py` | Candidate validation fixture contract |
| `tests/test_production_shape.py` | `tests/runtime/test_production_shape.py` | Production-shape contract |
| `tests/test_control_plane_install.py` | `tests/runtime/test_control_plane_install.py` | Control Plane installation contract |
| `tests/test_news_scheduler.py` | `tests/news/test_scheduler.py` | News scheduler owner |
| `tests/test_daily_brief.py` | `tests/news/test_daily_brief.py` | Daily Brief owner |
| `tests/test_storylines.py` | `tests/news/test_storylines.py` | News storyline owner |
| `tests/test_scheduler_transition_execution.py` | `tests/news/test_scheduler_transition_execution.py` | Scheduler transition contract |
| `tests/test_critical_annotation_state.py` | `tests/news/test_critical_annotation_state.py` | Critical annotation-state contract |
| `tests/test_dashboard_api.py` | `tests/dashboard/test_api.py` | Dashboard HTTP integration |
| `tests/test_dashboard_sync.py` | `tests/dashboard/test_sync.py` | Dashboard Sync integration |
| `tests/test_assistant_agent.py` | `tests/assistant/test_agent.py` | Retained Assistant agent contract |
| `tests/test_assistant_capacity.py` | `tests/assistant/test_capacity.py` | Retained Assistant capacity contract |
| `tests/test_assistant_chat_worker.py` | `tests/assistant/test_chat_worker.py` | Retained Assistant chat-worker contract |
| `tests/test_training_owner.py` | `tests/training/test_owner.py` | Training owner contract |
| `tests/test_decision.py` | `tests/decision/test_selection.py` | Decision selection contract |

The Control Center suite remains one explicit cross-runtime contract because
its action, service-key, runtime-control, release, and presentation invariants
span the same operator transaction. Splitting it would weaken the boundary it
protects.

## Current-main node replacement proof

The raw current-main-to-reconstructed comparison reports 76 removed node IDs
and 108 added node IDs. Sixty-seven removals are identical logical symbols: 45
from the Dashboard resource-owner split and 22 current-main #303/#305/#306 Control
Center contracts moved under the runtime owner. The remaining nine
are the following intentional stronger replacements:

| Current-main node ID | Replacement node ID | Reason and equivalent-or-stronger proof |
|---|---|---|
| `tests/test_dashboard_api.py::test_news_collector_uses_process_heartbeat_with_bounded_grace` | `tests/test_dashboard_health_projection.py::test_collector_component_heartbeat_boundaries[10-RUNNING-OK]` plus the five sibling boundary cases and `::test_collector_component_starting_grace_and_missing_heartbeat` | The former compound case is parameterized across the exact OK/WARN/STALE boundaries and separated from STARTING/missing-heartbeat assertions. |
| `tests/test_dashboard_api.py::test_news_collector_recovery_depends_on_heartbeat_not_old_poll` | `tests/test_dashboard_health_projection.py::test_collector_component_fresh_heartbeat_is_independent_of_old_poll` | Same recovery invariant, renamed to state the owner contract directly. |
| `tests/test_dashboard_api.py::test_decision_collector_still_fails_for_invalid_heartbeat[ERROR-1]` | `tests/test_dashboard_health_projection.py::test_decision_collector_preserves_invalid_heartbeat_failure[ERROR-1]` | Same parameter and fail-closed assertion under the extracted projection owner. |
| `tests/test_dashboard_api.py::test_decision_collector_still_fails_for_invalid_heartbeat[RUNNING-301]` | `tests/test_dashboard_health_projection.py::test_decision_collector_preserves_invalid_heartbeat_failure[RUNNING-301]` | Same parameter and fail-closed assertion under the extracted projection owner. |
| `tests/test_dashboard_api.py::test_decision_collector_still_fails_for_invalid_heartbeat[STOPPED-1]` | `tests/test_dashboard_health_projection.py::test_decision_collector_preserves_invalid_heartbeat_failure[STOPPED-1]` | Same parameter and fail-closed assertion under the extracted projection owner. |
| `tests/test_dashboard_api.py::test_decision_output_stall_honors_bounded_five_minute_cadence[420-CURRENT]` | `tests/test_dashboard_health_projection.py::test_decision_output_stall_honors_exact_boundary[420-CURRENT]` | Same boundary input and expected state; the replacement name makes the exact-boundary contract explicit. |
| `tests/test_dashboard_api.py::test_decision_output_stall_honors_bounded_five_minute_cadence[421-STALLED]` | `tests/test_dashboard_health_projection.py::test_decision_output_stall_honors_exact_boundary[421-STALLED]` | Same boundary input and expected state; the replacement name makes the exact-boundary contract explicit. |
| `tests/test_dashboard_api.py::test_decision_output_without_a_prior_row_uses_observation_start[420-NO_RECENT_DECISION]` | `tests/test_dashboard_health_projection.py::test_decision_output_without_prior_row_uses_observation_start[420-NO_RECENT_DECISION]` | Same input and output assertion; grammar-only rename after owner extraction. |
| `tests/test_dashboard_api.py::test_decision_output_without_a_prior_row_uses_observation_start[421-STALLED]` | `tests/test_dashboard_health_projection.py::test_decision_output_without_prior_row_uses_observation_start[421-STALLED]` | Same input and output assertion; grammar-only rename after owner extraction. |

After path normalization and these explicit replacements, every current-main
contract is represented. The 32-case net increase is campaign-owned coverage.

## Web and Worker organization

The Web runner in `web/package.json` names each test file explicitly and does
not discover subdirectories recursively. The Web and Worker tests therefore
remain in their current flat paths. Their feature-oriented filenames already
make the public status/resource, Dashboard, Preview, admin-auth, Worker/D1,
release, and responsive contracts visible without changing runner semantics.

## Validation evidence

- `python -m pytest --collect-only -q`: 1,569 on current `main`, 1,512 on the
  original closure, and 1,601 on the reconstructed head. Normalized inventory
  reconciliation found zero removals from current `main` and 32 campaign
  additions.
- Owner directories: 1,143 passed.
- Full Python suite: 1,601 passed.
- Windows-routed runtime families: 280 passed; the Control Center plus Control
  Plane subset contains 212 cases, including 14 focused installer cases.
- `npm test`: build completed; 249 passed and 6 skipped.
- `npm run lint`: passed.
- Architecture checker: passed.
- Architecture documentation/import and repository-policy tests: 18 passed.
- `python -m compileall -q xauusd_forecaster scripts`: passed.
- `git diff --check`: passed.

On Windows, the first Wrangler type check reported the generated declaration as
out of date solely because of line-ending normalization. Regeneration produced
zero content differences when ignoring end-of-line whitespace; the complete
Web suite then passed. The generated declaration is not part of this change,
and Linux exact-head CI remains the canonical clean-tree type-generation gate.
