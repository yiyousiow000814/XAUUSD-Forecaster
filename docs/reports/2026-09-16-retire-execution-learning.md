# Retire position sizing and early exit research

## Change contract

The user explicitly requests stopping and deleting both research families, including training, derived data and web presentation. This authorization is the narrowly scoped exception to preservation of their own append-only research records. Shared direction predictions, raw quotes, news, model artifacts and fixed 30-minute outcomes remain authoritative and unchanged.

Actors: the main-only supervisor stops all old services before updating; Collector creates predictions and examples; its single background owner trains; dashboard projects learning; synchronization publishes to Cloudflare. Remove every producer before retiring storage. New code never recreates research tables. Existing direction processing continues without them.

Transition: old running -> supervisor stops old writers -> new main starts without execution hooks -> explicit retirement cleanup -> direction-only publication. SQLite retirement is one transaction with child tables removed first, including append-only triggers attached to retired tables. Filesystem cleanup is limited to the verified execution-models-v1 and execution-models-v2 child directories of the runtime data root; paths and reparse points are checked before deletion. Retry is idempotent. A crash cannot require old execution code to recover. Forward repair is owned by main supervisor; deleted research results are intentionally not recoverable through code rollback.

Independent sender/Worker order: old payloads can contain execution data but new readers never render it; new payloads omit it and old readers may temporarily show empty state. Remote retired history records must be deleted and future ingestion rejected. Critical direction data is never deleted. Optional learning synchronization failure does not block collection; retry replaces the retired learning summary.

Verification: run actual Collector/decision/training boundaries, schema upgrade with seeded old tables and unrelated sentinel rows, repeat cleanup/reopen, learning transport and Worker readers, frontend build and responsive flow where browser access permits. Inspect live table counts, artifact paths and runtime identity before cleanup and verify absence after main activation. Do not claim production completion from unit tests alone.

## Final boundary review and validation

Reviewed Collector -> decision preparation/persistence -> background trainer ->
SQLite -> optional learning projection -> exact chart export -> Worker routes ->
reader. Removed obsolete executable algorithms and schemas. Shared direction
and outcome contracts are retained. Historical locator tooling now enumerates
only direction artifacts. Old chart-cache rows are retired by its own rebuild
transaction before exact-count validation. Snapshot ingestion strips the retired
field even from an old sender; history ingestion and readers reject its retired
resource names. The explicit D1 cleanup preserves count triggers.

Real execution matrix: Python CLI runs from an unrelated working directory with
an absolute runtime-state root; dry-run, apply, repeat and ledger reopen are
covered. It preserves sibling direction artifacts and tables and refuses linked
artifact roots before mutation. Real child Python processes exercise Collector
clock crash/restart and exact completion. The background training owner tests
exercise training and recovery without execution parameters. Worker tests run the
built route and SQLite migration, including repeated cleanup, surviving direction
rows/counts and rejection of retired reader requests.

Validation: 320 Python tests passed, one symlink test skipped because this Windows
host does not allow test symlink creation; seven chart-history tests passed.
Web build passed. Updated Worker/UI suites passed after rebuilding the release
validation fixture manifest. All Python tests collect successfully. Remote CI and
production activation/cleanup remain release gates.
