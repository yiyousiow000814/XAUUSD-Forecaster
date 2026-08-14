# PR 37: Paginate Growing Dashboard History

## Problem

The local dashboard currently produces about 7.8 MB of JSON. The mirror keeps
individual uploads below 750 KB through adaptive compaction, but learning and
market history continue to grow. Compaction can delay the limit; it cannot make
an append-only history permanently bounded.

Current measured mirror payloads:

- live status: 203 KB
- learning: 747 KB
- market chart: 688 KB

The Worker 800 KB check must remain an emergency guard, not the storage model.

## Source Of Truth

- Keep the live status snapshot as the small current-state authority.
- Reuse the existing D1 market-history ledger and bounded-range API instead of
  creating another market-history representation.
- Preserve the local SQLite learning ledger as the complete research authority.
- Add one paged D1 representation for remote learning history; do not keep a
  second growing learning blob after the handover is verified.

## Implemented Design

1. `learning_records` stores model versions, training generations, OOS curve
   points, and execution evaluation rows under stable composite keys.
2. `/api/learning-history` accepts requests up to 350 KB and uses D1 JSON1 for
   validation and set-based idempotent upserts without parsing the body in the
   Worker.
3. History reads use opaque cursors, at most 500 rows, and a 400 KB SQL byte
   budget. D1 emits the response JSON directly, so the Worker does not parse
   and re-serialize growing pages. If either limit is reached, the next cursor
   continues from the last returned record.
4. The local synchronizer materializes fixed-size visual summaries before
   upload. Learning curves preserve each bucket's first, low, high, and last
   points plus real source gaps; market candles preserve OHLC; decision
   summaries preserve the time span and Long/Short changes. Visitor requests
   read these summaries directly and never scan or rank the growing raw D1
   tables.
5. `/api/learning` is a fixed first page: six generations per model, 48 curve
   points per cadence, and 20 execution results per model.
6. The existing market-history D1 ledger remains the only complete remote
   market authority. The market-chart snapshot now keeps 576 recent candles
   and a bounded recent decision window instead of retaining all half-hour
   decisions forever.
7. The UI requests older training pages and curve overviews only after the
   interactive graph opens. Branch Previews use the same API contract over an
   immutable build snapshot.
8. Every asynchronous graph surface distinguishes loading, confirmed empty,
   and failed states. A visible loading animation remains for at least 500 ms,
   and failures provide an explicit retry.
9. Dashed OOS segments come only from a source-gap flag computed before
   downsampling. Missing metadata fails to a solid line instead of guessing a
   gap from the wider spacing between compressed points.
10. Graph history uses the shared browser resource cache. Reopening a graph
    shows its last successful result immediately; live data refreshes in the
   background after 60 seconds, while immutable build-snapshot data is reused
   for the page lifetime.

## Measured Current Data

The real local status on 2026-08-11 produced:

- 7,955 normalized learning records
- 11 initial history batches
- 293 KB maximum history request
- 192 KB learning first page, down from about 730 KB
- 576 KB recent market snapshot, with complete history still in D1

After the initial backfill, the synchronizer uploads only new or changed record
hashes. A daily idempotent refresh repairs missing remote rows without creating
duplicates.

## Acceptance Criteria

- Every write request remains bounded independently of total history age.
- Every read response has a documented row and byte limit plus a continuation
  cursor.
- Current views render from the first page without downloading all history.
- All-history charts have fixed-size query and render work as raw history grows.
- All historical learning groups and market decisions remain reachable.
- Repeating a sync is idempotent and does not duplicate rows.
- Invalid or partial pages never replace previously verified history.
- Desktop, 390x844, and 360x800 Preview flows can open, paginate, return, and
  close without clipped or unreachable controls.
- Tests prove the new paged generation is active and no obsolete runtime blob
  writer remains.

## Out Of Scope

- Changing model training, prediction semantics, or promotion rules.
- Moving public history to R2 before D1 pagination is measured and insufficient.
- Combining this migration with the PR 36 Worker CPU fix.
