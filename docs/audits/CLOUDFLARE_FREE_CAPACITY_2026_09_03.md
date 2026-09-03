# Cloudflare Free Capacity Audit — 2026-09-03

## Scope and authorities

This point-in-time audit covers the production D1 database and the exact
`77395c2173e42d3f908f80a2977dd938f04ef187` implementation before PR A. It uses
read-only Cloudflare Analytics/D1 queries and local source inspection. Local
SQLite remains the complete forecasting, News, and learning authority; D1 is a
bounded public projection.

Official limits were re-read on 2026-09-03 from Cloudflare's
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
[D1 limits](https://developers.cloudflare.com/d1/platform/limits/),
[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), and
[D1 enforcement changelog](https://developers.cloudflare.com/changelog/product/d1/).
The applicable Free limits are 100,000 Worker requests/day, 10 ms CPU/request,
50 subrequests/request, 3 MB compressed Worker size, 64 bindings/environment
variables, 20,000 static assets/version, 500 MB/database, 5 GB/account, 50 D1
queries/invocation, 5 million rows read/day, and 100,000 rows written/day.

The reproducible machine-readable observations and formulas are in
`CLOUDFLARE_FREE_CAPACITY_2026_09_03.json`.

## Observed incident

The rolling 24-hour observation contained about 7.94 million D1 rows read and
143.5 thousand rows written. The largest normalized read query was the
learning-history model-identity page/count query: 5,907,926 rows across 196
executions (74.44%). Repeated schema capability discovery contributed 954,000
rows (12.02%), and another first-page learning-history shape contributed
366,824 rows (4.62%). These three families attribute 91.08% of observed reads.

The operator retry mirror contributed 95,049 writes (66.22%) because each
changed local snapshot replaced approximately 200 D1 rows. Learning records
contributed 15,126, market decisions 9,711, News-evidence cleanup 8,200,
status snapshots 5,230, projection receipts 4,334, and other bounded dashboard
families 2,761. These families attribute more than 97% of writes. Projection
receipt writes are release/bootstrap work, not recurring steady state.

## Storage and retention

The primary D1 database was 364,400,640 bytes; all account D1 databases totaled
about 372.4 MB. `news_evidence_records` held 52,969 rows and about 107.3 MB of
payload. Only 1,358 rows (about 2.74 MB) belonged to the active snapshot; 51,611
rows (about 104.56 MB) were inactive cleanup debt across 41 snapshots. Public
behavior needs the active bounded audit window, while complete historical
evidence already remains in authoritative local SQLite.

The apparent earlier 21.39 MB/day growth was generation/cleanup transition
debt, not retained steady-state growth. Provider database size moved only about
0.54 MB from September 1 to 2 and 0.045 MB from September 2 to the September 3
sample. `news_details`/`news_index`, current plus one superseded News projection,
learning records, and market decisions explain the remaining material payload.
No production row was deleted or updated during this audit.

## Candidate bounded model

PR A replaces the accumulated learning scan with an identity/time index,
bounded `limit + 1` page work, and exact materialized counts. Successful D1
capability observations are cached per isolate and failed observations remain
retryable. Operator retry mirroring becomes a one-row delta with a local
durable source digest; unchanged snapshots issue no mirror request and cause
zero D1 mutation. Snapshot writers update only when canonical payload bytes
change. News-evidence cleanup is limited to one 1,280-row reservation plus its
single budget-ledger write per UTC day.

The conservative recurring model is 38,640 Worker requests/day, 1,892,632 D1
rows read/day, and 46,881 D1 rows written/day. It includes a 30,000-request
public traffic envelope, the exact 30-second heartbeat/control cadence, all
2,880 daily heavy-lane slots, per-family write amplification, and the complete
daily cleanup reservation. It is not derived by requiring the old Stable to
have already become compliant.

The additive migration reads at most 52,273 learning rows and writes at most
52,320 index/count records in one UTC day. Cloudflare counts index creation and
index maintenance as rows written, so the hard migration-day gate checks the
sum rather than treating migration and recurring work independently: 1,944,905
rows read and 99,201 rows written. With a conservative 8.6 MB migration
allowance, pre-cutover size remains below 373 MB. Draining the observed inactive
News-evidence debt at the bounded daily rate yields a derived steady-state below
278 MB. The conservative 30-day hard-limit projection gives cleanup reclamation
zero credit and rounds up `364,400,640 + 8,600,000 + 30 * 1,000,000` to 404 MB.
Local SQLite is not
deleted, truncated, rewritten, or downgraded.

## Decision gate

The implementation is designed for `D1_ONLY`: all recurring and transition
bounds fit the project headroom, retained public data has a bounded owner, no
public requirement needs the complete cold evidence archive, and the local
authority remains intact. This is provisional until the exact merged PR A
Candidate proof and additive migration measurements reconcile these bounds.
R2 is not justified by the current product requirement or capacity model.
