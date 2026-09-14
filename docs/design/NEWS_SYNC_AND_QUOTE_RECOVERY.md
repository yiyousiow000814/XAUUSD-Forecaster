# News synchronization and interrupted quote recovery

## Change contract

Local quote ingestion, the optional local News export, authenticated Worker
ingest and the existing D1 current projection are affected. No trading rules,
historical evidence or production configuration change in this PR.

The quote writer owns append-only bytes. Restart terminates an unfinished line
before appending a new record. Readers defer an unfinished tail, reject malformed
complete records with a bounded diagnostic, and continue to valid observations.
Original bytes remain available. Wrong symbols, invalid prices and other schema
errors remain failures. Incomplete/corrupt input never becomes a fabricated quote.
The collector owns decision freshness; its startup pulse is not output progress.
Historical News qualification remains immutable and is labelled as historical
when its decision is stale, without replaying its old errors as current failures.

## Actors and state

The quote cBot, forced process supervisor, incremental/archive readers and
collector share the quote boundary. The source materializer owns a frozen complete
News generation. Dashboard Sync owns its target-specific acknowledged fingerprint
baseline. The authenticated Worker owns one atomic current D1 publication; public
readers observe only committed publications. Full replay and its staging TTL and
cleanup remain the bootstrap/recovery owner.

The optional sparse delta applies only to a complete acknowledged baseline and a
complete frozen target. It contains all changed index rows, changed details and
explicit removed keys. It is bounded by 32 index changes, eight detail changes,
32 removals and the existing 120 KB route envelope. Large changes and unknown
baselines use the existing full replay. No total Free-tier compliance claim is
derived from this fast path; full replay is recurring and must be budgeted.

The delta digest binds the baseline, complete source manifest and exact patch.
It is a distinct transport generation/receipt, not a claimed full-stream receipt.
Current index rows, counts, receipt and generation pointer change in one D1 batch.
A transaction-time baseline/staging guard prevents check/use races. Validation
failure rolls back the complete batch. Unchanged bodies and per-row transfer
receipts are not copied. Complete counters are rebuilt in one bounded 10,000-row
scan; the source remains complete, and unrelated rows are preserved.

## Compatibility and recovery matrix

| Condition | Behavior |
| --- | --- |
| New Worker, old producer | Full v4 replay remains valid |
| Old Worker, new producer | Capability absent: use full replay |
| New code, old sync state | Verify complete state, capture fingerprints |
| Sparse target, matching baseline | Atomic delta, acknowledge exact digest |
| Missing or mismatched baseline | Full replay; never infer deletions |
| Large patch | Existing bounded full replay |
| Lost ACK / process or machine restart | Reconstruct identical patch from pinned target and retry; accepted digest is idempotent |
| Concurrent publication or staging | Transaction guard rejects stale patch without partial writes |
| Empty target | Explicit complete membership/removals; never infer from failed export |
| Interrupted quote tail | Preserve bytes, separate next record; defer while incomplete |
| Damaged complete quote | Log source/offset/hash, omit invalid observation, continue valid records |

No new lock or daemon is needed. Additive migration 0038 extends the existing
index/detail deletion fences to the delta discriminator. The Worker advertises
delta support only after both triggers exist; direct delta writes also check
them. During the transaction only, the publication owner sets REPLAYING so
existing legacy-write fences allow the update; commit restores CURRENT and
failure rolls back to the previous CURRENT state. This intermediate state is
never externally committed. Delta metadata uses the
existing generation contract discriminator and existing current tables. Retain
at most the current and preceding generation metadata; existing full-replay
cleanup owns retired transfer receipts. The sync owner requires full replay
once the changed source is a day newer than its last full replay watermark,
so continuous sparse updates cannot bypass that retention owner indefinitely.
An unchanged source creates no growth and does not force a replay. Credentials remain on existing local
and Worker authentication boundaries. PR creation does not authorize production
activation. Main-only forward repair is the supported release path.

## Verification and pre-mortem

Test real Python reader/file append and gzip behavior, real C# file restart
framing, local authenticated API export, Python serializer to Worker consumer,
SQLite-backed D1 transaction rollback, lost ACK, stale baseline, full fallback,
empty membership, unchanged rows and counts/pagination/detail consumers. Replay
the captured 4,877-row production shapes locally and measure transmitted batches
and database statements. Provider analytics are advisory estimates, not exact
billing receipts. No production mutation is needed for this rehearsal.

Worst cases are a lost ACK followed by deleting the wrong baseline, validation
after a partial commit, stale generation pagination, and a malformed quote being
misreported as healthy data. Tests must exercise those boundaries, not only hash
helpers. Review final callers and consumers independently after implementation.
