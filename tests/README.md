# Test ownership

Tests protect contracts, not the historical PR stack or a directory move. The
current main uses one authoritative inventory in
`.github/python-test-shards.json`; `test_ci_contracts.py` proves every Python test
file is assigned exactly once. Shards preserve the existing five-minute CI and
30-second per-test budgets. A package move does not justify dropping a test.

Test files are physically grouped into `decision/`, `evidence/`, `training/`,
`news/`, `ai/`, `assistant/`, `dashboard/`, `runtime/` and `architecture/`.
Shared fixture helpers and immutable vectors live in `fixtures/`; `conftest.py`
stays at the root so every domain inherits the same repository import root.
Directory ownership and CI sharding are separate: shards balance bounded runtime,
while directories explain what a test protects. Recursive inventory checking
proves that nested tests are collected and assigned exactly once.

This implements #301's physical grouping and complete-collection intent.
Focused contract files keep their test names;
fixtures patch the actual package owner. Process tests still invoke production
entry points, including HTTP routing, thread lifecycle, restart and failure.

| Owner | Principal contract families |
| --- | --- |
| Decision / Evidence | `test_decision.py`, `test_forward_only.py`, `test_evidence_integrity_v2.py`, `test_ledger.py`, `test_clock_atomicity.py`, `test_clock_recovery.py` |
| Training | `test_training_owner.py`, materialization and generation cases in `test_evidence_integrity_v2.py` |
| News | `test_news_scheduler.py`, `test_scheduler_transition_execution.py`, `test_daily_brief.py`, `test_critical_annotation_state.py`, retrieval/collection/semantic contract files |
| AI / Assistant | `test_model_gateway.py`, quota/provider tests, and `test_assistant_*.py`; no activation |
| Dashboard | `test_dashboard_*.py`, `dashboard_news_fixtures.py`, and Web store/route/consumer suites |
| Runtime | `test_main_runtime.py`, `test_runtime_*.py`, `test_operational_*.py`, `test_wal_checkpoint_ownership.py` |
| Architecture / validation | `test_architecture_*.py`, `test_ci_contracts.py`, bounded Web and dependency contracts |

`test_package_ownership.py` executes fresh-process canonical imports, package
resource loading, repeated schema initialization and real entrypoint argument
parsing from an unrelated working directory. Existing behavior suites establish
the transaction, lifecycle, provider and serialized-contract properties beyond
import success. Historical mutation outcomes remain evidence for their original
source; current evidence must identify its own source and actual test execution.
