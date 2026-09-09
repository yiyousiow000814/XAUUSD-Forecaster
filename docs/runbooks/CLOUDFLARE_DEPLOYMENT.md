# Cloudflare Deployment

The sole production application is `aurum-signal-room`. Workers Builds connects
to `yiyousiow000814/XAUUSD-Forecaster`, production branch `main`, root `/web`.
Non-production builds are disabled. Build: `npm ci && npm test`.
Deploy: `npx wrangler deploy --message "main:$WORKERS_CI_COMMIT_SHA"`.

Merge only after required checks and review. Check current production schema and
input compatibility before main activation. Native deployment serves one version
at 100 percent traffic. Verify deployed source identity, real business API
responses and strict sync ACK. A failed deployment is a failure to fix forward.
Do not restore an old database or fabricate health/ACK evidence.

On 2026-09-08 the settings above were saved and obsolete Workers `agents`,
`aurum-signal-room-preview` and inactive `aurum-live-broadcast` were deleted.
The dashboard inventory was refreshed and showed exactly one application.
Required production storage, secrets and Access were preserved.

Production migration 0031 was applied and verified on 2026-09-08. Exact counts
match all source resource/model groups, and learning-history lookups use the new
identity/time index. The account was already on Workers Paid (verified in the
Cloudflare Current plan display); no plan change was made. Retain efficient
bounded requests and measured headroom rather than assuming Free limits describe
the actual account. Migration, ongoing traffic and backlog remain distinct costs.

## Exact chart history activation

Apply additive migration `0034_exact_chart_history.sql` before activating the
new chart reader. Preserve old learning rows and all source SQLite facts.
Deploy the single main revision normally. The existing local optional learning
owner rebuilds its changed contract, then Dashboard Sync drains bounded exact
history pages. A 503 chart response during initial backfill means pending, not
an empty history. Do not manufacture the completion marker to bypass a failed
export or count mismatch.

Acceptance compares per-resource/identity counts and first/last timestamps with
the source-derived rows. Verify 24h, 7d, 30d, all history, navigation back to
older windows, both cadences and execution pages. Repeat on the branch Preview
and production desktop/phone. Record D1 read/write costs separately for initial
backfill and subsequent requests. Metrics summaries must contain no chart data,
and old curve/version overview GETs must no longer return misleading graphs.

## Lightweight chart reads

Apply additive index migration0035 before the pyramid reader. The existing
optional owner upgrades its derived format and syncs changed raw ordinals plus
extrema blocks through the unchanged acknowledged cursor. Wait for an actual
pyramid-v1 completion receipt, then verify the next incremental publication.
Do not replace the cursor, manufacture completion, or rewrite original facts.
Compare the same range/source identity with measured D1 reads and preserve the
PR502 baseline (core24h11171 rows; all607708 rows). Those baseline numbers are
not an account-wide reduction claim. Healthy graph extent and small-range
exactness remain required alongside lower read cost.
