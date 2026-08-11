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

## Planned Change

1. Define stable row identities and cursors for learning generations, model
   curves, and evaluation results.
2. Store growing learning records as idempotent D1 rows or bounded pages.
3. Keep `/api/learning` as a small summary and expose bounded cursor-based
   history reads.
4. Make the market-chart snapshot recent-only and retrieve older candles and
   decisions from the existing market-history API.
5. Load older pages only when the user requests a longer range or older group.
6. Backfill existing retained history, verify row counts and hashes, switch the
   complete generation together, then remove the superseded growing-blob path.

## Acceptance Criteria

- Every write request remains bounded independently of total history age.
- Every read response has a documented row and byte limit plus a continuation
  cursor.
- Current views render from the first page without downloading all history.
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
