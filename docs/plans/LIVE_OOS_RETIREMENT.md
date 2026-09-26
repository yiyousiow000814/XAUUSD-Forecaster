# Live OOS retirement plan

Status: local implementation prepared; deployed Preview acceptance and production
activation/cleanup remain pending. No production data mutation has started.

## Requested boundary

Retire the Live OOS learning dashboard, its six forecast models, training and
prediction lifecycle, and exclusively owned data. Preserve current usable news
events, news collection, annotation, source evidence, event aggregation,
storylines, daily briefs, market collection, and their publication paths.
The user confirmed that decision/outcome history, predictions and scores are
also retired.

## Ownership and impact

- `scripts/runtime/run_forward_collector.py` starts news collection, training,
  decision generation, outcome settlement, backups, quote archival and WAL
  maintenance. Retirement must preserve the non-model owners and shutdown paths.
- News collection currently imports `ForwardEngine` merely to invoke
  `collect_official_news`; remove this dependency before removing inference.
- Training generation, materialization, inference and scoring share SQLite
  state with news. Never delete the whole evidence database or state directory.
- Dashboard status and news metrics consume learning statistics. The current
  usable-event filter and event evidence remain authoritative independently of
  training statistics and frozen model-use receipts.
- Local dashboard API, optional read-model builders, synchronization schedules,
  Worker learning/history routes, Preview fixtures and the audit view all expose
  model resources. Retirement must cover producers and consumers together.
- Local runtime and Cloudflare update independently. Old senders must not break
  retained resource ingestion; stopped model resources must not degrade health
  or create permanent retries.

## Safety, liveness and recovery

No production activation is part of local implementation. No data deletion has
been executed. Inventory exact table/file ownership and news dependencies before
preparing deletion. Any data cleanup needs a bounded, restartable operation with
an explicit retained-resource inventory and verification. Existing pause state
must remain effective until model lifecycle removal is deployed.

Preserve news identity, point-in-time source timing, filtering, deduplication,
annotation, event continuity, source links, and current event availability.
Collection and publication must progress without model artifacts or training
tables. Database contention, unavailable providers and restart must retain their
existing bounded retry and failure isolation.

## Verification plan

Exercise the collector and news owner with the real Python runtime, including
startup, one loop, shutdown and dependency failures. Check existing populated
state and a fresh database. Verify retained news events before and after cleanup
on an isolated database copy. Verify API/sync publication with retired resources
absent and independently updated components. Update affected Python and web
contract tests; run type/build checks. Independently trace the final diff through
actual callers, schemas, entrypoints, resource registries and consumers.

UI acceptance requires branch Preview checks at desktop, 390x844 and 360x800,
including retained news/event flows and every navigation-grid border. Close the
browser verification session and report its final count. Production activation,
physical production cleanup and any unavailable external checks must be reported
separately from local implementation evidence.

## Final boundary review and local evidence

The final review traced the supervised collector through news ownership and
SQLite, dashboard read models and source-first sync, Worker routing and D1,
Preview fixtures, navigation and live-status consumers. The retired routes are
absent from both route files and the production Worker router. Retained current
event selection and its API/reader source are unchanged.

Review findings repaired before final validation:

- Quotes formerly depended on model snapshots. Status now reads bounded quote
  JSONL directly and skips malformed, crossed, non-finite and incomplete rows.
- Collector startup exceptions could bypass closing the ledger. Its complete
  lifetime now closes in a finally block; retained background owners close on
  loop exit without creating model rows.
- The separate broadcaster still exposed model forecasts and decision deltas.
  Python, Worker and browser now use quote/health-only PUBLIC_LIVE_V2. Stored V1
  state is never sent to a new subscriber; a monotonic V2 publish replaces it
  with a full state. Interrupted independent upgrades use existing HTTP fallback.
- Tests for deleted requirements were removed; retained family tests exercise
  real API routes, candle-only synchronization, news pagination and events.

Cleanup rehearsals use real SQLite schema and semantic-event reads before and
after deletion, repeated execution, reopen, dashboard composition and retained
foreign-key rejection with rollback. Worker/D1 fixtures execute idempotent SQL
and assert retained news and candle data. These fixtures are local evidence;
they do not establish provider availability or production deletion throughput.
The production database read-only preflight observed 17,416,060,928 bytes,
40 present retired tables, and no retained foreign-key dependency. Historical
row inventory used materialized counts rather than scanning the full database.

Final Python regression: 1,770 passed and 7 skipped in 294.29 seconds.
Local acceptance includes the web production build (10 prerendered assets),
392 passing web tests with 6 skips, and 11 broadcast Worker tests plus 11 Python
publisher tests. Cloudflare type generation, generated architecture checks,
import boundaries and documentation links are checked separately. The exact
working tree remains uncommitted on retire-live-oos; source-index digest is
`e0e359ec15e84869a356f2324058748d8404ffb8b75842c89e3b3b00e7072ddb`.

## Remaining external acceptance

No PR, branch Preview, merge, deployment, publisher activation, or production
cleanup was performed. Responsive Preview checks at desktop, 390x844 and
360x800 remain pending. No browser sessions were opened (task session count 0).
The isolated broadcast receiver must precede publisher activation; verify a
fresh V2 payload replaces the old Durable Object state. Runtime and Worker
identities must be verified on main before the cleanup runbook is applied.
Old writers continuing after cleanup would repopulate retired data, so release
and stopped-writer checks are required before physical production deletion.


## PR verification follow-up

PR555 Preview review found that the new overview event count used optional event
fields from compact status, where zero is not a complete event count. The
overview now links to the independently owned current-events page without
claiming its count. Rendered coverage guards this behavior.
The clean Linux checkout also exposed an obsolete package-ownership test that
still imported removed decision/training packages; local ignored bytecode
directories had made those imports appear as namespace packages. The owner
inventory now tests only retained runtime packages.
